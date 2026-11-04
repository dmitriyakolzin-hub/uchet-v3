"""Платёжный календарь: ожидаемые платежи (docs/TZ.md, раздел 18, «Детали 8.2»).
Просмотр — право VIEW; руководителю ЦФО недоступно (деньги организации целиком)."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Article, BankAccount, CalendarBalance, Cfo, Counterparty, LegalEntity, PaymentRequest
from app.services import calendar, calendar_sheet
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


# --- календарь по форме владельца, «лист 1» (docs/TZ.md, раздел 24, «Детали 14.6») ---

MAX_SHEET_DAYS = 62


class PairOut(BaseModel):
    fact: Decimal | None = None
    plan: Decimal | None = None


class SheetItem(BaseModel):
    source: str
    source_id: int
    title: str
    date: dt.date
    amount: Decimal
    status: str
    overdue: bool
    movable: bool  # заявку можно перенести (перетащить на другой день)
    amount_editable: bool  # сумму заявки можно поправить в ячейке


class SheetOp(BaseModel):
    operation_id: int
    number: int
    amount: Decimal


class SheetCell(PairOut):
    ops: list[SheetOp] = []
    items: list[SheetItem] = []


class SheetLine(BaseModel):
    counterparty_id: int | None
    contract_id: int | None
    title: str
    contract_title: str
    bank: Decimal
    cash: Decimal
    cells: dict[dt.date, SheetCell]


class SheetGroup(BaseModel):
    direction: str
    article_id: int | None
    title: str
    bank: Decimal
    cash: Decimal
    cells: dict[dt.date, PairOut]
    lines: list[SheetLine]


class SheetSource(BaseModel):
    key: str
    kind: str
    title: str
    opening: dict[dt.date, PairOut]
    closing: dict[dt.date, Decimal]


class SheetTotals(BaseModel):
    opening: dict[dt.date, PairOut]
    inflow: dict[dt.date, PairOut]
    outflow: dict[dt.date, PairOut]
    closing: dict[dt.date, Decimal]


class SheetOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    today: dt.date
    days: list[dt.date]
    sources: list[SheetSource]
    totals: dict[str, SheetTotals]  # bank / cash
    groups: list[SheetGroup]


def _pair(p) -> PairOut:
    return PairOut(fact=p.fact, plan=p.plan)


def _sum_pairs(pairs) -> PairOut:
    fact = [p.fact for p in pairs if p.fact is not None]
    plan = [p.plan for p in pairs if p.plan is not None]
    return PairOut(fact=sum(fact, Decimal("0.00")) if fact else None, plan=sum(plan, Decimal("0.00")) if plan else None)


def _sheet(db: Session, user, organization_id: int, date_from: dt.date, date_to: dt.date, legal_entity_id,
           include_pending: bool) -> SheetOut:
    get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    period(date_from, date_to)
    if (date_to - date_from).days >= MAX_SHEET_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Календарь по статьям — не больше 2 месяцев")
    _scope(db, organization_id, legal_entity_id, None, None)
    sheet = calendar_sheet.build(db, organization_id, date_from, date_to, legal_entity_id=legal_entity_id,
                                 include_pending=include_pending)
    request_ids = {i.source_id for g in sheet.groups for ln in g.lines.values() for c in ln.cells.values()
                   for i in c.items if i.source == "request"}
    requests = {r.id: r for r in db.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(request_ids)))} \
        if request_ids else {}

    def item_out(i) -> SheetItem:
        req = requests.get(i.source_id) if i.source == "request" else None
        movable = req is not None and req.status in ("pending", "approved") and req.linked_id is None
        return SheetItem(source=i.source, source_id=i.source_id, title=i.title, date=i.date, amount=i.amount,
                         status=i.status, overdue=i.overdue, movable=movable,
                         amount_editable=movable and req.source != "supplier")

    groups = []
    for g in sheet.groups:
        lines = [SheetLine(counterparty_id=ln.counterparty_id, contract_id=ln.contract_id, title=ln.title,
                           contract_title=ln.contract_title, bank=ln.bank, cash=ln.cash,
                           cells={d: SheetCell(fact=c.fact, plan=c.plan, ops=[SheetOp(**o.__dict__) for o in c.ops],
                                               items=[item_out(i) for i in c.items]) for d, c in ln.cells.items()})
                 for ln in g.lines.values()]
        cells = {d: _sum_pairs([ln.cells[d] for ln in lines if d in ln.cells]) for d in sheet.days
                 if any(d in ln.cells for ln in lines)}
        groups.append(SheetGroup(direction=g.direction, article_id=g.article_id, title=g.title,
                                 bank=sum((ln.bank for ln in lines), Decimal("0.00")),
                                 cash=sum((ln.cash for ln in lines), Decimal("0.00")), cells=cells, lines=lines))
    totals = {}
    for kind in ("bank", "cash"):
        own = [s for s in sheet.sources if s.kind == kind]

        def flows(fact_attr, plan_attr, d):
            fact = sum((getattr(s, fact_attr).get(d, Decimal("0.00")) for s in own), Decimal("0.00"))
            plan = sum((getattr(s, plan_attr).get(d, Decimal("0.00")) for s in own), Decimal("0.00"))
            return PairOut(fact=fact or None, plan=plan or None)

        totals[kind] = SheetTotals(
            opening={d: _sum_pairs([s.opening[d] for s in own if d in s.opening]) for d in sheet.days},
            inflow={d: flows("fact_in", "plan_in", d) for d in sheet.days},
            outflow={d: flows("fact_out", "plan_out", d) for d in sheet.days},
            closing={d: sum((s.closing.get(d, Decimal("0.00")) for s in own), Decimal("0.00")) for d in sheet.days})
    return SheetOut(date_from=date_from, date_to=date_to, today=sheet.today, days=sheet.days,
                    sources=[SheetSource(key=s.key, kind=s.kind, title=s.title,
                                         opening={d: _pair(p) for d, p in s.opening.items()}, closing=s.closing)
                             for s in sheet.sources],
                    totals=totals, groups=groups)


@router.get("/organizations/{organization_id}/payment-calendar/sheet", response_model=SheetOut)
def calendar_sheet_view(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
                        legal_entity_id: int | None = None, include_pending: bool = False):
    return _sheet(db, user, organization_id, date_from, date_to, legal_entity_id, include_pending)


def _money_text(p: PairOut) -> Decimal | None:
    if p.fact is None and p.plan is None:
        return None
    return (p.fact or Decimal("0.00")) + (p.plan or Decimal("0.00"))


@router.get("/organizations/{organization_id}/payment-calendar/sheet.xlsx")
def calendar_sheet_xlsx(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
                        legal_entity_id: int | None = None, include_pending: bool = False):
    """Те же строки; в ячейке — факт + план одной суммой."""
    data = _sheet(db, user, organization_id, date_from, date_to, legal_entity_id, include_pending)
    columns = [XlsxColumn("Статья / контрагент", 34), XlsxColumn("Договор", 16), XlsxColumn("Безнал", 14, money=True),
               XlsxColumn("Касса", 14, money=True), XlsxColumn("Всего", 14, money=True)]
    columns += [XlsxColumn(f"{d:%d.%m} {WEEKDAYS[d.weekday()]}", 13, money=True) for d in data.days]
    rows = []
    for title, kind in (("Расчётные счета", "bank"), ("Касса", "cash")):
        t = data.totals[kind]
        rows.append((0, [f"На начало: {title.lower()}", None, None, None, None,
                         *[_money_text(t.opening[d]) for d in data.days]], True))
        for s in data.sources:
            if s.kind == kind:
                rows.append((1, [s.title, None, None, None, None, *[_money_text(s.opening[d]) for d in data.days]]))
        rows.append((0, [f"Поступления: {title.lower()}", None, None, None, None,
                         *[_money_text(t.inflow[d]) for d in data.days]]))
        rows.append((0, [f"Платежи: {title.lower()}", None, None, None, None,
                         *[_money_text(t.outflow[d]) for d in data.days]]))
        rows.append((0, [f"На конец: {title.lower()}", None, None, None, None, *[t.closing[d] for d in data.days]], True))
    for direction, title in (("in", "ПОСТУПЛЕНИЯ"), ("out", "ПЛАТЕЖИ")):
        rows.append((0, [title], True))
        for g in data.groups:
            if g.direction != direction:
                continue
            rows.append((0, [g.title, None, g.bank, g.cash, g.bank + g.cash,
                             *[_money_text(g.cells[d]) if d in g.cells else None for d in data.days]], True))
            for ln in g.lines:
                rows.append((1, [ln.title, ln.contract_title, ln.bank, ln.cash, ln.bank + ln.cash,
                                 *[_money_text(ln.cells[d]) if d in ln.cells else None for d in data.days]]))
    content = build("Платёжный календарь", f"{date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}", columns, rows)
    return Response(content, media_type=XLSX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="payment_calendar_sheet.xlsx"'})


class BalanceIn(BaseModel):
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None
    day: dt.date
    amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)  # None — убрать ручной остаток


@router.put("/organizations/{organization_id}/calendar-balances", status_code=204)
def set_balance(organization_id: int, body: BalanceIn, user: CurrentUser, db: DbSession) -> Response:
    """Остаток на начало дня по банк-клиенту (финансовый менеджер); пустая сумма — убрать."""
    get_organization_for(db, user, organization_id, Perm.POST)
    if (body.bank_account_id is None) == (body.cash_cfo_id is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите расчётный счёт или кассу")
    _scope(db, organization_id, None, body.bank_account_id, body.cash_cfo_id)
    row = db.scalar(select(CalendarBalance).where(CalendarBalance.bank_account_id.is_not_distinct_from(body.bank_account_id),
                                                  CalendarBalance.cash_cfo_id.is_not_distinct_from(body.cash_cfo_id),
                                                  CalendarBalance.day == body.day))
    if body.amount is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(CalendarBalance(organization_id=organization_id, bank_account_id=body.bank_account_id,
                               cash_cfo_id=body.cash_cfo_id, day=body.day, amount=body.amount, entered_by=user.id))
    else:
        row.amount, row.entered_by = body.amount, user.id
    db.commit()
    return Response(status_code=204)
