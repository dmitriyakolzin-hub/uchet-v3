"""Платёжный календарь (docs/TZ.md, раздел 18, «Детали 8.2»): ожидаемые платежи из заявок, ведомостей,
регламентных операций и плановых оплат инвестпроектов — всё, что ещё не стало фактом в журнале."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Contract,
    InvestmentProject,
    Operation,
    PaymentRequest,
    PayoutSheet,
    Posting,
    RoutineRun,
)
from app.services import reports, routines
from app.services.ledger import subtree
from app.services.payroll import sheet_total

ZERO = Decimal("0.00")
SHEET_KINDS = {"advance": "аванс", "salary": "зарплата"}


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
