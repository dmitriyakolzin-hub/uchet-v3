"""Бюджеты и версии (docs/TZ.md, раздел 13). Просмотр — VIEW; создание, утверждение, архив,
удаление — право проводить (POST): финансовый директор и администратор."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.access import branch_ids, cfo_scope, get_organization_for, org_permissions
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Budget, BudgetVersion, User
from app.services import plan
from app.services.journal import JournalError

router = APIRouter(prefix="/api", tags=["Бюджеты"])

Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class BudgetCreate(BaseModel):
    kind: Literal["year", "quarter", "month"]
    year: Annotated[int, Field(ge=2000, le=2100)]
    period: int = 1

    @model_validator(mode="after")
    def _period(self):
        limit = {"year": 1, "quarter": 4, "month": 12}[self.kind]
        if not 1 <= self.period <= limit:
            raise ValueError("неверный период бюджета")
        return self


class VersionCreate(BaseModel):
    name: Name = ""
    copy_from_id: int | None = None  # скопировать план другой версии этого бюджета


class VersionUpdate(BaseModel):
    name: Name


class VersionOut(BaseModel):
    id: int
    budget_id: int
    number: int
    name: str
    status: str
    based_on_id: int | None
    created_at: dt.datetime
    approved_at: dt.datetime | None


class BudgetOut(BaseModel):
    id: int
    organization_id: int
    kind: str
    year: int
    period: int
    title: str
    months: list[int]
    versions: list[VersionOut]


def _version_out(v: BudgetVersion) -> VersionOut:
    return VersionOut(id=v.id, budget_id=v.budget_id, number=v.number, name=v.name, status=v.status,
                      based_on_id=v.based_on_id, created_at=v.created_at, approved_at=v.approved_at)


def _budget_out(db: Session, b: Budget) -> BudgetOut:
    versions = db.scalars(select(BudgetVersion).where(BudgetVersion.budget_id == b.id).order_by(BudgetVersion.number))
    return BudgetOut(id=b.id, organization_id=b.organization_id, kind=b.kind, year=b.year, period=b.period,
                     title=b.title, months=b.months, versions=[_version_out(v) for v in versions])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} не найден(а)")


def _get_budget(db: Session, user: User, budget_id: int, perm: Perm) -> Budget:
    budget = db.get(Budget, budget_id)
    if budget is None:
        raise _not_found("Бюджет")
    try:
        get_organization_for(db, user, budget.organization_id, perm)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _not_found("Бюджет") from None
        raise
    return budget


def _get_version(db: Session, user: User, version_id: int, perm: Perm) -> BudgetVersion:
    version = db.get(BudgetVersion, version_id)
    if version is None:
        raise _not_found("Версия бюджета")
    try:
        _get_budget(db, user, version.budget_id, perm)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _not_found("Версия бюджета") from None
        raise
    return version


def _new_version(db: Session, budget: Budget, user: User, name: str, based_on: BudgetVersion | None) -> BudgetVersion:
    number = (db.scalar(select(func.max(BudgetVersion.number)).where(BudgetVersion.budget_id == budget.id)) or 0) + 1
    version = BudgetVersion(budget_id=budget.id, number=number, name=name or f"Версия {number}", status="draft",
                            based_on_id=based_on.id if based_on else None, created_by=user.id)
    db.add(version)
    db.flush()
    return version


@router.get("/organizations/{organization_id}/budgets", response_model=list[BudgetOut])
def list_budgets(organization_id: int, user: CurrentUser, db: DbSession, year: int | None = None):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    query = select(Budget).where(Budget.organization_id == organization_id)
    if year is not None:
        query = query.where(Budget.year == year)
    budgets = db.scalars(query.order_by(Budget.year.desc(), Budget.kind.desc(), Budget.period))
    # порядок внутри года: год, кварталы, месяцы
    order = {"year": 0, "quarter": 1, "month": 2}
    return sorted((_budget_out(db, b) for b in budgets), key=lambda b: (-b.year, order[b.kind], b.period))


@router.post("/organizations/{organization_id}/budgets", response_model=BudgetOut, status_code=201)
def create_budget(organization_id: int, body: BudgetCreate, user: CurrentUser, db: DbSession):
    """Новый бюджет на период; сразу создаётся первая версия-черновик."""
    get_organization_for(db, user, organization_id, Perm.POST)
    budget = Budget(organization_id=organization_id, **body.model_dump())
    db.add(budget)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Бюджет на этот период уже есть") from None
    _new_version(db, budget, user, "", None)
    db.commit()
    return _budget_out(db, budget)


@router.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(budget_id: int, user: CurrentUser, db: DbSession) -> None:
    budget = _get_budget(db, user, budget_id, Perm.POST)
    if db.scalar(select(BudgetVersion.id).where(BudgetVersion.budget_id == budget.id,
                                                BudgetVersion.status != "draft").limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="У бюджета есть утверждённые или архивные версии — удалить нельзя")
    db.delete(budget)
    db.commit()


@router.post("/budgets/{budget_id}/versions", response_model=VersionOut, status_code=201)
def create_version(budget_id: int, body: VersionCreate, user: CurrentUser, db: DbSession):
    """Новая версия-черновик: пустая или копия другой версии этого бюджета (ежемесячный пересмотр)."""
    budget = _get_budget(db, user, budget_id, Perm.POST)
    source = None
    if body.copy_from_id is not None:
        source = db.get(BudgetVersion, body.copy_from_id)
        if source is None or source.budget_id != budget.id:
            raise _not_found("Версия бюджета")
    version = _new_version(db, budget, user, body.name, source)
    if source is not None:
        plan.copy_plan(db, source, version, user)
    db.commit()
    return _version_out(version)


@router.patch("/budget-versions/{version_id}", response_model=VersionOut)
def rename_version(version_id: int, body: VersionUpdate, user: CurrentUser, db: DbSession):
    version = _get_version(db, user, version_id, Perm.POST)
    version.name = body.name
    db.commit()
    return _version_out(version)


@router.post("/budget-versions/{version_id}/approve", response_model=VersionOut)
def approve_version(version_id: int, user: CurrentUser, db: DbSession):
    """Утвердить черновик; прежняя утверждённая версия уходит в архив."""
    version = _get_version(db, user, version_id, Perm.POST)
    if version.status != "draft":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Утвердить можно только черновик")
    for previous in db.scalars(select(BudgetVersion).where(BudgetVersion.budget_id == version.budget_id,
                                                          BudgetVersion.status == "approved")):
        previous.status = "archived"
    db.flush()
    version.status = "approved"
    version.approved_by = user.id
    version.approved_at = dt.datetime.now(dt.UTC)
    db.commit()
    return _version_out(version)


@router.post("/budget-versions/{version_id}/archive", response_model=VersionOut)
def archive_version(version_id: int, user: CurrentUser, db: DbSession):
    version = _get_version(db, user, version_id, Perm.POST)
    if version.status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Версия уже в архиве")
    version.status = "archived"
    db.commit()
    return _version_out(version)


@router.delete("/budget-versions/{version_id}", status_code=204)
def delete_version(version_id: int, user: CurrentUser, db: DbSession) -> None:
    version = _get_version(db, user, version_id, Perm.POST)
    if version.status != "draft":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Удалить можно только черновик; утверждённую версию — отправьте в архив")
    db.delete(version)
    db.commit()


# --- Сетка ввода плана (шаг 3.3): ячейка = плановая операция ---

PlanKind = Literal["pl", "cf"]


class CellIn(BaseModel):
    article_id: int
    month: Annotated[int, Field(ge=1, le=12)]
    amount: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = None


class CellsIn(BaseModel):
    kind: PlanKind
    cfo_id: int | None = None
    cells: list[CellIn] = Field(min_length=1, max_length=2000)


class GridOut(BaseModel):
    version: VersionOut
    budget_title: str
    kind: str
    cfo_id: int | None
    editable: bool
    months: list[dict]
    rows: list[dict]


def _check_plan_cfo(db: Session, user: User, organization_id: int, cfo_id: int | None) -> None:
    """Руководитель ЦФО работает с планом только своей ветки."""
    scope = cfo_scope(db, user, organization_id)
    if scope is not None and (cfo_id is None or cfo_id not in branch_ids(db, scope)):
        raise _not_found("ЦФО")


@router.get("/budget-versions/{version_id}/grid", response_model=GridOut)
def get_grid(version_id: int, user: CurrentUser, db: DbSession, kind: PlanKind = "pl", cfo_id: int | None = None):
    version = _get_version(db, user, version_id, Perm.VIEW)
    budget = db.get(Budget, version.budget_id)
    _check_plan_cfo(db, user, budget.organization_id, cfo_id)
    data = plan.grid(db, version, kind, cfo_id)
    editable = version.status == "draft" and Perm.DRAFT in org_permissions(db, user, budget.organization_id)
    return GridOut(version=_version_out(version), budget_title=budget.title, kind=kind, cfo_id=cfo_id,
                   editable=editable and (kind == "cf" or cfo_id is not None), **data)


@router.put("/budget-versions/{version_id}/cells")
def put_cells(version_id: int, body: CellsIn, user: CurrentUser, db: DbSession) -> dict:
    """Записать ячейки сетки (пусто или 0 — очистить). Только черновик версии."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    budget = db.get(Budget, version.budget_id)
    _check_plan_cfo(db, user, budget.organization_id, body.cfo_id)
    try:
        changed = plan.set_cells(db, version, body.kind, body.cfo_id,
                                 [plan.Cell(c.article_id, c.month, c.amount) for c in body.cells], user)
    except (plan.PlanError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"changed": changed}


# --- План в Excel ---

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _branch_for(db: Session, user: User, organization_id: int) -> set[int] | None:
    scope = cfo_scope(db, user, organization_id)
    return None if scope is None else branch_ids(db, scope)


@router.get("/budget-versions/{version_id}/plan.xlsx")
def download_plan(version_id: int, user: CurrentUser, db: DbSession, kind: PlanKind = "pl"):
    """Текущий план версии: ОПиУ — все ЦФО (руководителю ЦФО — его ветка) × статьи; ДДС — статьи."""
    version = _get_version(db, user, version_id, Perm.VIEW)
    budget = db.get(Budget, version.budget_id)
    branch = _branch_for(db, user, budget.organization_id)
    if kind == "cf":
        if branch is not None:
            raise _not_found("План ДДС")
        cfo_ids: list[int | None] = [None]
    else:
        labels = plan.cfo_labels(db, budget.organization_id, branch)
        cfo_ids = sorted(labels, key=lambda i: labels[i].lower())
    content = plan.plan_xlsx(db, version, kind, cfo_ids)
    name = f"plan_{'pl' if kind == 'pl' else 'dds'}_{budget.year}_v{version.number}.xlsx"
    return Response(content, media_type=XLSX_TYPE, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/budget-versions/{version_id}/plan")
async def upload_plan(version_id: int, request: Request, user: CurrentUser, db: DbSession, kind: PlanKind = "pl"):
    """Загрузить план из Excel в черновик версии (файл — как скачан, с исправленными суммами)."""
    from app.services.excel_import import ExcelError

    version = _get_version(db, user, version_id, Perm.DRAFT)
    budget = db.get(Budget, version.budget_id)
    branch = _branch_for(db, user, budget.organization_id)
    if kind == "cf" and branch is not None:
        raise _not_found("План ДДС")
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    try:
        result = await run_in_threadpool(plan.import_plan, db, version, kind, raw, user, branch)
    except (ExcelError, plan.PlanError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"rows": result.rows, "changed": result.changed, "errors": [e.__dict__ for e in result.errors]}
