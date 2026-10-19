"""Основные средства (docs/TZ.md, раздел 14, «Детали 4.4»).

Карточки — справочник организации (app/api/references.py, путь fixed-assets). Здесь — ведомость
остатков, ввод в эксплуатацию и начисление амортизации (право «проведение»: финансовый директор
и администратор). Руководителю ЦФО раздел недоступен.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import DepreciationRun, FixedAsset, LegalEntity, Operation, User
from app.schemas.reference import FixedAssetOut
from app.services import fixed_assets as fa

router = APIRouter(prefix="/api", tags=["Основные средства"])

Year = Annotated[int, Field(ge=2000, le=2100)]
Month = Annotated[int, Field(ge=1, le=12)]


def _call(db: Session, func, *args):
    try:
        result = func(*args)
    except fa.AssetError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return result


def _organization(db: Session, user: User, organization_id: int, perm: Perm) -> None:
    get_organization_for(db, user, organization_id, perm)
    forbid_scoped(db, user, organization_id)


def _asset(db: Session, user: User, asset_id: int) -> FixedAsset:
    asset = db.get(FixedAsset, asset_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Основное средство не найдено")
    if asset is None:
        raise not_found
    try:
        _organization(db, user, asset.organization_id, Perm.POST)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return asset


class RegisterRow(BaseModel):
    id: int
    cost: Decimal  # сальдо 01
    depreciation: Decimal  # сальдо 02
    residual: Decimal
    investment: Decimal  # сальдо 08
    monthly: Decimal  # амортизация в месяц


@router.get("/organizations/{organization_id}/fixed-assets/register", response_model=list[RegisterRow])
def register(organization_id: int, user: CurrentUser, db: DbSession, on: dt.date | None = None):
    """Стоимость, накопленная амортизация и остаточная стоимость каждого ОС на дату (по журналу)."""
    _organization(db, user, organization_id, Perm.VIEW)
    found = fa.balances(db, organization_id, on)
    rows = []
    for asset in db.scalars(select(FixedAsset).where(FixedAsset.organization_id == organization_id)):
        b = found.get(asset.id, fa.Balance())
        rows.append(RegisterRow(id=asset.id, cost=b.cost, depreciation=b.depreciation, residual=b.residual,
                                investment=b.investment, monthly=fa.monthly_amount(asset)))
    return rows


class CommissionIn(BaseModel):
    commissioned_on: dt.date


@router.post("/fixed-assets/{asset_id}/commission", response_model=FixedAssetOut)
def commission(asset_id: int, body: CommissionIn, user: CurrentUser, db: DbSession):
    asset = _asset(db, user, asset_id)
    _call(db, fa.commission, db, asset, body.commissioned_on, user)
    db.refresh(asset)
    return asset


@router.post("/fixed-assets/{asset_id}/uncommission", response_model=FixedAssetOut)
def uncommission(asset_id: int, user: CurrentUser, db: DbSession):
    asset = _asset(db, user, asset_id)
    _call(db, fa.uncommission, db, asset)
    db.refresh(asset)
    return asset


# --- Амортизация ---


class PreviewRow(BaseModel):
    asset_id: int
    asset_name: str
    inventory_number: str
    legal_entity_id: int
    legal_entity_name: str
    residual_before: Decimal
    amount: Decimal


class RunOut(BaseModel):
    id: int
    legal_entity_id: int
    legal_entity_name: str
    year: int
    month: int
    operation_id: int | None
    operation_number: int | None
    total: Decimal
    created_at: dt.datetime


def _run_out(db: Session, run: DepreciationRun) -> RunOut:
    op = db.get(Operation, run.operation_id) if run.operation_id else None
    return RunOut(id=run.id, legal_entity_id=run.legal_entity_id,
                  legal_entity_name=db.get(LegalEntity, run.legal_entity_id).short_name,
                  year=run.year, month=run.month, operation_id=run.operation_id,
                  operation_number=op.number if op else None, total=fa.run_total(db, run), created_at=run.created_at)


@router.get("/organizations/{organization_id}/depreciation/preview", response_model=list[PreviewRow])
def preview(organization_id: int, year: Year, month: Month, user: CurrentUser, db: DbSession):
    """Что будет начислено за месяц; юрлица, где начисление уже есть, не показываются."""
    _organization(db, user, organization_id, Perm.POST)
    rows = []
    for entity in db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)
                             .order_by(LegalEntity.short_name)):
        if db.scalar(select(DepreciationRun.id).where(
                DepreciationRun.legal_entity_id == entity.id, DepreciationRun.year == year,
                DepreciationRun.month == month)):
            continue
        for line in fa.preview(db, organization_id, entity.id, year, month):
            rows.append(PreviewRow(asset_id=line.asset.id, asset_name=line.asset.name,
                                   inventory_number=line.asset.inventory_number, legal_entity_id=entity.id,
                                   legal_entity_name=entity.short_name, residual_before=line.residual_before,
                                   amount=line.amount))
    return rows


@router.get("/organizations/{organization_id}/depreciation-runs", response_model=list[RunOut])
def list_runs(organization_id: int, user: CurrentUser, db: DbSession, year: int | None = None):
    _organization(db, user, organization_id, Perm.POST)
    query = select(DepreciationRun).where(DepreciationRun.organization_id == organization_id)
    if year is not None:
        query = query.where(DepreciationRun.year == year)
    runs = db.scalars(query.order_by(DepreciationRun.year.desc(), DepreciationRun.month.desc(),
                                     DepreciationRun.legal_entity_id))
    return [_run_out(db, r) for r in runs]


class DepreciateIn(BaseModel):
    year: Year
    month: Month


@router.post("/organizations/{organization_id}/depreciation-runs", response_model=list[RunOut], status_code=201)
def depreciate(organization_id: int, body: DepreciateIn, user: CurrentUser, db: DbSession):
    """Начислить амортизацию за месяц по всем юрлицам организации."""
    _organization(db, user, organization_id, Perm.POST)
    runs = _call(db, fa.depreciate_all, db, organization_id, body.year, body.month, user)
    return [_run_out(db, r) for r in runs]


@router.delete("/depreciation-runs/{run_id}", status_code=204)
def undo(run_id: int, user: CurrentUser, db: DbSession) -> None:
    run = db.get(DepreciationRun, run_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Начисление не найдено")
    if run is None:
        raise not_found
    try:
        _organization(db, user, run.organization_id, Perm.POST)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    _call(db, fa.undo_depreciation, db, run)
