"""«Деньги» и «Начальные остатки» (docs/TZ.md, раздел 10). Просмотр — право VIEW."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Operation
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
    total = {k: sum((r[k] for r in rows), Decimal("0.00")) for k in ("opening", "inflow", "outflow", "closing")}
    return MoneyOverview(date_from=date_from, date_to=date_to, rows=rows, **total)


@router.get("/organizations/{organization_id}/money/movements", response_model=MovementsOut)
def get_movements(
    organization_id: int, date_from: dt.date, date_to: dt.date, user: CurrentUser, db: DbSession,
    bank_account_id: int | None = None, cfo_id: int | None = None,
):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    _check_period(date_from, date_to)
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
