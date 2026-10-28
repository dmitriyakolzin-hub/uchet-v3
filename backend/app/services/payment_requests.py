"""Заявки на оплату (docs/TZ.md, раздел 18, «Детали 8.1»): проверка, контроль бюджета ДДС, поиск по платежу выписки."""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.models import (
    Article,
    BankAccount,
    Cfo,
    Contract,
    Counterparty,
    Executor,
    LegalEntity,
    Operation,
    PaymentRequest,
)
from app.services import reports
from app.services.articles import is_group
from app.services.ledger import select_lines


def _valid_inn(inn: str) -> bool:
    return bool(re.fullmatch(r"\d{10}|\d{12}", inn or "")) and set(inn) != {"0"}

ZERO = Decimal("0.00")
MATCH_DAYS = 14  # платёж выписки ищет заявку не дальше стольких дней от даты оплаты
OPEN_STATUSES = ("pending", "approved")  # неоплаченные заявки, которые уже занимают бюджет
STATUS_TITLES = {"draft": "черновик", "pending": "на согласовании", "approved": "согласована",
                 "rejected": "отклонена", "paid": "оплачена"}


class RequestError(Exception):
    pass


def next_number(db: Session, organization_id: int) -> int:
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    current = db.scalar(select(func.max(PaymentRequest.number)).where(PaymentRequest.organization_id == organization_id))
    return (current or 0) + 1


def validate(db: Session, req: PaymentRequest, branch: set[int] | None) -> None:
    """Ссылки — на записи своей организации; branch — ЦФО, которыми ограничен пользователь (None — без ограничения)."""
    org = req.organization_id

    def own(model, value, title):
        record = db.get(model, value) if value is not None else None
        if value is not None and (record is None or record.organization_id != org):
            raise RequestError(f"{title} не найден(а) в этой организации")
        return record

    entity = own(LegalEntity, req.legal_entity_id, "Юрлицо")
    if entity is None:
        raise RequestError("Не указано юрлицо")
    bank = own(BankAccount, req.bank_account_id, "Расчётный счёт")
    if bank is not None and bank.legal_entity_id != entity.id:
        raise RequestError("Расчётный счёт должен принадлежать юрлицу заявки")
    own(Cfo, req.cash_cfo_id, "Касса")
    if req.bank_account_id is not None and req.cash_cfo_id is not None:
        raise RequestError("Укажите либо расчётный счёт, либо кассу")
    own(Counterparty, req.counterparty_id, "Контрагент")
    contract = own(Contract, req.contract_id, "Договор")
    if contract is not None:
        if contract.counterparty_id != req.counterparty_id:
            raise RequestError("Договор должен быть с контрагентом заявки")
        if contract.legal_entity_id != entity.id:
            raise RequestError("Договор должен быть заключён юрлицом заявки")
    own(Cfo, req.cfo_id, "ЦФО")
    own(Executor, req.executor_id, "ЦФУ")
    if req.article_id is not None:
        article = db.get(Article, req.article_id)
        direction = "outflow" if req.direction == "out" else "inflow"
        if article is None or article.organization_id != org or not article.is_cash_flow or article.is_transfer \
                or article.cash_flow_direction != direction:
            raise RequestError("Статья должна быть статьёй ДДС " + ("выплат" if req.direction == "out" else "поступлений"))
        if is_group(db, article.id):
            raise RequestError(f"«{article.name}» — группа статей, выберите подстатью")
    if branch is not None and req.cfo_id not in branch:
        raise RequestError("Укажите ЦФО своей ветки")


@dataclass
class BudgetCheck:
    month: str
    plan: Decimal | None  # None — нет утверждённого плана ДДС на этот месяц
    fact: Decimal
    requested: Decimal  # другие неоплаченные заявки месяца по статье
    remaining: Decimal | None  # остаток до этой заявки
    over: Decimal  # насколько заявка больше остатка (0 — в пределах бюджета)


def _month_bounds(key: str) -> tuple[dt.date, dt.date]:
    start = dt.date(int(key[:4]), int(key[5:]), 1)
    end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return start, end


def _outflow(db: Session, organization_id: int, key: str, article_ids: list[int], **conditions) -> dict[int, Decimal]:
    """Выплаты по статьям за месяц по счетам денег: кредит − дебет."""
    start, end = _month_bounds(key)
    lines = select_lines(organization_id, date_from=start, date_to=end, account_ids=reports.money_account_ids(db),
                         analytics_in={"cash_flow_item": article_ids}, **conditions).subquery()
    rows = db.execute(select(lines.c.cash_flow_item, lines.c.side, func.sum(lines.c.amount_rub))
                      .group_by(lines.c.cash_flow_item, lines.c.side)).all()
    result: dict[int, Decimal] = {}
    for article_id, side, amount in rows:
        value = Decimal(amount) if side == "credit" else -Decimal(amount)
        result[article_id] = result.get(article_id, ZERO) + value
    return result


def budget_checks(db: Session, organization_id: int, requests: list[PaymentRequest]) -> dict[int, BudgetCheck]:
    """Контроль бюджета ДДС для выплат со статьёй: план месяца − факт − другие неоплаченные заявки."""
    targets = [r for r in requests if r.direction == "out" and r.article_id is not None and r.status != "paid"]
    months = sorted({f"{r.pay_date:%Y-%m}" for r in targets})
    if not months:
        return {}
    versions = reports.actual_plan_versions(db, organization_id, months, "cf")
    articles_by_month: dict[str, list[int]] = {}
    for r in targets:
        articles_by_month.setdefault(f"{r.pay_date:%Y-%m}", []).append(r.article_id)
    plan, fact, requested = {}, {}, {}
    for key, article_ids in articles_by_month.items():
        article_ids = sorted(set(article_ids))
        fact[key] = _outflow(db, organization_id, key, article_ids)
        if key in versions:
            plan[key] = _outflow(db, organization_id, key, article_ids, statuses=("plan",),
                                 budget_version_ids=[versions[key]])
        start, end = _month_bounds(key)
        requested[key] = list(db.execute(
            select(PaymentRequest.id, PaymentRequest.article_id, PaymentRequest.amount).where(
                PaymentRequest.organization_id == organization_id, PaymentRequest.direction == "out",
                PaymentRequest.status.in_(OPEN_STATUSES), PaymentRequest.article_id.in_(article_ids),
                PaymentRequest.pay_date.between(start, end))).all())
    result = {}
    for r in targets:
        key = f"{r.pay_date:%Y-%m}"
        others = sum((amount for rid, aid, amount in requested[key] if aid == r.article_id and rid != r.id), ZERO)
        spent = fact[key].get(r.article_id, ZERO)
        if key in plan:
            limit = plan[key].get(r.article_id, ZERO)
            remaining = limit - spent - others
            over = max(ZERO, r.amount - max(remaining, ZERO))
        else:
            limit = remaining = None
            over = ZERO
        result[r.id] = BudgetCheck(month=key, plan=limit, fact=spent, requested=others, remaining=remaining, over=over)
    return result


def find_for_payment(db: Session, organization_id: int, bank: BankAccount, direction: str, amount: Decimal,
                     inn: str, on_date: dt.date) -> PaymentRequest | None:
    """Согласованная заявка под платёж выписки: та же сумма, счёт (или не указан, но то же юрлицо),
    контрагент по ИНН, дата не дальше MATCH_DAYS дней; из нескольких — ближайшая по дате."""
    if not _valid_inn(inn):
        return None
    window = dt.timedelta(days=MATCH_DAYS)
    return db.scalar(
        select(PaymentRequest).join(Counterparty, Counterparty.id == PaymentRequest.counterparty_id).where(
            PaymentRequest.organization_id == organization_id, PaymentRequest.status == "approved",
            PaymentRequest.direction == direction, PaymentRequest.amount == amount,
            PaymentRequest.legal_entity_id == bank.legal_entity_id,
            or_(PaymentRequest.bank_account_id == bank.id,
                (PaymentRequest.bank_account_id.is_(None)) & (PaymentRequest.cash_cfo_id.is_(None))),
            PaymentRequest.pay_date.between(on_date - window, on_date + window),
            Counterparty.inn == inn,
        ).order_by(func.abs(PaymentRequest.pay_date - on_date), PaymentRequest.number).limit(1)
    )


def mark_paid(db: Session, req: PaymentRequest, paid_on: dt.date, operation_id: int | None = None,
              confirmed: bool = False) -> None:
    if req.status != "approved":
        raise RequestError(f"Заявка {STATUS_TITLES[req.status]} — отметить оплату можно только у согласованной")
    if operation_id is not None:
        op = db.get(Operation, operation_id)
        if op is None or op.organization_id != req.organization_id or op.budget_version_id is not None:
            raise RequestError("Операция журнала не найдена")
    req.status, req.paid_on, req.operation_id, req.confirmed_by_statement = "paid", paid_on, operation_id, confirmed
