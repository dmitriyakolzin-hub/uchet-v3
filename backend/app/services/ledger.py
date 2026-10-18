"""Единая функция отбора проводок (CLAUDE.md, правило 6).

Журнал и все отчёты (ОСВ, ДДС, ОПиУ, баланс, план/факт) отбирают проводки только через
ledger_lines() / select_lines(), поэтому расшифровка всегда совпадает с цифрой отчёта.

Каждая проводка даёт две строки — по дебету и по кредиту:
  account_id — счёт этой стороны, corr_account_id — корреспондирующий счёт,
  side — 'debit' / 'credit', amount_rub — сумма в рублях (всегда положительная),
  колонки аналитики этой стороны названы по виду: counterparty, contract, cfo, cash_flow_item …
"""

import datetime as dt

from sqlalchemy import Select, and_, literal, or_, select, union_all
from sqlalchemy.sql import Subquery

from app.models import Account, Operation, Posting
from app.models.journal import ANALYTIC_COLUMNS, analytic_column


def _side(side: str, account_col, corr_col):
    columns = [
        Posting.id.label("posting_id"),
        Posting.operation_id.label("operation_id"),
        Operation.organization_id.label("organization_id"),
        Operation.legal_entity_id.label("legal_entity_id"),
        Operation.op_date.label("op_date"),
        Operation.status.label("status"),
        account_col.label("account_id"),
        corr_col.label("corr_account_id"),
        literal(side).label("side"),
        Posting.amount_rub.label("amount_rub"),
        Posting.amount.label("amount"),
        Posting.currency_id.label("currency_id"),
        Posting.quantity.label("quantity"),
    ]
    columns += [getattr(Posting, analytic_column(side, kind)).label(kind) for kind in ANALYTIC_COLUMNS]
    return select(*columns).join(Operation, Operation.id == Posting.operation_id)


def ledger_lines() -> Subquery:
    """Все строки проводок (обе стороны) — без фильтров."""
    return union_all(
        _side("debit", Posting.debit_account_id, Posting.credit_account_id),
        _side("credit", Posting.credit_account_id, Posting.debit_account_id),
    ).subquery("ledger_lines")


def select_lines(
    organization_id: int,
    *,
    statuses: tuple[str, ...] = ("posted",),
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    legal_entity_id: int | None = None,
    account_id: int | None = None,
    side: str | None = None,
    analytics: dict[str, int] | None = None,
) -> Select:
    """Строки проводок по условиям. Счёт отбирается вместе со всеми своими субсчетами.

    По умолчанию — только проведённые операции (так считают отчёты).
    """
    lines = ledger_lines()
    query = select(lines).where(lines.c.organization_id == organization_id, lines.c.status.in_(statuses))
    if date_from is not None:
        query = query.where(lines.c.op_date >= date_from)
    if date_to is not None:
        query = query.where(lines.c.op_date <= date_to)
    if legal_entity_id is not None:
        query = query.where(lines.c.legal_entity_id == legal_entity_id)
    if side is not None:
        query = query.where(lines.c.side == side)
    if account_id is not None:
        root = select(Account.code).where(Account.id == account_id).scalar_subquery()
        subtree = select(Account.id).where(or_(Account.code == root, Account.code.like(root + ".%")))
        query = query.where(lines.c.account_id.in_(subtree))
    for kind, value in (analytics or {}).items():
        query = query.where(and_(getattr(lines.c, kind) == value))
    return query
