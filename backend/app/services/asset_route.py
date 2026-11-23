"""Маршрут согласования заявок на имущество (docs/TZ.md, раздел 28, «Детали 21.1»).

Шаги по порядку:
- руководители ЦФО снизу вверх: ЦФО заявки, его вышестоящий и так до верха. Если заявку подал руководитель ЦФО
  этой ветки — начиная с ЦФО над его собственным. ЦФО без руководителя пропускается;
- отметка о бюджете (сама, по плану ДДС; финансовый директор может поменять вручную с комментарием);
- сверх бюджета — CEO (если он есть в организации);
- финансовый директор — последним: принимает решение о закупке (поставщик, цена, вариант оплаты).
Администратор может действовать на любом шаге; финансовый директор может отклонить или вернуть на доработку на любом.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.core.permissions import Perm, Role
from app.models import Cfo, Membership, Operation, Posting, User
from app.models.asset import BUDGET_MARKS, AssetRequest
from app.services import reports
from app.services.articles import system_article

ZERO = Decimal("0.00")
STEP_TITLES = {"ceo": "CEO", "fd": "Финансовый директор"}
# заявки, которые уже занимают бюджет ЦФО (поданные и дальше, кроме отклонённых и отменённых)
TAKING_BUDGET = ("pending", "approved", "ordered", "shipped", "delivered", "received")


class RouteError(ValueError):
    pass


def current_step(req: AssetRequest) -> str | None:
    if req.status != "pending" or req.step >= len(req.route or []):
        return None
    return req.route[req.step]


def cfo_of(step: str) -> int | None:
    return int(step[4:]) if step.startswith("cfo:") else None


def _cfo_count(route: list[str]) -> int:
    return sum(1 for s in route if s.startswith("cfo:"))


def _path(db: Session, cfo_id: int) -> list[int]:
    """ЦФО и его вышестоящие — снизу вверх."""
    cfo = db.get(Cfo, cfo_id)
    return [int(i) for i in str(cfo.path).split(".")][::-1] if cfo else []


def _members(db: Session, organization_id: int, role: Role):
    return list(db.scalars(select(Membership).where(Membership.organization_id == organization_id,
                                                    Membership.role == role.value)))


def cfo_steps(db: Session, req: AssetRequest, author: User | None) -> list[str]:
    path = _path(db, req.cfo_id)
    if author is not None and not author.is_admin:
        own = db.scalar(select(Membership).where(Membership.user_id == author.id,
                                                 Membership.organization_id == req.organization_id))
        if own is not None and own.role == Role.CFO_HEAD and own.cfo_id in path:
            path = path[path.index(own.cfo_id) + 1:]  # себя не согласует: сразу вышестоящий
    heads = {m.cfo_id for m in _members(db, req.organization_id, Role.CFO_HEAD)}
    return [f"cfo:{c}" for c in path if c in heads]


def _tail(db: Session, req: AssetRequest) -> list[str]:
    if req.budget_mark == "over" and _members(db, req.organization_id, Role.CEO):
        return ["ceo", "fd"]
    return ["fd"]


def apply_tail(db: Session, req: AssetRequest) -> None:
    """Шаги после цепочки ЦФО — по отметке о бюджете. Если заявка уже за цепочкой ЦФО — снова с первого из них."""
    count = _cfo_count(req.route or [])
    req.route = list(req.route or [])[:count] + _tail(db, req)
    if req.step > count:
        req.step = count


# --- бюджет ---


def amount(req: AssetRequest) -> Decimal | None:
    """Сколько заявка занимает бюджета: стоимость решения ФД, до решения — ориентир цены × количество."""
    from app.services.asset_requests import total

    decided = total(req)
    if decided is not None:
        return decided
    return round_money(req.estimated_price * req.quantity) if req.estimated_price is not None else None


def _branch(db: Session, cfo_id: int) -> set[int]:
    root = select(Cfo.path).where(Cfo.id == cfo_id).scalar_subquery()
    return set(db.scalars(select(Cfo.id).where(Cfo.path.op("<@")(root))))


@dataclass
class Budget:
    cfo_id: int | None  # ЦФО, в бюджете которого заявка (ближайший снизу вверх, где запланирована покупка)
    plan: Decimal | None  # план ДДС ветки на месяц по статье «Покупка основных средств»; None — плана нет
    used: Decimal  # другие заявки на имущество ветки в этом месяце
    amount: Decimal

    @property
    def remaining(self) -> Decimal | None:
        return None if self.plan is None else self.plan - self.used

    @property
    def mark(self) -> str:
        return "over" if self.remaining is None or self.amount > self.remaining else "within"


def budget(db: Session, req: AssetRequest) -> Budget:
    org = req.organization_id
    need = amount(req) or ZERO
    month = req.needed_on.replace(day=1)
    key = f"{month:%Y-%m}"
    version = reports.actual_plan_versions(db, org, [key], "cf").get(key)
    article = system_article(db, org, "asset_purchase")
    found, plan = req.cfo_id, None
    if version is not None and article is not None:
        for cfo_id in _path(db, req.cfo_id):
            rows = db.execute(select(func.count(Posting.id), func.coalesce(func.sum(Posting.amount), 0))
                              .join(Operation, Operation.id == Posting.operation_id)
                              .where(Operation.budget_version_id == version, Operation.plan_kind == "cf",
                                     Operation.article_id == article.id, Operation.op_date == month,
                                     Operation.plan_cfo_id.in_(_branch(db, cfo_id)))).one()
            if rows[0]:
                found, plan = cfo_id, Decimal(rows[1])
                break
    end = (month + dt.timedelta(days=32)).replace(day=1)
    others = db.scalars(select(AssetRequest).where(
        AssetRequest.organization_id == org, AssetRequest.id != req.id, AssetRequest.status.in_(TAKING_BUDGET),
        AssetRequest.cfo_id.in_(_branch(db, found)), AssetRequest.needed_on >= month, AssetRequest.needed_on < end))
    used = sum((amount(r) or ZERO for r in others), ZERO)
    return Budget(cfo_id=found, plan=plan, used=used, amount=need)


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def budget_text(b: Budget) -> str:
    if b.plan is None:
        return f"{BUDGET_MARKS[b.mark]}: плана на покупку ОС на этот месяц нет"
    return (f"{BUDGET_MARKS[b.mark]}: план {_money(b.plan)}, другие заявки {_money(b.used)}, "
            f"остаток {_money(b.remaining)}, заявка {_money(b.amount)}")


def recompute(db: Session, req: AssetRequest) -> Budget:
    """Отметка по расчёту (ручную отметку финансового директора не трогает)."""
    b = budget(db, req)
    req.budget_cfo_id, req.budget_plan, req.budget_used = b.cfo_id, b.plan, b.used
    if not req.budget_manual:
        req.budget_mark = b.mark
    return b


# --- действия ---


def check_submit(req: AssetRequest) -> None:
    if req.cfo_id is None:
        raise RouteError("Укажите, для какого ЦФО заявка")
    if req.needed_on is None:
        raise RouteError("Укажите, когда нужно — по бюджету этого месяца проверяется заявка")
    if req.estimated_price is None:
        raise RouteError("Укажите ориентир цены за единицу — по нему проверяется бюджет")


def submit(db: Session, req: AssetRequest, user: User) -> str:
    check_submit(req)
    req.budget_manual, req.budget_comment = False, ""
    req.route, req.step, req.status, req.reject_reason = cfo_steps(db, req, user), 0, "pending", ""
    b = recompute(db, req)
    apply_tail(db, req)
    return budget_text(b)


def can_act(db: Session, user: User, req: AssetRequest, perms: frozenset) -> bool:
    """Может ли пользователь согласовать заявку на её текущем шаге (на шаге ФД — принять решение)."""
    step = current_step(req)
    if step is None:
        return False
    if user.is_admin:
        return True
    if step == "fd":
        return Perm.SUPERVISE in perms
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                    Membership.organization_id == req.organization_id))
    if membership is None:
        return False
    if step == "ceo":
        return membership.role == Role.CEO
    return membership.role == Role.CFO_HEAD and membership.cfo_id == cfo_of(step)


def approve_step(db: Session, req: AssetRequest) -> str | None:
    """Шаг ЦФО или CEO согласован; после цепочки ЦФО бюджет пересчитывается. Возвращает текст пересчёта."""
    step = current_step(req)
    if step is None or step == "fd":
        raise RouteError("На шаге финансового директора — «Принять решение…»")
    req.step += 1
    if step.startswith("cfo:") and req.step == _cfo_count(req.route):
        b = recompute(db, req)
        apply_tail(db, req)
        return budget_text(b)
    return None


def set_mark(db: Session, req: AssetRequest, mark: str | None, comment: str) -> str:
    """Финансовый директор ставит отметку вручную (mark None — снова по расчёту)."""
    if req.status != "pending":
        raise RouteError("Отметку о бюджете меняют у заявки на согласовании")
    if mark is None:
        req.budget_manual, req.budget_comment = False, ""
        text = budget_text(recompute(db, req)) + " (по расчёту)"
    else:
        if not comment:
            raise RouteError("Напишите, почему отметка меняется вручную")
        req.budget_manual, req.budget_mark, req.budget_comment = True, mark, comment
        text = f"{BUDGET_MARKS[mark]} (вручную): {comment}"
    if req.step >= _cfo_count(req.route or []):
        apply_tail(db, req)
    return text


def back_to_decision(req: AssetRequest) -> None:
    """«Вернуть на решение»: заявка снова у финансового директора."""
    if "fd" not in (req.route or []):
        req.route = list(req.route or []) + ["fd"]
    req.status, req.step = "pending", req.route.index("fd")


def step_title(step: str, cfo_names: dict[int, str]) -> str:
    cfo_id = cfo_of(step)
    if cfo_id is not None:
        return f"Руководитель ЦФО «{cfo_names.get(cfo_id, cfo_id)}»"
    return STEP_TITLES.get(step, step)
