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
from app.models import (
    Article,
    BankAccount,
    CalendarBalance,
    CalendarTransfer,
    Cfo,
    Counterparty,
    LegalEntity,
    PaymentRequest,
)
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


# --- календарь по форме владельца: раскрывающиеся блоки (docs/TZ.md, раздел 24 «Детали 14.6», раздел 26 «Детали 16.1») ---

MAX_SHEET_DAYS = 62
CHANNELS = (("bank", "Безнал"), ("cash", "Наличные"))


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


class BalanceRef(BaseModel):
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None


class SheetRow(BaseModel):
    key: str
    parent: str | None
    level: int
    kind: str  # block / entity / channel / source / type / article / line / section
    title: str
    subtitle: str = ""  # договор строки расшифровки
    signed: bool = False  # технические операции: + пришло, − ушло
    balance: BalanceRef | None = None  # остаток на начало можно ввести вручную
    source: str | None = None  # ключ счёта / кассы (bank:…, cash:…)
    source_ref: BalanceRef | None = None  # счёт или касса строки — для ручного поступления
    entity_id: int | None = None
    add: str | None = None  # «+»: inflow — ожидаемое поступление на счёт, transfer — плановый перевод
    cells: dict[dt.date, SheetCell] = {}


class SheetOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    today: dt.date
    days: list[dt.date]
    rows: list[SheetRow]


def _add(target: dict, day, fact=None, plan=None) -> None:
    if fact is None and plan is None:
        return
    cell = target.setdefault(day, SheetCell())
    if fact is not None:
        cell.fact = (cell.fact or Decimal("0.00")) + fact
    if plan is not None:
        cell.plan = (cell.plan or Decimal("0.00")) + plan


def _sheet(db: Session, user, organization_id: int, date_from: dt.date, date_to: dt.date, legal_entity_id,
           include_pending: bool, with_statements: bool = True) -> SheetOut:
    get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    period(date_from, date_to)
    if (date_to - date_from).days >= MAX_SHEET_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Календарь по статьям — не больше 2 месяцев")
    _scope(db, organization_id, legal_entity_id, None, None)
    sheet = calendar_sheet.build(db, organization_id, date_from, date_to, legal_entity_id=legal_entity_id,
                                 include_pending=include_pending, with_statements=with_statements)
    today = sheet.today
    request_ids = {i.source_id for ln in sheet.lines for c in ln.cells.values() for i in c.items if i.source == "request"}
    requests = {r.id: r for r in db.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(request_ids)))} \
        if request_ids else {}

    def item_out(i) -> SheetItem:
        req = requests.get(i.source_id) if i.source == "request" else None
        movable = req is not None and req.status in ("pending", "approved") and req.linked_id is None
        return SheetItem(source=i.source, source_id=i.source_id, title=i.title, date=i.date, amount=i.amount,
                         status=i.status, overdue=i.overdue, movable=movable,
                         amount_editable=movable and req.source != "supplier")

    def detail_cell(c) -> SheetCell:
        return SheetCell(fact=c.fact, plan=c.plan, ops=[SheetOp(**o.__dict__) for o in c.ops],
                         items=[item_out(i) for i in c.items])

    rows: list[SheetRow] = []

    def push(row: SheetRow) -> SheetRow:
        rows.append(row)
        return row

    def merge(target: SheetRow, source: SheetRow) -> None:
        for day, c in source.cells.items():
            _add(target.cells, day, c.fact, c.plan)

    def source_tree(prefix: str, title: str, values, include_all: bool, level: int = 0, parent: str | None = None,
                    balance: bool = False, leaf=None, add: str | None = None) -> None:
        """Блок → юрлицо → безнал / наличные → счёт или касса (→ leaf: строки под счётом)."""
        block = push(SheetRow(key=prefix, parent=parent, level=level, kind="block", title=title))
        for entity_id, entity_name in sheet.entities.items():
            entity = SheetRow(key=f"{prefix}:e{entity_id}", parent=prefix, level=level + 1, kind="entity",
                              title=entity_name)
            body: list[SheetRow] = []
            for kind, channel_title in CHANNELS:
                channel = SheetRow(key=f"{entity.key}:{kind}", parent=entity.key, level=level + 2, kind="channel",
                                   title=channel_title)
                channel_body: list[SheetRow] = []
                for src in sheet.sources:
                    if src.entity_id != entity_id or src.kind != kind:
                        continue
                    cells = values(src)
                    if not include_all and not cells:
                        continue
                    real = src.bank_account_id is not None or src.cash_cfo_id is not None
                    ref = BalanceRef(bank_account_id=src.bank_account_id, cash_cfo_id=src.cash_cfo_id) if real else None
                    row = SheetRow(key=f"{channel.key}:{src.key}", parent=channel.key, level=level + 3, kind="source",
                                   title=src.title, cells=cells, source=src.key, entity_id=src.entity_id,
                                   source_ref=ref, balance=ref if balance else None, add=add if real else None)
                    channel_body += [row, *(leaf(row, src) if leaf else [])]
                    merge(channel, row)
                if channel_body:
                    body += [channel, *channel_body]
                    merge(entity, channel)
            if body:
                rows.extend([entity, *body])
                merge(block, entity)

    def pairs(src_dict_fact, src_dict_plan) -> dict:
        cells = {}
        for day in sheet.days:
            fact, plan = src_dict_fact.get(day), src_dict_plan.get(day)
            if fact or plan:
                _add(cells, day, fact or None, plan or None)
        return cells

    def opening(src):
        return {d: SheetCell(fact=p.fact, plan=p.plan) for d, p in src.opening.items()}

    def closing(src):
        return {d: SheetCell(fact=v if d < today else None, plan=v if d >= today else None)
                for d, v in src.closing.items()}

    def article_leaf(direction: str):
        """Под счётом: статья ДДС → контрагент · договор (как в расшифровке платежей)."""
        own_lines = [ln for ln in sheet.lines if ln.direction == direction]

        def leaf(row: SheetRow, src) -> list[SheetRow]:
            articles: dict = {}
            for ln in own_lines:
                if ln.source_key == src.key:
                    articles.setdefault((ln.article_id, ln.article_title), []).append(ln)
            result = []
            for (article_id, article_title), lns in articles.items():
                art = SheetRow(key=f"{row.key}:a{article_id}", parent=row.key, level=row.level + 1, kind="article",
                               title=article_title, source=src.key)
                result.append(art)
                for ln in lns:
                    line_row = SheetRow(
                        key=f"{art.key}:{ln.counterparty_id}:{ln.contract_id}", parent=art.key, level=art.level + 1,
                        kind="line", title=ln.title, subtitle=ln.contract_title, source=src.key,
                        cells={d: detail_cell(c) for d, c in ln.cells.items()})
                    result.append(line_row)
                    merge(art, line_row)
            return result

        return leaf

    source_tree("open", "Деньги на начало", opening, include_all=True, balance=True)
    source_tree("in", "Поступления", lambda src: pairs(src.fact_in, src.plan_in), include_all=True,
                leaf=article_leaf("in"), add="inflow")
    # технические операции: вид → счёт / касса → встречный счёт / касса, сумма со знаком; «+» — плановый перевод
    tech = push(SheetRow(key="tech", parent=None, level=0, kind="block", title="Технические операции", signed=True,
                         add="transfer"))
    for t in calendar_sheet.TECH_TITLES:
        own = [src for src in sheet.sources if t in src.tech]
        if not own:
            continue
        type_row = push(SheetRow(key=f"tech:{t}", parent="tech", level=1, kind="type",
                                 title=calendar_sheet.TECH_TITLES[t], signed=True))
        for src in own:
            row = push(SheetRow(key=f"tech:{t}:{src.key}", parent=type_row.key, level=2, kind="source",
                                title=sheet.titles.get(src.key, src.title), signed=True, source=src.key,
                                entity_id=src.entity_id))
            for corr_key, cells in src.tech[t].items():
                line_row = push(SheetRow(key=f"{row.key}:{corr_key}", parent=row.key, level=3, kind="line",
                                         title=("из: " if any((c.fact or c.plan or 0) > 0 for c in cells.values())
                                                else "в: ") + sheet.titles.get(corr_key, corr_key),
                                         signed=True, source=src.key, cells={d: detail_cell(c) for d, c in cells.items()}))
                merge(row, line_row)
            merge(type_row, row)
        merge(tech, type_row)
    source_tree("out", "Оплаты", lambda src: pairs(src.fact_out, src.plan_out), include_all=False)
    source_tree("close", "Деньги на конец", closing, include_all=True)

    # расшифровка платежей: оплаты по той же логике + статья ДДС → контрагент и договор
    # (поступления раскрыты до контрагента прямо в блоке «Поступления»)
    rows.append(SheetRow(key="details", parent=None, level=0, kind="section", title="Расшифровка платежей"))
    out_keys = {ln.source_key for ln in sheet.lines if ln.direction == "out"}
    out_lines = [ln for ln in sheet.lines if ln.direction == "out"]
    source_tree("det:out", "Оплаты", lambda src: _line_sums(src, out_lines) if src.key in out_keys else {},
                include_all=False, parent="details", level=1, leaf=article_leaf("out"))
    return SheetOut(date_from=date_from, date_to=date_to, today=today, days=sheet.days, rows=rows)


def _line_sums(src, own_lines) -> dict:
    cells: dict = {}
    for ln in own_lines:
        if ln.source_key == src.key:
            for day, c in ln.cells.items():
                _add(cells, day, c.fact, c.plan)
    return cells


@router.get("/organizations/{organization_id}/payment-calendar/sheet", response_model=SheetOut)
def calendar_sheet_view(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
                        legal_entity_id: int | None = None, include_pending: bool = False, with_statements: bool = True):
    return _sheet(db, user, organization_id, date_from, date_to, legal_entity_id, include_pending, with_statements)


def _money_text(c: SheetCell | None) -> Decimal | None:
    if c is None or (c.fact is None and c.plan is None):
        return None
    return (c.fact or Decimal("0.00")) + (c.plan or Decimal("0.00"))


@router.get("/organizations/{organization_id}/payment-calendar/sheet.xlsx")
def calendar_sheet_xlsx(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
                        legal_entity_id: int | None = None, include_pending: bool = False, with_statements: bool = True):
    """Те же строки деревом (уровень — отступ); в ячейке — факт + план одной суммой."""
    data = _sheet(db, user, organization_id, date_from, date_to, legal_entity_id, include_pending, with_statements)
    columns = [XlsxColumn("Показатель", 42), XlsxColumn("Договор", 16)]
    columns += [XlsxColumn(f"{d:%d.%m} {WEEKDAYS[d.weekday()]}", 13, money=True) for d in data.days]
    rows = [(r.level, [r.title, r.subtitle or None, *[_money_text(r.cells.get(d)) for d in data.days]],
             r.kind in ("block", "section")) for r in data.rows]
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


# --- плановые технические операции в календаре (docs/TZ.md, раздел 26, «Детали 16.2») ---


class TransferIn(BaseModel):
    from_bank_account_id: int | None = None
    from_cash_cfo_id: int | None = None
    to_bank_account_id: int | None = None
    to_cash_cfo_id: int | None = None
    day: dt.date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    description: str = Field(default="", max_length=500)
    legal_entity_id: int | None = None  # только касса ↔ касса без юрлица «Компании»


class TransferOut(TransferIn):
    id: int
    legal_entity_id: int


def _transfer_entity(db: Session, organization_id: int, body: TransferIn) -> int:
    for side in ("from", "to"):
        bank, cash = getattr(body, f"{side}_bank_account_id"), getattr(body, f"{side}_cash_cfo_id")
        if (bank is None) == (cash is None):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="Укажите, откуда и куда: расчётный счёт или кассу")
        _scope(db, organization_id, None, bank, cash)
    if (body.from_bank_account_id, body.from_cash_cfo_id) == (body.to_bank_account_id, body.to_cash_cfo_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Откуда и куда — один и тот же счёт")
    for bank_id in (body.from_bank_account_id, body.to_bank_account_id):
        if bank_id is not None:
            return db.get(BankAccount, bank_id).legal_entity_id
    roots = calendar_sheet._root_entity(db, organization_id)
    entity = roots.get(body.from_cash_cfo_id) or roots.get(body.to_cash_cfo_id) or body.legal_entity_id
    if entity is None:
        entities = list(db.scalars(select(LegalEntity.id).where(LegalEntity.organization_id == organization_id)))
        if len(entities) != 1:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите юрлицо перемещения между кассами")
        entity = entities[0]
    _scope(db, organization_id, entity, None, None)
    return entity


def _transfer_out(t: CalendarTransfer) -> TransferOut:
    return TransferOut(id=t.id, legal_entity_id=t.legal_entity_id, from_bank_account_id=t.from_bank_account_id,
                       from_cash_cfo_id=t.from_cash_cfo_id, to_bank_account_id=t.to_bank_account_id,
                       to_cash_cfo_id=t.to_cash_cfo_id, day=t.day, amount=t.amount, description=t.description)


@router.post("/organizations/{organization_id}/calendar-transfers", response_model=TransferOut, status_code=201)
def add_transfer(organization_id: int, body: TransferIn, user: CurrentUser, db: DbSession):
    """Плановый перевод между своими счетами / сдача наличных / снятие в кассу (право проводить)."""
    get_organization_for(db, user, organization_id, Perm.POST)
    t = CalendarTransfer(organization_id=organization_id, legal_entity_id=_transfer_entity(db, organization_id, body),
                         created_by=user.id, **body.model_dump(exclude={"legal_entity_id"}))
    db.add(t)
    db.commit()
    db.refresh(t)
    return _transfer_out(t)


def _get_transfer(db: Session, user, transfer_id: int) -> CalendarTransfer:
    t = db.get(CalendarTransfer, transfer_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Перевод не найден")
    try:
        get_organization_for(db, user, t.organization_id, Perm.POST)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Перевод не найден") from None
        raise
    return t


@router.get("/calendar-transfers/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: int, user: CurrentUser, db: DbSession):
    return _transfer_out(_get_transfer(db, user, transfer_id))


@router.put("/calendar-transfers/{transfer_id}", response_model=TransferOut)
def update_transfer(transfer_id: int, body: TransferIn, user: CurrentUser, db: DbSession):
    t = _get_transfer(db, user, transfer_id)
    t.legal_entity_id = _transfer_entity(db, t.organization_id, body)
    for key, value in body.model_dump(exclude={"legal_entity_id"}).items():
        setattr(t, key, value)
    db.commit()
    db.refresh(t)
    return _transfer_out(t)


@router.delete("/calendar-transfers/{transfer_id}", status_code=204)
def delete_transfer(transfer_id: int, user: CurrentUser, db: DbSession) -> Response:
    db.delete(_get_transfer(db, user, transfer_id))
    db.commit()
    return Response(status_code=204)
