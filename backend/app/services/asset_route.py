"""Маршрут согласования заявок на имущество (docs/TZ.md, раздел 28, «Детали 21.1»).

Шаги по порядку:
- руководители ЦФО снизу вверх: ЦФО заявки, его вышестоящий и так до верха. Если заявку подал руководитель ЦФО
  этой ветки — начиная с ЦФО над его собственным. ЦФО без руководителя пропускается;
- отметка о бюджете (сама; финансовый директор может поменять вручную с комментарием). Бюджет — по виду затрат
  (шаг 21.3): инвестиционная — план вложений инвестпроекта на месяц и остаток его лимита; операционная — план ОПиУ
  ветки ЦФО по статье расходов; прежние заявки без вида затрат — план ДДС «Покупка основных средств»;
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
from app.models import Account, Article, Cfo, FixedAsset, InvestmentProject, Membership, Operation, Posting, User
from app.models.asset import BUDGET_MARKS, AssetRequest
from app.services import reports
from app.services.articles import is_group, system_article

ZERO = Decimal("0.00")
STEP_TITLES = {"ceo": "CEO", "fd": "Финансовый директор"}
# заявки, которые уже занимают бюджет ЦФО (поданные и дальше, кроме отклонённых и отменённых)
TAKING_BUDGET = ("pending", "approved", "ordered", "shipped", "delivered", "received")
# по виду затрат: принятое имущество уже в факте (вложения / расходы после постановки на учёт)
OPEN_REQUESTS = ("pending", "approved", "ordered", "shipped", "delivered")
COST_KINDS = {"invest": "Инвестиционная (капвложение)", "opex": "Операционная (расход)"}
EXPENSE_SECTIONS = ("cost", "selling", "admin", "other_expense")


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
    plan: Decimal | None  # план на месяц; None — плана нет
    used: Decimal  # другие заявки в этом бюджете в этом месяце
    amount: Decimal
    fact: Decimal = ZERO  # факт месяца (вложения проекта / расходы по статье)
    limit_left: Decimal | None = None  # остаток лимита инвестпроекта на весь срок (без лимита — None)

    @property
    def remaining(self) -> Decimal | None:
        return None if self.plan is None else self.plan - self.fact - self.used

    @property
    def mark(self) -> str:
        if self.remaining is None or self.amount > self.remaining:
            return "over"
        return "over" if self.limit_left is not None and self.amount > self.limit_left else "within"


def _month(req: AssetRequest) -> tuple[dt.date, dt.date, str]:
    start = req.needed_on.replace(day=1)
    end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return start, end, f"{start:%Y-%m}"


def _other_requests(db: Session, req: AssetRequest, **where) -> Decimal:
    start, end, _ = _month(req) if where.pop("month", True) else (None, None, None)
    query = select(AssetRequest).where(AssetRequest.organization_id == req.organization_id, AssetRequest.id != req.id,
                                       AssetRequest.status.in_(OPEN_REQUESTS), AssetRequest.cost_kind == req.cost_kind)
    if start is not None:
        query = query.where(AssetRequest.needed_on.between(start, end))
    for key, value in where.items():
        column = getattr(AssetRequest, key)
        query = query.where(column.in_(value) if isinstance(value, set) else column == value)
    return sum((amount(r) or ZERO for r in db.scalars(query)), ZERO)


def _invest_budget(db: Session, req: AssetRequest, need: Decimal) -> Budget:
    """План вложений проекта на месяц (по статье вложений, если указана) − вложено за месяц − другие заявки проекта;
    плюс остаток лимита проекта: лимит − вложено с начала − все открытые заявки проекта."""
    start, end, key = _month(req)
    project = db.get(InvestmentProject, req.project_id)
    version = reports.actual_plan_versions(db, req.organization_id, [key], "inv").get(key)
    plan = None
    if version is not None:
        query = (select(func.count(Posting.id), func.coalesce(func.sum(Posting.amount_rub), 0))
                 .join(Operation, Operation.id == Posting.operation_id)
                 .where(Operation.budget_version_id == version, Operation.plan_kind == "inv",
                        Operation.plan_project_id == project.id, Operation.op_date == start))
        if req.budget_article_id is not None:
            query = query.where(Operation.article_id == req.budget_article_id)
        count, total = db.execute(query).one()
        plan = Decimal(total) + ZERO if count else None
    f = reports.ReportFilter(organization_id=req.organization_id, date_from=start, date_to=end)
    account = db.scalar(select(Account.id).where(Account.code == "08"))
    assets = list(db.scalars(select(FixedAsset.id).where(FixedAsset.project_id == project.id)))

    def invested(date_from):
        lines = f.lines(date_from=date_from, date_to=end, account_ids=[account], side="debit",
                        analytics_in={"fixed_asset": assets}).subquery()
        return Decimal(db.scalar(select(func.coalesce(func.sum(lines.c.amount_rub), 0)))) + ZERO

    fact = invested(start) if assets else ZERO
    limit_left = None
    if project.limit_amount and project.limit_amount > 0:
        total_fact = invested(None) if assets else ZERO
        limit_left = project.limit_amount - total_fact - _other_requests(db, req, month=False, project_id=project.id)
    used = _other_requests(db, req, project_id=project.id)
    return Budget(cfo_id=project.cfo_id, plan=plan, used=used, amount=need, fact=fact, limit_left=limit_left)


def _opex_budget(db: Session, req: AssetRequest, need: Decimal) -> Budget:
    """План ОПиУ ветки ЦФО по статье расходов на месяц − расходы по журналу − другие заявки (ближайший снизу вверх
    ЦФО, у которого статья запланирована). Расход = дебет − кредит строк со статьёй (без ввода остатков и реформации)."""
    start, end, key = _month(req)
    version = reports.actual_plan_versions(db, req.organization_id, [key], "pl").get(key)
    exclude = list(db.scalars(select(Account.id).where(Account.code == "84")))  # реформация — не расход

    def expense(f: reports.ReportFilter) -> tuple[int, Decimal]:
        lines = f.lines(date_from=start, date_to=end, analytics_in={"pl_item": [req.budget_article_id]},
                        opening=False, exclude_corr_account_ids=exclude).subquery()
        count, debit, credit = db.execute(select(
            func.count(), func.coalesce(func.sum(lines.c.amount_rub).filter(lines.c.side == "debit"), 0),
            func.coalesce(func.sum(lines.c.amount_rub).filter(lines.c.side == "credit"), 0))).one()
        return count, Decimal(debit) - Decimal(credit) + ZERO

    found, plan = req.cfo_id, None
    if version is not None:
        for cfo_id in _path(db, req.cfo_id):
            count, total = expense(reports.ReportFilter(organization_id=req.organization_id, date_from=start,
                                                        date_to=end, cfo_id=cfo_id,
                                                        plan_versions=((key, version),)))
            if count:
                found, plan = cfo_id, total
                break
    _, fact = expense(reports.ReportFilter(organization_id=req.organization_id, date_from=start, date_to=end,
                                           cfo_id=found))
    used = _other_requests(db, req, budget_article_id=req.budget_article_id, cfo_id=_branch(db, found))
    return Budget(cfo_id=found, plan=plan, used=used, amount=need, fact=fact)


def budget(db: Session, req: AssetRequest) -> Budget:
    need = amount(req) or ZERO
    if req.cost_kind == "invest":
        return _invest_budget(db, req, need)
    if req.cost_kind == "opex":
        return _opex_budget(db, req, need)
    return _cash_budget(db, req)


def _cash_budget(db: Session, req: AssetRequest) -> Budget:
    """Прежние заявки без вида затрат (21.1): план ДДС «Покупка основных средств»."""
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
        return f"{BUDGET_MARKS[b.mark]}: плана на этот месяц нет"
    text = (f"{BUDGET_MARKS[b.mark]}: план {_money(b.plan)}, факт {_money(b.fact)}, другие заявки {_money(b.used)}, "
            f"остаток {_money(b.remaining)}, заявка {_money(b.amount)}")
    if b.limit_left is not None:
        text += f"; остаток лимита проекта {_money(b.limit_left)}"
    return text


def recompute(db: Session, req: AssetRequest) -> Budget:
    """Отметка по расчёту (ручную отметку финансового директора не трогает)."""
    b = budget(db, req)
    req.budget_cfo_id, req.budget_plan, req.budget_used = b.cfo_id, b.plan, b.used
    req.budget_fact, req.budget_limit_left = (b.fact, b.limit_left) if req.cost_kind else (None, None)
    if not req.budget_manual:
        req.budget_mark = b.mark
    return b


# --- действия ---


def check_cost(db: Session, req: AssetRequest) -> None:
    """Вид затрат: инвестиционная — инвестпроект организации (+ статья вложений), операционная — статья расходов ОПиУ."""
    if req.cost_kind not in COST_KINDS:
        raise RouteError("Укажите вид затрат: инвестиционная или операционная")
    article = db.get(Article, req.budget_article_id) if req.budget_article_id else None
    if article is not None and (article.organization_id != req.organization_id or is_group(db, article.id)):
        raise RouteError("Статья: не найдена или это группа статей")
    if req.cost_kind == "invest":
        project = db.get(InvestmentProject, req.project_id) if req.project_id else None
        if project is None or project.organization_id != req.organization_id:
            raise RouteError("Укажите инвестпроект")
        if article is not None and not (article.is_cash_flow and article.cash_flow_activity == "investing"
                                        and article.cash_flow_direction == "outflow"):
            raise RouteError("Статья вложений — инвестиционная статья ДДС выплат")
    else:
        req.project_id = None
        if article is None or not article.is_pl or article.pl_section not in EXPENSE_SECTIONS:
            raise RouteError("Укажите статью расходов ОПиУ")


def check_submit(req: AssetRequest) -> None:
    if req.cfo_id is None:
        raise RouteError("Укажите, для какого ЦФО заявка")
    if req.needed_on is None:
        raise RouteError("Укажите, когда нужно — по бюджету этого месяца проверяется заявка")
    if req.estimated_price is None:
        raise RouteError("Укажите ориентир цены за единицу — по нему проверяется бюджет")


def submit(db: Session, req: AssetRequest, user: User) -> str:
    check_submit(req)
    check_cost(db, req)
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


def set_cost(db: Session, req: AssetRequest, cost_kind: str, project_id: int | None,
             budget_article_id: int | None) -> str:
    """Финансовый директор указывает или поправляет вид затрат (и у прежних заявок) — бюджет пересчитывается;
    у заявки на согласовании после цепочки ЦФО маршрут перестраивается (сверх бюджета — CEO)."""
    if req.status not in OPEN_REQUESTS:
        raise RouteError("Вид затрат меняется у заявки на согласовании или в закупке")
    req.cost_kind, req.project_id, req.budget_article_id = cost_kind, project_id, budget_article_id
    check_cost(db, req)
    if req.needed_on is None:
        raise RouteError("Укажите, когда нужно — заявку правит автор или финансовый директор")
    b = recompute(db, req)
    if req.status == "pending" and req.step >= _cfo_count(req.route or []):
        apply_tail(db, req)
    return f"{COST_KINDS[cost_kind]}; " + budget_text(b)


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
