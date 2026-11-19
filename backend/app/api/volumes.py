"""План объёмов и расчёт плана ОПиУ по цепочке — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.6»).

Смотреть — право просмотра (руководителю ЦФО — его ветка); вводить объёмы — право черновиков (по ветке, утверждённый
ЦФО — только администратор и фин. директор); «Рассчитать план» пишет ячейки по всей цепочке (логистика,
производства) — право черновиков без ограничения веткой.
"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.access import forbid_scoped
from app.api.budgets import _branch_for, _get_version, _guard
from app.api.deps import CurrentUser, DbSession
from app.api.imports import RowErrorOut, _body, _xlsx
from app.core.permissions import Perm
from app.models import Budget, BudgetVersion
from app.services import excel_import as xl
from app.services import volumes as service
from app.services.journal import JournalError
from app.services.plan import PlanError

router = APIRouter(prefix="/api", tags=["Бюджеты"])


class VolumeCellIn(BaseModel):
    cfo_id: int
    nomenclature_id: int
    kind: Literal["sale", "discount", "waste"]
    discount_cfo_id: int | None = None
    month: Annotated[int, Field(ge=1, le=12)]
    quantity: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)] | None = None


class VolumesIn(BaseModel):
    cells: Annotated[list[VolumeCellIn], Field(min_length=1, max_length=5000)]


class CalculateIn(BaseModel):
    revenue_article_id: int


class CalculateOut(BaseModel):
    cells: int
    total_revenue: Decimal
    missing: list[str]


class VolumesImportOut(BaseModel):
    rows: int
    changed: int
    errors: list[RowErrorOut]


def _org(db: Session, version: BudgetVersion) -> int:
    return db.get(Budget, version.budget_id).organization_id


def _fail(db: Session, exc: Exception):
    db.rollback()
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


def _check_points(db: Session, user, version: BudgetVersion, cfo_ids: set[int]) -> None:
    """Права на точки: утверждённый ЦФО — только администратор и фин. директор (как ячейки плана)."""
    guard = _guard(db, user, version)
    for cfo_id in cfo_ids:
        guard(cfo_id)


@router.get("/budget-versions/{version_id}/volumes")
def get_volumes(version_id: int, user: CurrentUser, db: DbSession) -> dict:
    version = _get_version(db, user, version_id, Perm.VIEW)
    guard = _guard(db, user, version)
    data = service.grid(db, version, _branch_for(db, user, _org(db, version)))
    data["editable"] = version.status != "archived" and (guard.supervisor or (guard.can_draft and version.status == "draft"))
    return data


@router.put("/budget-versions/{version_id}/volumes")
def put_volumes(version_id: int, body: VolumesIn, user: CurrentUser, db: DbSession) -> dict:
    """Записать количества (пусто или 0 — удалить ячейку). После — «Рассчитать план»."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    try:
        _check_points(db, user, version, {c.cfo_id for c in body.cells})
        changed = service.set_volumes(db, version, [service.VolumeIn(**c.model_dump()) for c in body.cells],
                                      _branch_for(db, user, _org(db, version)))
    except PlanError as exc:
        _fail(db, exc)
    db.commit()
    return {"changed": changed}


@router.post("/budget-versions/{version_id}/volumes/calculate", response_model=CalculateOut)
def calculate(version_id: int, body: CalculateIn, user: CurrentUser, db: DbSession):
    """Пересчитать ячейки плана ОПиУ «из объёмов». Не хватает цен — ничего не меняет, возвращает список."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    forbid_scoped(db, user, _org(db, version))
    try:
        result = service.calculate(db, version, user, body.revenue_article_id, guard=_guard(db, user, version))
    except (PlanError, JournalError) as exc:
        _fail(db, exc)
    if result.missing:
        db.rollback()
    else:
        db.commit()
    return CalculateOut(cells=result.cells, total_revenue=result.total_revenue, missing=result.missing[:200])


@router.get("/budget-versions/{version_id}/volumes.xlsx")
def download_volumes(version_id: int, user: CurrentUser, db: DbSession):
    version = _get_version(db, user, version_id, Perm.VIEW)
    content = service.volumes_xlsx(db, version, _branch_for(db, user, _org(db, version)))
    return _xlsx(content, f"plan_volumes_{version_id}.xlsx")


@router.post("/budget-versions/{version_id}/volumes", response_model=VolumesImportOut)
async def upload_volumes(version_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Тело запроса — файл .xlsx как есть."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    branch = _branch_for(db, user, _org(db, version))
    raw = await _body(request)
    guard = _guard(db, user, version)
    if version.status != "draft" and not guard.supervisor:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Менять можно только черновик версии")
    try:
        result = await run_in_threadpool(service.import_volumes, db, version, raw, branch)
    except xl.ExcelError as exc:
        _fail(db, exc)
    db.commit()
    return VolumesImportOut(rows=result.rows, changed=result.changed, errors=[e.__dict__ for e in result.errors])
