"""«Сверху ИТОГ» и фиксация плана месяца (docs/TZ.md, раздел 31, «Детали 23.4»): правят администратор
и финансовый директор (право SUPERVISE); смотреть историю — все с просмотром бюджета."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.budgets import _guard
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Budget, BudgetVersion, Cfo, PlanItogEvent, User
from app.services import plan_itog
from app.services.articles import labels as article_labels
from app.services.journal import JournalError
from app.services.plan import PlanError
from app.services.reports import ReportFilter

router = APIRouter(prefix="/api", tags=["Бюджет"])

Month = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]


class ItogIn(BaseModel):
    month: Month
    cfo_id: int | None = None  # ЦФО отчёта (пусто — вся организация)
    article_id: int
    amount: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = None  # своя сумма
    # откуда ИТОГ: колонка norm/avg/fact/calc/cfo/executor (сумма — из неё) или manual; пусто и без суммы — сбросить
    source: Literal["norm", "avg", "fact", "calc", "cfo", "executor", "manual"] | None = None


class FixIn(BaseModel):
    month: Month
    cfo_id: int | None = None


class EventOut(BaseModel):
    at: dt.datetime
    user_name: str
    action: str
    article_name: str
    old_amount: Decimal | None
    new_amount: Decimal | None
    details: str


def _version(db: Session, user: User, version_id: int, perm: Perm, month: str,
             cfo_id: int | None) -> tuple[BudgetVersion, Budget, dt.date]:
    version = db.get(BudgetVersion, version_id)
    budget = db.get(Budget, version.budget_id) if version else None
    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Версия бюджета не найдена")
    try:
        get_organization_for(db, user, budget.organization_id, perm)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Версия бюджета не найдена") from None
        raise
    if cfo_id is not None and ((cfo := db.get(Cfo, cfo_id)) is None or cfo.organization_id != budget.organization_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="ЦФО не найден")
    try:
        first = dt.date.fromisoformat(f"{month}-01")
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Месяц — в виде ГГГГ-ММ") from None
    if first.year != budget.year or first.month not in budget.months:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Этот месяц не входит в бюджет версии")
    if perm == Perm.SUPERVISE and version.status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Версия в архиве — план не меняется")
    return version, budget, first


@router.put("/budget-versions/{version_id}/itog")
def put_itog(version_id: int, body: ItogIn, user: CurrentUser, db: DbSession) -> dict:
    """«Сверху ИТОГ» статьи на месяц: из колонки метода или своя сумма; пусто — снова равен бюджету."""
    version, budget, first = _version(db, user, version_id, Perm.SUPERVISE, body.month, body.cfo_id)
    try:
        plan_itog.set_itog(db, version, budget.organization_id, body.cfo_id, body.article_id, first, body.amount, user,
                           source=body.source)
    except PlanError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"ok": True}


@router.post("/budget-versions/{version_id}/itog/own-methods")
def itog_own_methods(version_id: int, body: FixIn, user: CurrentUser, db: DbSession) -> dict:
    """ИТОГ = основная колонка статьи у всех статей, где ИТОГ ещё не выбран."""
    version, budget, first = _version(db, user, version_id, Perm.SUPERVISE, body.month, body.cfo_id)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    f = ReportFilter(organization_id=budget.organization_id, date_from=first, date_to=last, cfo_id=body.cfo_id)
    try:
        count = plan_itog.own_methods(db, version, f, first, user)
    except PlanError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"count": count}


@router.post("/budget-versions/{version_id}/itog/fix")
def fix_plan(version_id: int, body: FixIn, user: CurrentUser, db: DbSession) -> dict:
    """«Зафиксировать план»: бюджет — по выбранному ИТОГ (24.4), ИТОГ статей — утверждённые цифры."""
    version, budget, first = _version(db, user, version_id, Perm.SUPERVISE, body.month, body.cfo_id)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    f = ReportFilter(organization_id=budget.organization_id, date_from=first, date_to=last, cfo_id=body.cfo_id)
    try:
        result = plan_itog.fix(db, version, f, first, user, _guard(db, user, version))
    except (PlanError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return result


@router.get("/budget-versions/{version_id}/itog-events", response_model=list[EventOut])
def itog_events(version_id: int, user: CurrentUser, db: DbSession, month: Month,
                cfo_id: Annotated[int | None, Query()] = None):
    version, budget, first = _version(db, user, version_id, Perm.VIEW, month, cfo_id)
    rows = db.execute(select(PlanItogEvent, User).join(User, User.id == PlanItogEvent.user_id).where(
        PlanItogEvent.version_id == version.id, PlanItogEvent.month == first.month,
        PlanItogEvent.cfo_id.is_(None) if cfo_id is None else PlanItogEvent.cfo_id == cfo_id)
        .order_by(PlanItogEvent.id.desc())).all()
    names = article_labels(db, budget.organization_id)
    return [EventOut(at=e.at, user_name=u.full_name or u.login, action=e.action,
                     article_name=names.get(e.article_id, "") if e.article_id else "", old_amount=e.old_amount,
                     new_amount=e.new_amount, details=e.details) for e, u in rows]
