"""Закрытие периода и регламентные операции (docs/TZ.md, раздел 14, «Детали 4.5»).

Закрывают и открывают период, ведут регламентные операции — финансовый директор и администратор
(право «проведение»); остальные видят дату закрытия в организации. Руководителю ЦФО раздел недоступен.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import LegalEntity, Operation, Organization, PeriodClosing, Posting, RoutineRule, RoutineRun, User
from app.services import period, routines

router = APIRouter(prefix="/api", tags=["Закрытие периода"])

Year = Annotated[int, Field(ge=2000, le=2100)]
Month = Annotated[int, Field(ge=1, le=12)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Day = Annotated[int, Field(ge=1, le=31)]


def _organization(db: Session, user: User, organization_id: int) -> None:
    get_organization_for(db, user, organization_id, Perm.POST)
    forbid_scoped(db, user, organization_id)


def _call(db: Session, func_, *args, **kwargs):
    try:
        result = func_(*args, **kwargs)
    except (period.ClosingError, routines.RoutineError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return result


# --- Закрытие ---


class CheckOut(BaseModel):
    code: str
    title: str
    ok: bool
    blocking: bool
    details: list[str]


class LogOut(BaseModel):
    action: str
    closed_through: dt.date | None
    previous: dt.date | None
    user_name: str
    at: dt.datetime


class ClosingOut(BaseModel):
    closed_through: dt.date | None
    year: int
    month: int
    checks: list[CheckOut]
    can_close: bool
    log: list[LogOut]


def _closing_out(db: Session, organization_id: int, year: int, month: int) -> ClosingOut:
    db.expire_all()
    checks = period.checks(db, organization_id, year, month)
    rows = db.execute(select(PeriodClosing, User.full_name).join(User, User.id == PeriodClosing.user_id)
                      .where(PeriodClosing.organization_id == organization_id)
                      .order_by(PeriodClosing.id.desc()).limit(50)).all()
    return ClosingOut(
        closed_through=db.get(Organization, organization_id).closed_through, year=year, month=month,
        checks=[CheckOut(**c.__dict__) for c in checks],
        can_close=all(c.ok for c in checks if c.blocking),
        log=[LogOut(action=r.action, closed_through=r.closed_through, previous=r.previous, user_name=name, at=r.at)
             for r, name in rows])


@router.get("/organizations/{organization_id}/closing", response_model=ClosingOut)
def closing_state(organization_id: int, year: Year, month: Month, user: CurrentUser, db: DbSession):
    """Дата закрытия, проверки месяца и история закрытий."""
    _organization(db, user, organization_id)
    return _closing_out(db, organization_id, year, month)


class MonthIn(BaseModel):
    year: Year
    month: Month


@router.post("/organizations/{organization_id}/closing/close", response_model=ClosingOut)
def close_month(organization_id: int, body: MonthIn, user: CurrentUser, db: DbSession):
    _organization(db, user, organization_id)
    _call(db, period.close, db, organization_id, body.year, body.month, user)
    return _closing_out(db, organization_id, body.year, body.month)


@router.post("/organizations/{organization_id}/closing/reopen", response_model=ClosingOut)
def reopen_month(organization_id: int, body: MonthIn, user: CurrentUser, db: DbSession):
    """Открыть последний закрытый месяц (year/month — какой месяц показать после открытия)."""
    _organization(db, user, organization_id)
    _call(db, period.reopen, db, organization_id, user)
    return _closing_out(db, organization_id, body.year, body.month)


# --- Регламентные операции ---


class RuleOut(BaseModel):
    id: int
    name: str
    legal_entity_id: int
    legal_entity_name: str
    day: int
    valid_from: dt.date
    valid_to: dt.date | None
    post: bool
    is_active: bool
    description: str
    total: Decimal  # сумма проводок образца (в валюте проводок)
    postings: int


def _rule_out(db: Session, rule: RoutineRule) -> RuleOut:
    postings = rule.template.get("postings", [])
    return RuleOut(id=rule.id, name=rule.name, legal_entity_id=rule.legal_entity_id,
                   legal_entity_name=db.get(LegalEntity, rule.legal_entity_id).short_name, day=rule.day,
                   valid_from=rule.valid_from, valid_to=rule.valid_to, post=rule.post, is_active=rule.is_active,
                   description=rule.template.get("description", ""),
                   total=sum((Decimal(p["amount"]) for p in postings if p.get("amount")), Decimal("0.00")),
                   postings=len(postings))


def _rule(db: Session, user: User, rule_id: int) -> RoutineRule:
    rule = db.get(RoutineRule, rule_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Регламентная операция не найдена")
    if rule is None:
        raise not_found
    try:
        _organization(db, user, rule.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return rule


@router.get("/organizations/{organization_id}/routines", response_model=list[RuleOut])
def list_rules(organization_id: int, user: CurrentUser, db: DbSession):
    _organization(db, user, organization_id)
    rules = db.scalars(select(RoutineRule).where(RoutineRule.organization_id == organization_id)
                       .order_by(RoutineRule.is_active.desc(), RoutineRule.day, RoutineRule.name))
    return [_rule_out(db, r) for r in rules]


class RuleCreate(BaseModel):
    name: Name
    day: Day
    valid_from: dt.date  # первый месяц (берётся месяц даты)
    valid_to: dt.date | None = None
    post: bool = True


@router.post("/operations/{operation_id}/routine", response_model=RuleOut, status_code=201)
def create_rule(operation_id: int, body: RuleCreate, user: CurrentUser, db: DbSession):
    """Сделать операцию журнала регламентной: она станет образцом для следующих месяцев."""
    op = db.get(Operation, operation_id)
    if op is None or op.budget_version_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Операция не найдена")
    try:
        _organization(db, user, op.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Операция не найдена") from None
        raise
    if body.valid_to is not None and body.valid_to.replace(day=1) < body.valid_from.replace(day=1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Последний месяц раньше первого")
    rule = _call(db, routines.from_operation, db, op, name=body.name, day=body.day, valid_from=body.valid_from,
                 post=body.post)
    if body.valid_to is not None:
        rule.valid_to = body.valid_to.replace(day=1)
        db.commit()
    return _rule_out(db, rule)


class RuleUpdate(BaseModel):
    name: Name | None = None
    day: Day | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    post: bool | None = None
    is_active: bool | None = None


@router.patch("/routines/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleUpdate, user: CurrentUser, db: DbSession):
    rule = _rule(db, user, rule_id)
    data = body.model_dump(exclude_unset=True)
    for name, value in data.items():
        if name in ("valid_from", "valid_to") and value is not None:
            value = value.replace(day=1)
        if value is not None or name == "valid_to":
            setattr(rule, name, value)
    if rule.valid_to is not None and rule.valid_to < rule.valid_from:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Последний месяц раньше первого")
    db.commit()
    return _rule_out(db, rule)


@router.delete("/routines/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: CurrentUser, db: DbSession) -> None:
    """Удалить правило, которое ещё ни разу не выполнялось; выполнявшееся — только в архив."""
    rule = _rule(db, user, rule_id)
    if db.scalar(select(func.count()).select_from(RoutineRun).where(RoutineRun.rule_id == rule.id)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Правило уже выполнялось — снимите отметку «действует», чтобы оно больше не выполнялось")
    db.delete(rule)
    db.commit()


class RunRow(BaseModel):
    rule_id: int
    rule_name: str
    legal_entity_name: str
    op_date: dt.date
    run_id: int | None
    operation_id: int | None
    operation_number: int | None
    operation_status: str | None
    total: Decimal | None
    error: str | None = None


def _run_rows(db: Session, organization_id: int, year: int, month: int,
              errors: dict[int, str] | None = None) -> list[RunRow]:
    rows = []
    for rule in routines.due(db, organization_id, year, month):
        run = routines.run_of(db, rule.id, year, month)
        op = db.get(Operation, run.operation_id) if run else None
        total = db.scalar(select(func.coalesce(func.sum(Posting.amount_rub), 0)).where(
            Posting.operation_id == op.id)) if op else None
        rows.append(RunRow(rule_id=rule.id, rule_name=rule.name,
                           legal_entity_name=db.get(LegalEntity, rule.legal_entity_id).short_name,
                           op_date=routines.run_date(rule, year, month), run_id=run.id if run else None,
                           operation_id=op.id if op else None, operation_number=op.number if op else None,
                           operation_status=op.status if op else None, total=total,
                           error=(errors or {}).get(rule.id)))
    return rows


@router.get("/organizations/{organization_id}/routine-runs", response_model=list[RunRow])
def list_runs(organization_id: int, year: Year, month: Month, user: CurrentUser, db: DbSession):
    """Регламентные операции месяца: выполнена или нет."""
    _organization(db, user, organization_id)
    return _run_rows(db, organization_id, year, month)


@router.post("/organizations/{organization_id}/routine-runs", response_model=list[RunRow])
def execute_runs(organization_id: int, body: MonthIn, user: CurrentUser, db: DbSession):
    """Выполнить невыполненные регламентные операции месяца."""
    _organization(db, user, organization_id)
    results = routines.execute(db, organization_id, body.year, body.month, user)
    db.commit()
    db.expire_all()
    return _run_rows(db, organization_id, body.year, body.month,
                     {r.rule.id: r.error for r in results if r.error})
