"""«Деньги» и «Начальные остатки» (docs/TZ.md, раздел 10). Просмотр — право VIEW."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, select

from app.api.access import branch_ids, cfo_scope, forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Operation
from app.services.bank_import import import_statement
from app.services.bank_statement import StatementError
from app.services.journal import JournalError
from app.services.money import money_movements, money_overview, opening_balance_check

router = APIRouter(prefix="/api", tags=["Деньги"])


class MoneyRow(BaseModel):
    kind: str
    bank_account_id: int | None
    cfo_id: int | None
    name: str
    number: str | None
    legal_entity_id: int | None
    currency: str
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal


class MoneyOverview(BaseModel):
    date_from: dt.date
    date_to: dt.date
    rows: list[MoneyRow]
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal


class MovementRow(BaseModel):
    operation_id: int
    number: int
    op_date: dt.date
    description: str
    counterparty_id: int | None
    cash_flow_item: int | None
    inflow: Decimal | None
    outflow: Decimal | None
    balance: Decimal


class MovementsOut(BaseModel):
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    rows: list[MovementRow]


class OpeningSummary(BaseModel):
    opening_balance_date: dt.date | None
    account_00_balance: Decimal  # должно быть 0
    posted: int
    drafts: int


def _check_period(date_from: dt.date, date_to: dt.date) -> None:
    if date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Начало периода позже его конца")


@router.get("/organizations/{organization_id}/money", response_model=MoneyOverview)
def get_money(organization_id: int, date_from: dt.date, date_to: dt.date, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    _check_period(date_from, date_to)
    rows = money_overview(db, organization_id, date_from, date_to)
    scope = cfo_scope(db, user, organization_id)
    if scope is not None:
        # руководитель ЦФО видит только кассу своей ветки
        branch = branch_ids(db, scope)
        rows = [r for r in rows if r["kind"] == "cash" and r["cfo_id"] in branch]
    total = {k: sum((r[k] for r in rows), Decimal("0.00")) for k in ("opening", "inflow", "outflow", "closing")}
    return MoneyOverview(date_from=date_from, date_to=date_to, rows=rows, **total)


@router.get("/organizations/{organization_id}/money/movements", response_model=MovementsOut)
def get_movements(
    organization_id: int, date_from: dt.date, date_to: dt.date, user: CurrentUser, db: DbSession,
    bank_account_id: int | None = None, cfo_id: int | None = None,
):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    _check_period(date_from, date_to)
    scope = cfo_scope(db, user, organization_id)
    if scope is not None and (bank_account_id is not None or cfo_id not in branch_ids(db, scope)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Счёт не найден")
    if (bank_account_id is None) == (cfo_id is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите расчётный счёт или ЦФО кассы"
        )
    result = money_movements(
        db, organization_id, date_from, date_to, bank_account_id=bank_account_id, cfo_id=cfo_id
    )
    return MovementsOut(
        opening=result.opening, inflow=result.inflow, outflow=result.outflow,
        closing=result.closing, rows=result.rows,
    )


@router.get("/organizations/{organization_id}/opening-balances", response_model=OpeningSummary)
def get_opening_summary(organization_id: int, user: CurrentUser, db: DbSession):
    organization = get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    counts = dict(db.execute(
        select(Operation.status, func.count())
        .where(Operation.organization_id == organization_id, Operation.is_opening)
        .group_by(Operation.status)
    ).all())
    return OpeningSummary(
        opening_balance_date=organization.opening_balance_date,
        account_00_balance=opening_balance_check(db, organization_id),
        posted=counts.get("posted", 0),
        drafts=counts.get("draft", 0),
    )


# --- Загрузка банковской выписки ---

MAX_STATEMENT_BYTES = 10 * 1024 * 1024


class AccountCheckOut(BaseModel):
    bank_account_id: int
    name: str
    number: str
    statement_opening: Decimal | None
    statement_closing: Decimal | None
    statement_in: Decimal
    statement_out: Decimal
    documents_in: Decimal
    documents_out: Decimal
    totals_match: bool
    system_opening: Decimal
    system_closing: Decimal


class ImportOut(BaseModel):
    date_from: dt.date | None
    date_to: dt.date | None
    created: list[int]
    incomplete: list[int]
    skipped_duplicates: int
    skipped_before_opening: int
    new_counterparties: list[str]
    accounts: list[AccountCheckOut]


@router.post("/organizations/{organization_id}/bank-statements", response_model=ImportOut)
async def upload_statement(organization_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Тело запроса — файл выписки как есть (kl_to_1c.txt). Платежи становятся черновиками."""
    get_organization_for(db, user, organization_id, Perm.DRAFT)
    forbid_scoped(db, user, organization_id)
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл выписки пуст")
    if len(raw) > MAX_STATEMENT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    try:
        # разбор и запись — обычный (синхронный) код, выполняем вне цикла событий
        result = await run_in_threadpool(import_statement, db, organization_id, user, raw)
    except (StatementError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return ImportOut(
        date_from=result.date_from, date_to=result.date_to, created=result.created,
        incomplete=result.incomplete, skipped_duplicates=result.skipped_duplicates,
        skipped_before_opening=result.skipped_before_opening, new_counterparties=result.new_counterparties,
        accounts=[AccountCheckOut(**{**a.__dict__, "totals_match": a.totals_match}) for a in result.accounts],
    )
