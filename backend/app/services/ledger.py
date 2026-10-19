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

from app.models import Account, Cfo, Operation, Posting
from app.models.journal import ANALYTIC_COLUMNS, analytic_column


def _side(side: str, account_col, corr_col):
    columns = [
        Posting.id.label("posting_id"),
        Posting.operation_id.label("operation_id"),
        Operation.organization_id.label("organization_id"),
        Operation.legal_entity_id.label("legal_entity_id"),
        Operation.op_date.label("op_date"),
        Operation.status.label("status"),
        Operation.is_opening.label("is_opening"),
        Operation.budget_version_id.label("budget_version_id"),
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


def subtree(account_ids: list[int]) -> Select:
    """id счетов вместе со всеми их субсчетами."""
    root_codes = select(Account.code).where(Account.id.in_(account_ids)).subquery()
    return select(Account.id).join(
        root_codes, or_(Account.code == root_codes.c.code, Account.code.like(root_codes.c.code + ".%"))
    )


def select_lines(
    organization_id: int,
    *,
    statuses: tuple[str, ...] = ("posted",),
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    legal_entity_id: int | None = None,
    account_id: int | None = None,
    account_ids: list[int] | None = None,
    side: str | None = None,
    analytics: dict[str, int | None] | None = None,
    cfo_branch: int | None = None,
    exclude_corr_account_ids: list[int] | None = None,
    opening: bool | None = None,
    budget_version_ids: list[int] | None = None,
    executor_ids: list[int] | None = None,
    analytics_in: dict[str, list[int]] | None = None,
) -> Select:
    """Строки проводок по условиям. Счёт (account_id или любой из account_ids) отбирается
    вместе со всеми своими субсчетами. Аналитика со значением None — «не указана».
    cfo_branch — ЦФО вместе со всеми подчинёнными (строки без ЦФО не попадают).
    exclude_corr_account_ids — без строк, где корреспондирующий счёт — один из этих (с субсчетами).
    opening — True: только ввод начальных остатков, False: без него, None: все.
    budget_version_ids — плановые операции этих версий бюджета (вместе со statuses=("plan",)).
    executor_ids — только строки с одним из этих ЦФУ.
    analytics_in — аналитика из списка значений (например, все ОС инвестпроекта); пустой список — ничего.

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
    if opening is not None:
        query = query.where(lines.c.is_opening == opening)
    if budget_version_ids is not None:
        query = query.where(lines.c.budget_version_id.in_(budget_version_ids))
    roots = ([account_id] if account_id is not None else []) + list(account_ids or [])
    if roots:
        query = query.where(lines.c.account_id.in_(subtree(roots)))
    if exclude_corr_account_ids:
        query = query.where(or_(lines.c.corr_account_id.is_(None),
                                lines.c.corr_account_id.not_in(subtree(exclude_corr_account_ids))))
    for kind, value in (analytics or {}).items():
        column = getattr(lines.c, kind)
        query = query.where(column.is_(None) if value is None else and_(column == value))
    for kind, values in (analytics_in or {}).items():
        query = query.where(getattr(lines.c, kind).in_(values))
    if executor_ids is not None:
        query = query.where(lines.c.executor.in_(executor_ids))
    if cfo_branch is not None:
        root = select(Cfo.path).where(Cfo.id == cfo_branch).scalar_subquery()
        query = query.where(lines.c.cfo.in_(select(Cfo.id).where(Cfo.path.op("<@")(root))))
    return query
