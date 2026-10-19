"""Платёжный календарь: ожидаемые платежи (docs/TZ.md, раздел 18, «Детали 8.2»).
Просмотр — право VIEW; руководителю ЦФО недоступно (деньги организации целиком)."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Article, BankAccount, Cfo, Counterparty, LegalEntity
from app.services import calendar
from app.services.report_xlsx import XlsxColumn, build

router = APIRouter(prefix="/api", tags=["Платёжный календарь"])

MAX_DAYS = 400
MAX_GRID_DAYS = 186
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExpectedItem(BaseModel):
    source: str
    source_id: int
    title: str
    date: dt.date
    direction: str
    amount: Decimal
    status: str
    overdue: bool
    legal_entity_id: int | None
    legal_entity_name: str
    bank_account_id: int | None
    cash_cfo_id: int | None
    source_name: str  # расчётный счёт или касса
    counterparty_id: int | None
    counterparty_name: str
    article_id: int | None
    article_name: str


class ExpectedOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    rows: list[ExpectedItem]
    total_in: Decimal
    total_out: Decimal


def _names(db: Session, model, ids, label) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}


def period(date_from: dt.date, date_to: dt.date) -> None:
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Конец периода раньше начала")
    if (date_to - date_from).days > MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Период календаря — не больше 13 месяцев")


def expected_out(db: Session, items: list[calendar.Item]) -> list[ExpectedItem]:
    entities = _names(db, LegalEntity, [i.legal_entity_id for i in items], lambda e: e.short_name)
    banks = _names(db, BankAccount, [i.bank_account_id for i in items], lambda b: f"{b.name} ··{b.number[-4:]}")
    cfos = _names(db, Cfo, [i.cash_cfo_id for i in items], lambda c: f"Касса: {c.name}")
    counterparties = _names(db, Counterparty, [i.counterparty_id for i in items], lambda c: c.name)
    articles = _names(db, Article, [i.article_id for i in items], lambda a: a.name)
    return [ExpectedItem(
        **i.__dict__, legal_entity_name=entities.get(i.legal_entity_id, ""),
        source_name=banks.get(i.bank_account_id) or cfos.get(i.cash_cfo_id, ""),
        counterparty_name=counterparties.get(i.counterparty_id, ""), article_name=articles.get(i.article_id, ""),
    ) for i in items]


@router.get("/organizations/{organization_id}/payment-calendar/expected", response_model=ExpectedOut)
def expected(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
             include_pending: bool = False, legal_entity_id: int | None = None):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    period(date_from, date_to)
    if legal_entity_id is not None:
        entity = db.get(LegalEntity, legal_entity_id)
        if entity is None or entity.organization_id != organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Юрлицо не найдено в этой организации")
    items = calendar.expected(db, organization_id, date_from, date_to, include_pending=include_pending,
                              legal_entity_id=legal_entity_id)
    total = {d: sum((i.amount for i in items if i.direction == d), Decimal("0.00")) for d in ("in", "out")}
    return ExpectedOut(date_from=date_from, date_to=date_to, rows=expected_out(db, items),
                       total_in=total["in"], total_out=total["out"])


# --- календарь по дням (docs/TZ.md, раздел 18, «Детали 8.3») ---


class FactOut(BaseModel):
    operation_id: int
    number: int
    description: str
    inflow: Decimal
    outflow: Decimal


class DayOut(BaseModel):
    date: dt.date
    opening: Decimal
    fact_in: Decimal
    fact_out: Decimal
    expected_in: Decimal
    expected_out: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    fact: list[FactOut]
    expected: list[ExpectedItem]


class WeekOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    inflow: Decimal
    outflow: Decimal
    closing: Decimal


class GapOut(BaseModel):
    date: dt.date
    closing: Decimal


class GridOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    first_gap: GapOut | None
    days: list[DayOut]
    weeks: list[WeekOut]


def _scope(db: Session, organization_id: int, legal_entity_id, bank_account_id, cash_cfo_id) -> calendar.Scope:
    if bank_account_id is not None and cash_cfo_id is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Выберите либо расчётный счёт, либо кассу")
    for model, value, title in ((LegalEntity, legal_entity_id, "Юрлицо"), (BankAccount, bank_account_id, "Расчётный счёт"),
                                (Cfo, cash_cfo_id, "Касса")):
        record = db.get(model, value) if value is not None else None
        if value is not None and (record is None or record.organization_id != organization_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{title} не найден(а) в этой организации")
    return calendar.Scope(legal_entity_id, bank_account_id, cash_cfo_id)


def _grid(db: Session, user, organization_id: int, date_from: dt.date, date_to: dt.date, legal_entity_id,
          bank_account_id, cash_cfo_id, include_pending) -> GridOut:
    get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    period(date_from, date_to)
    if (date_to - date_from).days >= MAX_GRID_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Период календаря по дням — не больше 6 месяцев")
    scope = _scope(db, organization_id, legal_entity_id, bank_account_id, cash_cfo_id)
    result = calendar.grid(db, organization_id, date_from, date_to, scope, include_pending=include_pending)
    days = [DayOut(date=d.date, opening=d.opening, fact_in=d.fact_in, fact_out=d.fact_out, expected_in=d.expected_in,
                   expected_out=d.expected_out, inflow=d.inflow, outflow=d.outflow, closing=d.closing,
                   fact=[FactOut(**f.__dict__) for f in d.fact], expected=expected_out(db, d.expected))
            for d in result.days]
    weeks: list[WeekOut] = []
    for d in result.days:
        if not weeks or d.date.weekday() == 0:
            weeks.append(WeekOut(date_from=d.date, date_to=d.date, inflow=Decimal("0.00"), outflow=Decimal("0.00"),
                                 closing=d.closing))
        week = weeks[-1]
        week.date_to, week.closing = d.date, d.closing
        week.inflow += d.inflow
        week.outflow += d.outflow
    gap = result.first_gap
    return GridOut(
        date_from=date_from, date_to=date_to, opening=result.opening,
        inflow=sum((d.inflow for d in days), Decimal("0.00")), outflow=sum((d.outflow for d in days), Decimal("0.00")),
        closing=days[-1].closing, first_gap=GapOut(date=gap.date, closing=gap.closing) if gap else None,
        days=days, weeks=weeks,
    )


@router.get("/organizations/{organization_id}/payment-calendar", response_model=GridOut)
def calendar_grid(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
                  legal_entity_id: int | None = None, bank_account_id: int | None = None,
                  cash_cfo_id: int | None = None, include_pending: bool = False):
    return _grid(db, user, organization_id, date_from, date_to, legal_entity_id, bank_account_id, cash_cfo_id,
                 include_pending)


WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


@router.get("/organizations/{organization_id}/payment-calendar.xlsx")
def calendar_xlsx(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
                  legal_entity_id: int | None = None, bank_account_id: int | None = None,
                  cash_cfo_id: int | None = None, include_pending: bool = False):
    data = _grid(db, user, organization_id, date_from, date_to, legal_entity_id, bank_account_id, cash_cfo_id,
                 include_pending)
    columns = [XlsxColumn("Дата", 18), XlsxColumn("Остаток на начало", 18, money=True),
               XlsxColumn("Поступления", 16, money=True), XlsxColumn("Выплаты", 16, money=True),
               XlsxColumn("Остаток на конец", 18, money=True), XlsxColumn("в т.ч. ожидается поступлений", 18, money=True),
               XlsxColumn("в т.ч. ожидается выплат", 18, money=True), XlsxColumn("", 18)]
    rows = []
    weeks = iter(data.weeks)
    week = next(weeks)
    for d in data.days:
        rows.append((0, [f"{d.date:%d.%m.%Y} {WEEKDAYS[d.date.weekday()]}", d.opening, d.inflow, d.outflow, d.closing,
                         d.expected_in, d.expected_out, "кассовый разрыв" if d.closing < 0 else None]))
        if d.date == week.date_to:
            rows.append((0, [f"Неделя {week.date_from:%d.%m}–{week.date_to:%d.%m}", None, week.inflow, week.outflow,
                             week.closing, None, None, None], True))
            week = next(weeks, week)
    content = build("Платёжный календарь", f"Период: {date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}", columns, rows,
                    total=["Итого за период", data.opening, data.inflow, data.outflow, data.closing, None, None, None])
    return Response(content, media_type=XLSX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="payment_calendar.xlsx"'})
