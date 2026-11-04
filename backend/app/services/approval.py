"""Маршрут согласования заявок на оплату (docs/TZ.md, раздел 24, «Детали 14.1»).

Шаги по порядку:
- встречная сторона: счёт принёс ЦФО — согласует руководитель ЦФУ заявки; принёс ЦФУ — руководитель ЦФО;
  нет второй стороны или её руководителя — шаг пропускается;
- финансовый менеджер (если он есть в организации);
- CEO — по правилу: все инвестиционные заявки и суммы больше 1 000 000 ₽, кроме оплат поставщикам
  (поставщики идут из реестра накладных);
- финансовый директор — последним; после него заявка «согласована» и попадает в реестр на оплату.
Периодические платежи согласует только финансовый директор. Администратор может действовать на любом шаге.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Role
from app.models import Article, Cfo, Executor, Membership, PaymentRequest, PaymentRequestEvent, User

CEO_LIMIT = Decimal("1000000")
STEP_TITLES = {"executor": "Руководитель ЦФУ", "cfo": "Руководитель ЦФО", "fm": "Финансовый менеджер",
               "ceo": "CEO", "fd": "Финансовый директор"}
STEP_ROLES = {"executor": Role.EXECUTOR_HEAD, "cfo": Role.CFO_HEAD, "fm": Role.FINANCE_MANAGER, "ceo": Role.CEO,
              "fd": Role.FINANCE_DIRECTOR}


def initiator_of(db: Session, user: User, organization_id: int) -> str:
    """Кто принёс счёт: руководитель ЦФО, руководитель ЦФУ или финансовая служба (остальные роли)."""
    if user.is_admin:
        return "finance"
    role = db.scalar(select(Membership.role).where(Membership.user_id == user.id,
                                                   Membership.organization_id == organization_id))
    return {Role.CFO_HEAD: "cfo", Role.EXECUTOR_HEAD: "executor"}.get(role, "finance")


def _executor_ids(db: Session, executor_id: int) -> set[int]:
    """ЦФУ заявки и его отдел: исполнителя согласует руководитель исполнителя или его отдела."""
    executor = db.get(Executor, executor_id)
    return {executor.id, executor.parent_id} - {None} if executor else set()


def _cfo_ancestors(db: Session, cfo_id: int) -> set[int]:
    cfo = db.get(Cfo, cfo_id)
    return {int(i) for i in str(cfo.path).split(".")} if cfo else set()


def _members(db: Session, organization_id: int, role: Role):
    return db.scalars(select(Membership).where(Membership.organization_id == organization_id,
                                               Membership.role == role.value))


def _has_approver(db: Session, req: PaymentRequest, step: str) -> bool:
    members = list(_members(db, req.organization_id, STEP_ROLES[step]))
    if step == "executor":
        ids = _executor_ids(db, req.executor_id)
        return any(m.executor_id in ids for m in members)
    if step == "cfo":
        ancestors = _cfo_ancestors(db, req.cfo_id)
        return any(m.cfo_id in ancestors for m in members)
    return bool(members)


def ceo_required(db: Session, req: PaymentRequest) -> bool:
    article = db.get(Article, req.article_id) if req.article_id else None
    if article is not None and article.cash_flow_activity == "investing":
        return True
    return req.source != "supplier" and req.amount > CEO_LIMIT


def build_route(db: Session, req: PaymentRequest) -> list[str]:
    if req.source == "periodic":
        return ["fd"]
    steps = []
    if req.initiator == "cfo" and req.executor_id and _has_approver(db, req, "executor"):
        steps.append("executor")
    if req.initiator == "executor" and req.cfo_id and _has_approver(db, req, "cfo"):
        steps.append("cfo")
    if _has_approver(db, req, "fm"):
        steps.append("fm")
    if ceo_required(db, req) and _has_approver(db, req, "ceo"):
        steps.append("ceo")
    steps.append("fd")
    return steps


def current_step(req: PaymentRequest) -> str | None:
    if req.status != "pending" or req.step >= len(req.route or []):
        return None
    return req.route[req.step]


def can_act(db: Session, user: User, req: PaymentRequest) -> bool:
    """Может ли пользователь согласовать (отклонить, вернуть на доработку) заявку на её текущем шаге."""
    step = current_step(req)
    if step is None:
        return False
    if user.is_admin:
        return True
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                    Membership.organization_id == req.organization_id))
    if membership is None or membership.role != STEP_ROLES[step].value:
        return False
    if step == "executor":
        return membership.executor_id in _executor_ids(db, req.executor_id)
    if step == "cfo":
        return membership.cfo_id in _cfo_ancestors(db, req.cfo_id)
    return True


def event(db: Session, req: PaymentRequest, user: User, action: str, comment: str = "", step: str = "") -> None:
    db.add(PaymentRequestEvent(request_id=req.id, user_id=user.id, action=action, step=step, comment=comment[:1000]))


def submit(db: Session, req: PaymentRequest, user: User) -> None:
    req.route, req.step, req.status, req.reject_reason = build_route(db, req), 0, "pending", ""
    event(db, req, user, "submitted")


def approve(db: Session, req: PaymentRequest, user: User, comment: str = "") -> None:
    """Шаг согласован: следующий шаг или, после последнего, «согласована» (в реестр на оплату)."""
    step = current_step(req)
    event(db, req, user, "approved", comment, step or "")
    req.step += 1
    if req.step >= len(req.route):
        req.status, req.approved_by, req.approved_at = "approved", user.id, dt.datetime.now(dt.UTC)
