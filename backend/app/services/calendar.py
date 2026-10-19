"""Платёжный календарь (docs/TZ.md, раздел 18, «Детали 8.2»): ожидаемые платежи из заявок, ведомостей,
регламентных операций и плановых оплат инвестпроектов — всё, что ещё не стало фактом в журнале."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Contract,
    InvestmentProject,
    Operation,
    PaymentRequest,
    PayoutSheet,
    Posting,
    RoutineRun,
)
from app.services import reports, routines
from app.services.ledger import select_lines, subtree
from app.services.payroll import sheet_total

ZERO = Decimal("0.00")
SHEET_KINDS = {"advance": "аванс", "salary": "зарплата"}
CASH_CODE = "50"


@dataclass
class Item:
    source: str  # request, payout, routine, investment
    source_id: int
    title: str
    date: dt.date
    direction: str  # in — поступление, out — выплата
    amount: Decimal
    legal_entity_id: int | None = None
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None
    counterparty_id: int | None = None
    article_id: int | None = None
    status: str = ""
    overdue: bool = False


def _months(date_from: dt.date, date_to: dt.date) -> list[tuple[int, int]]:
    result, year, month = [], date_from.year, date_from.month
    while (year, month) <= (date_to.year, date_to.month):
        result.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def _requests(db: Session, organization_id: int, date_from, date_to, include_pending: bool) -> list[Item]:
    statuses = ("approved", "pending") if include_pending else ("approved",)
    return [
        Item("request", r.id, f"Заявка № {r.number}" + (f": {r.description}" if r.description else ""), r.pay_date,
             r.direction, r.amount, r.legal_entity_id, r.bank_account_id, r.cash_cfo_id, r.counterparty_id,
             r.article_id, r.status)
        for r in db.scalars(select(PaymentRequest).where(
            PaymentRequest.organization_id == organization_id, PaymentRequest.status.in_(statuses),
            PaymentRequest.pay_date.between(date_from, date_to)))
    ]


def _payouts(db: Session, organization_id: int, date_from, date_to) -> list[Item]:
    items = []
    for sheet in db.scalars(select(PayoutSheet).where(
            PayoutSheet.organization_id == organization_id, PayoutSheet.status.in_(("draft", "sent")),
            PayoutSheet.pay_date.between(date_from, date_to))):
        amount = sheet_total(db, sheet)
        if amount > 0:
            items.append(Item("payout", sheet.id, f"Ведомость № {sheet.number} ({SHEET_KINDS[sheet.kind]} "
                                                  f"{sheet.month:02d}.{sheet.year})", sheet.pay_date, "out", amount,
                              sheet.legal_entity_id, sheet.bank_account_id, status=sheet.status))
    return items


def _routines(db: Session, organization_id: int, date_from, date_to, money_ids: set[int]) -> list[Item]:
    """Невыполненные месяцы правил; в прогноз — только проводки по счетам денег."""
    items = []
    for year, month in _months(date_from, date_to):
        done = set(db.scalars(select(RoutineRun.rule_id).where(RoutineRun.year == year, RoutineRun.month == month)))
        for rule in routines.due(db, organization_id, year, month):
            day = routines.run_date(rule, year, month)
            if rule.id in done or not date_from <= day <= date_to:
                continue
            for posting in rule.template.get("postings", []):
                inflow = posting["debit_account_id"] in money_ids
                outflow = posting["credit_account_id"] in money_ids
                if inflow == outflow or not posting.get("amount"):
                    continue  # не деньги или перевод между своими счетами
                money, other = (posting["debit"], posting["credit"]) if inflow else (posting["credit"], posting["debit"])
                items.append(Item(
                    "routine", rule.id, f"Регламентная: {rule.name}", day, "in" if inflow else "out",
                    Decimal(posting["amount"]), rule.legal_entity_id, money.get("bank_account"),
                    money.get("cfo") if not money.get("bank_account") else None, other.get("counterparty"),
                    money.get("cash_flow_item") or rule.template.get("article_id"), "rule"))
    return items


def _investments(db: Session, organization_id: int, date_from, date_to) -> list[Item]:
    """Плановые оплаты проектов из актуального утверждённого бюджета минус заявки по договорам проекта."""
    months = [f"{y}-{m:02d}" for y, m in _months(date_from, date_to)]
    versions = reports.actual_plan_versions(db, organization_id, months, "cf")
    items = []
    for key, version_id in versions.items():
        start = dt.date(int(key[:4]), int(key[5:]), 1)
        end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        rows = db.execute(
            select(Operation.plan_project_id, Operation.legal_entity_id, Operation.article_id,
                   func.sum(Posting.amount))
            .join(Posting, Posting.operation_id == Operation.id)
            .where(Operation.budget_version_id == version_id, Operation.plan_kind == "cf",
                   Operation.plan_project_id.is_not(None), Operation.op_date.between(start, end))
            .group_by(Operation.plan_project_id, Operation.legal_entity_id, Operation.article_id)).all()
        for project_id, entity_id, article_id, amount in rows:
            requested = db.scalar(
                select(func.coalesce(func.sum(PaymentRequest.amount), 0))
                .join(Contract, Contract.id == PaymentRequest.contract_id)
                .where(PaymentRequest.organization_id == organization_id, Contract.project_id == project_id,
                       PaymentRequest.direction == "out", PaymentRequest.status.in_(("pending", "approved", "paid")),
                       PaymentRequest.pay_date.between(start, end)))
            rest = Decimal(amount) - Decimal(requested)
            if rest <= 0:
                continue
            project = db.get(InvestmentProject, project_id)
            # оплата планируется на месяц: 1-е число, а если период начинается позже — его первый день
            items.append(Item("investment", project_id, f"Инвестпроект: {project.name}", max(start, date_from), "out",
                              rest + ZERO, entity_id, article_id=article_id, status="plan"))
    return items


def expected(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date, *,
             include_pending: bool = False, legal_entity_id: int | None = None,
             today: dt.date | None = None) -> list[Item]:
    """Ожидаемые платежи за период по дате, затем по источнику."""
    today = today or dt.date.today()
    money_ids = set(db.scalars(subtree(reports.money_account_ids(db))))
    items = (_requests(db, organization_id, date_from, date_to, include_pending)
             + _payouts(db, organization_id, date_from, date_to)
             + _routines(db, organization_id, date_from, date_to, money_ids)
             + _investments(db, organization_id, date_from, date_to))
    if legal_entity_id is not None:
        items = [i for i in items if i.legal_entity_id == legal_entity_id]
    order = {"request": 0, "payout": 1, "routine": 2, "investment": 3}
    for item in items:
        item.overdue = item.date < today
    return sorted(items, key=lambda i: (i.date, order[i.source], i.source_id))


# --- календарь по дням (docs/TZ.md, раздел 18, «Детали 8.3») ---

LOOKBACK_DAYS = 366  # просроченные ожидаемые платежи ищутся не дальше года назад


@dataclass
class Scope:
    """Чьи деньги: вся организация, юрлицо, один расчётный счёт или одна касса."""

    legal_entity_id: int | None = None
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None

    @property
    def one_source(self) -> bool:
        return self.bank_account_id is not None or self.cash_cfo_id is not None

    def accepts(self, item: Item) -> bool:
        if self.legal_entity_id is not None and item.legal_entity_id != self.legal_entity_id:
            return False
        if self.bank_account_id is not None:
            return item.bank_account_id == self.bank_account_id
        if self.cash_cfo_id is not None:
            return item.cash_cfo_id == self.cash_cfo_id
        return True


@dataclass
class FactEntry:
    operation_id: int
    number: int
    description: str
    inflow: Decimal
    outflow: Decimal


@dataclass
class Day:
    date: dt.date
    opening: Decimal
    fact_in: Decimal = ZERO
    fact_out: Decimal = ZERO
    expected_in: Decimal = ZERO
    expected_out: Decimal = ZERO
    closing: Decimal = ZERO
    fact: list[FactEntry] | None = None
    expected: list[Item] | None = None

    @property
    def inflow(self) -> Decimal:
        return self.fact_in + self.expected_in

    @property
    def outflow(self) -> Decimal:
        return self.fact_out + self.expected_out


@dataclass
class Grid:
    opening: Decimal
    days: list[Day]
    first_gap: Day | None


def _money_lines(db: Session, organization_id: int, scope: Scope, **conditions):
    """Строки по счетам денег (50, 51, 52, 55) в пределах scope — те же, что в «Деньгах» и ДДС."""
    roots = reports.money_account_ids(db)
    analytics = {}
    if scope.bank_account_id is not None:
        analytics["bank_account"] = scope.bank_account_id
    if scope.cash_cfo_id is not None:
        analytics["cfo"] = scope.cash_cfo_id
        roots = list(db.scalars(select(Account.id).where(Account.code == CASH_CODE)))
    if not scope.one_source:
        # переводы между своими счетами внутри организации или юрлица — не поток
        conditions["exclude_corr_account_ids"] = roots
    return select_lines(organization_id, account_ids=roots, legal_entity_id=scope.legal_entity_id,
                        analytics=analytics or None, **conditions)


def _balance(db: Session, organization_id: int, scope: Scope, on: dt.date) -> Decimal:
    """Остаток по журналу на конец дня on (переводы внутри scope на остаток не влияют)."""
    lines = _money_lines(db, organization_id, scope, date_to=on).subquery()
    rows = db.execute(select(lines.c.side, func.coalesce(func.sum(lines.c.amount_rub), 0)).group_by(lines.c.side)).all()
    return sum((Decimal(v) if side == "debit" else -Decimal(v) for side, v in rows), ZERO) + ZERO


def _fact(db: Session, organization_id: int, scope: Scope, date_from: dt.date, date_to: dt.date) -> dict:
    """Факт по дням: {дата: [FactEntry по операциям]}."""
    lines = _money_lines(db, organization_id, scope, date_from=date_from, date_to=date_to).subquery()
    rows = db.execute(
        select(lines.c.op_date, lines.c.operation_id, Operation.number, Operation.description, lines.c.side,
               func.sum(lines.c.amount_rub))
        .join(Operation, Operation.id == lines.c.operation_id)
        .group_by(lines.c.op_date, lines.c.operation_id, Operation.number, Operation.description, lines.c.side)
        .order_by(lines.c.op_date, Operation.number)).all()
    result: dict[dt.date, dict[int, FactEntry]] = {}
    for day, op_id, number, description, side, amount in rows:
        entry = result.setdefault(day, {}).setdefault(op_id, FactEntry(op_id, number, description, ZERO, ZERO))
        if side == "debit":
            entry.inflow += Decimal(amount)
        else:
            entry.outflow += Decimal(amount)
    return {day: list(entries.values()) for day, entries in result.items()}


def grid(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date, scope: Scope, *,
         include_pending: bool = False, today: dt.date | None = None) -> Grid:
    """Дни периода: факт за прошедшие дни и сегодня, ожидаемые платежи — с сегодняшнего дня;
    просроченные ожидаемые — на сегодня (или на первый день будущего периода)."""
    today = today or dt.date.today()
    forecast_from = max(today, date_from)
    items = []
    if forecast_from <= date_to:
        found = expected(db, organization_id, today - dt.timedelta(days=LOOKBACK_DAYS), date_to,
                         include_pending=include_pending, today=today)
        items = [i for i in found if scope.accepts(i)]
    for item in items:
        item.overdue = item.date < today
    if date_from <= today:
        opening = _balance(db, organization_id, scope, date_from - dt.timedelta(days=1))
    else:  # будущий период: остаток сегодня плюс ожидаемое до начала периода
        opening = _balance(db, organization_id, scope, today)
        opening += sum((i.amount if i.direction == "in" else -i.amount
                        for i in items if i.date < date_from), ZERO)
    fact = _fact(db, organization_id, scope, date_from, min(date_to, today)) if date_from <= today else {}

    days, balance, first_gap = [], opening, None
    day = date_from
    by_day: dict[dt.date, list[Item]] = {}
    for item in items:
        effective = max(item.date, forecast_from)
        if effective >= date_from:
            by_day.setdefault(effective, []).append(item)
    while day <= date_to:
        row = Day(day, balance, fact=fact.get(day, []), expected=by_day.get(day, []))
        row.fact_in = sum((e.inflow for e in row.fact), ZERO)
        row.fact_out = sum((e.outflow for e in row.fact), ZERO)
        row.expected_in = sum((i.amount for i in row.expected if i.direction == "in"), ZERO)
        row.expected_out = sum((i.amount for i in row.expected if i.direction == "out"), ZERO)
        balance = row.closing = balance + row.inflow - row.outflow
        if first_gap is None and balance < 0:
            first_gap = row
        days.append(row)
        day += dt.timedelta(days=1)
    return Grid(opening, days, first_gap)
