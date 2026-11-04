"""Бюджеты и версии (docs/TZ.md, раздел 13). Просмотр — VIEW; создание, утверждение, архив,
удаление — право проводить (POST): финансовый директор и администратор."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.access import branch_ids, cfo_scope, get_organization_for, org_permissions
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import (
    Article,
    Budget,
    BudgetCfoStatus,
    BudgetVersion,
    Cfo,
    Executor,
    LegalEntity,
    Operation,
    PlanChange,
    Posting,
    User,
)
from app.services import plan, plan_access, plan_methods
from app.services.articles import labels as article_labels
from app.services.articles import subtree_ids
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
    get_organization_for(db, user, organization_id, Perm.SUPERVISE)
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
    budget = _get_budget(db, user, budget_id, Perm.SUPERVISE)
    if db.scalar(select(BudgetVersion.id).where(BudgetVersion.budget_id == budget.id,
                                                BudgetVersion.status != "draft").limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="У бюджета есть утверждённые или архивные версии — удалить нельзя")
    db.delete(budget)
    db.commit()


@router.post("/budgets/{budget_id}/versions", response_model=VersionOut, status_code=201)
def create_version(budget_id: int, body: VersionCreate, user: CurrentUser, db: DbSession):
    """Новая версия-черновик: пустая или копия другой версии этого бюджета (ежемесячный пересмотр)."""
    budget = _get_budget(db, user, budget_id, Perm.SUPERVISE)
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
    version = _get_version(db, user, version_id, Perm.SUPERVISE)
    version.name = body.name
    db.commit()
    return _version_out(version)


@router.post("/budget-versions/{version_id}/approve", response_model=VersionOut)
def approve_version(version_id: int, user: CurrentUser, db: DbSession):
    """Утвердить черновик; прежняя утверждённая версия уходит в архив."""
    version = _get_version(db, user, version_id, Perm.SUPERVISE)
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
    version = _get_version(db, user, version_id, Perm.SUPERVISE)
    if version.status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Версия уже в архиве")
    version.status = "archived"
    db.commit()
    return _version_out(version)


@router.delete("/budget-versions/{version_id}", status_code=204)
def delete_version(version_id: int, user: CurrentUser, db: DbSession) -> None:
    version = _get_version(db, user, version_id, Perm.SUPERVISE)
    if version.status != "draft":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Удалить можно только черновик; утверждённую версию — отправьте в архив")
    db.delete(version)
    db.commit()


# --- Сетка ввода плана (шаг 3.3): ячейка = плановая операция ---

PlanKind = Literal["pl", "cf", "inv"]


class CellIn(BaseModel):
    article_id: int
    month: Annotated[int, Field(ge=1, le=12)]
    amount: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = None


class CellsIn(BaseModel):
    kind: PlanKind
    cfo_id: int | None = None
    executor_id: int | None = None  # ЦФУ строки (исполнитель); не указан — строка статьи без ЦФУ
    project_id: int | None = None  # инвестпроект (план инвестиций)
    cells: list[CellIn] = Field(min_length=1, max_length=2000)


class GridOut(BaseModel):
    version: VersionOut
    budget_title: str
    kind: str
    cfo_id: int | None
    executor_id: int | None = None
    mode: str  # own — план ЦФО (ввод); branch — свод с подчинёнными; executor — план ЦФУ по всем ЦФО (просмотр)
    has_children: bool
    editable: bool
    locked: bool = False  # ЦФО (или версия) утверждён: правка — только администратор и фин. директор, с историей
    months: list[dict]
    rows: list[dict]


def _check_plan_cfo(db: Session, user: User, organization_id: int, cfo_id: int | None) -> None:
    """Руководитель ЦФО работает с планом только своей ветки."""
    scope = cfo_scope(db, user, organization_id)
    if scope is not None and (cfo_id is None or cfo_id not in branch_ids(db, scope)):
        raise _not_found("ЦФО")


def _guard(db: Session, user: User, version: BudgetVersion) -> plan_access.Guard:
    """Права на правку плана версии (docs/TZ.md, раздел 22, «Детали 12.5»): после утверждения ЦФО или версии —
    только право проводить (администратор, финансовый директор), с историей."""
    perms = org_permissions(db, user, db.get(Budget, version.budget_id).organization_id)
    return plan_access.Guard(db, version, supervisor=Perm.SUPERVISE in perms, can_draft=Perm.DRAFT in perms)


@router.get("/budget-versions/{version_id}/grid", response_model=GridOut)
def get_grid(version_id: int, user: CurrentUser, db: DbSession, kind: PlanKind = "pl", cfo_id: int | None = None,
             mode: Literal["own", "branch", "executor"] = "own", executor_id: int | None = None):
    """Сетка плана: own — ячейки этого ЦФО (ввод); branch — свод ЦФО с подчинёнными; executor — план ЦФУ
    (отдел — с исполнителями) по всем ЦФО (руководителю ЦФО — по его ветке). Свод и ЦФУ — только просмотр."""
    version = _get_version(db, user, version_id, Perm.VIEW)
    budget = db.get(Budget, version.budget_id)
    if mode == "executor":
        executor = db.get(Executor, executor_id) if executor_id is not None else None
        if executor is None or executor.organization_id != budget.organization_id:
            raise _not_found("ЦФУ")
        data = plan.executor_grid(db, version, executor.id, _branch_for(db, user, budget.organization_id))
        return GridOut(version=_version_out(version), budget_title=budget.title, kind="pl", cfo_id=None,
                       executor_id=executor.id, mode="executor", has_children=False, editable=False, **data)
    _check_plan_cfo(db, user, budget.organization_id, cfo_id)
    guard = _guard(db, user, version)
    if kind == "inv":
        return GridOut(version=_version_out(version), budget_title=budget.title, kind=kind, cfo_id=None,
                       mode="own", has_children=False, editable=guard.can_edit(None),
                       **plan.investment_grid(db, version))
    branch = mode == "branch" and kind == "pl" and cfo_id is not None
    if cfo_id is not None:
        cfo = db.get(Cfo, cfo_id)
        if cfo is None or cfo.organization_id != budget.organization_id:
            raise _not_found("ЦФО")
    data = plan.branch_grid(db, version, kind, cfo_id) if branch else plan.grid(db, version, kind, cfo_id)
    editable = guard.can_edit(cfo_id if kind == "pl" else None)
    return GridOut(version=_version_out(version), budget_title=budget.title, kind=kind, cfo_id=cfo_id,
                   mode="branch" if branch else "own", has_children=plan.has_children(db, cfo_id),
                   editable=editable and not branch and (kind == "cf" or cfo_id is not None),
                   locked=guard.is_locked(cfo_id if kind == "pl" else None), **data)


@router.get("/budget-versions/{version_id}/branch.xlsx")
def download_branch(version_id: int, cfo_id: int, user: CurrentUser, db: DbSession):
    """Свод плана ОПиУ по ветке ЦФО в Excel."""
    version = _get_version(db, user, version_id, Perm.VIEW)
    budget = db.get(Budget, version.budget_id)
    cfo = db.get(Cfo, cfo_id)
    if cfo is None or cfo.organization_id != budget.organization_id:
        raise _not_found("ЦФО")
    _check_plan_cfo(db, user, budget.organization_id, cfo_id)
    content = plan.branch_xlsx(db, version, "pl", cfo_id)
    name = f"plan_svod_{budget.year}_v{version.number}_{cfo_id}.xlsx"
    return Response(content, media_type=XLSX_TYPE, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.put("/budget-versions/{version_id}/cells")
def put_cells(version_id: int, body: CellsIn, user: CurrentUser, db: DbSession) -> dict:
    """Записать ячейки сетки (пусто или 0 — очистить). Только черновик версии."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    budget = db.get(Budget, version.budget_id)
    _check_plan_cfo(db, user, budget.organization_id, body.cfo_id)
    try:
        changed = plan.set_cells(db, version, body.kind, body.cfo_id,
                                 [plan.Cell(c.article_id, c.month, c.amount) for c in body.cells], user,
                                 body.executor_id, body.project_id, guard=_guard(db, user, version))
    except (plan.PlanError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"changed": changed}


class PlanCellOut(BaseModel):
    operation_id: int
    cfo_id: int | None
    executor_id: int | None
    project_id: int | None
    article_id: int
    month: int
    amount: Decimal
    editable: bool = False  # этот пользователь может менять ячейку (ЦФО не утверждён или он — администратор / фин. директор)


class PlanCellsOut(BaseModel):
    version: VersionOut
    budget_title: str
    year: int
    months: list[int]  # месяцы бюджета из запрошенных
    editable: bool
    articles: list[dict]  # статьи плана из ветки запрошенной статьи: {id, label}
    cells: list[PlanCellOut]
    total: Decimal
    locked_cfo_ids: list[int] = []  # ЦФО, которые этому пользователю менять нельзя (утверждены)
    audited: bool = False  # правки записываются в историю (версия утверждена)


@router.get("/budget-versions/{version_id}/cells", response_model=PlanCellsOut)
def get_cells(version_id: int, user: CurrentUser, db: DbSession, kind: PlanKind, article_id: int,
              month: Annotated[list[str], Query(description="месяцы отчёта: YYYY-MM")],
              cfo_id: int | None = None, executor_id: int | None = None, legal_entity_id: int | None = None):
    """Плановые операции версии по статье (с подстатьями) и месяцам — окно «Плановые операции» из отчётов
    (docs/TZ.md, раздел 22, «Детали 12.1»). Фильтры — как у отчёта, итог совпадает с его цифрой."""
    version = _get_version(db, user, version_id, Perm.VIEW)
    budget = db.get(Budget, version.budget_id)
    org_id = budget.organization_id
    article = db.get(Article, article_id)
    if article is None or article.organization_id != org_id:
        raise _not_found("Статья")
    for model, value, title in ((Cfo, cfo_id, "ЦФО"), (Executor, executor_id, "ЦФУ"), (LegalEntity, legal_entity_id, "Юрлицо")):
        if value is not None and ((obj := db.get(model, value)) is None or obj.organization_id != org_id):
            raise _not_found(title)
    months = sorted({int(m[5:7]) for m in month if len(m) == 7 and m[:4] == str(budget.year) and m[5:7].isdigit()}
                    & set(budget.months))
    scope = cfo_scope(db, user, org_id)
    cfo_ids = branch_ids(db, cfo_id) if cfo_id is not None else None
    if scope is not None:  # руководитель ЦФО — только своя ветка
        own = branch_ids(db, scope)
        cfo_ids = own if cfo_ids is None else cfo_ids & own
    executor_ids = None
    if executor_id is not None:
        executor_ids = {executor_id, *db.scalars(select(Executor.id).where(Executor.parent_id == executor_id))}
    tree = set(subtree_ids(db, article.id))
    articles = [a for a, _ in plan.plan_articles(db, org_id, kind) if a.id in tree]
    cells = plan.cell_list(db, version, kind, [a.id for a in articles] or [article.id], months, cfo_ids=cfo_ids,
                           executor_ids=executor_ids, legal_entity_id=legal_entity_id)
    guard = _guard(db, user, version)
    editable = kind != "inv" and version.status != "archived" and (
        guard.supervisor or (guard.can_draft and version.status == "draft"))
    for c in cells:
        c["editable"] = editable and not c["project_id"] and guard.can_edit(c["cfo_id"])
    return PlanCellsOut(version=_version_out(version), budget_title=budget.title, year=budget.year, months=months,
                        editable=editable, articles=[{"id": a.id, "label": a.display_name} for a in articles],
                        cells=cells, total=sum((c["amount"] for c in cells), Decimal("0.00")),
                        locked_cfo_ids=[] if guard.supervisor else sorted(guard.locked),
                        audited=version.status == "approved")


# --- Снизу вверх: «план сдан» по ЦФО (docs/TZ.md, раздел 22, «Детали 12.2») ---


class CfoStatusOut(BaseModel):
    cfo_id: int
    parent_id: int | None
    level: int
    name: str
    is_leaf: bool
    plan_total: Decimal  # свой план ОПиУ ЦФО (без подчинённых)
    submitted_at: dt.datetime | None
    submitted_by: str | None
    changed_after: bool  # план менялся после отметки «сдан»
    approved_at: dt.datetime | None = None  # утверждён сам ЦФО (12.5)
    approved_by: str | None = None
    locked: bool = False  # утверждён он или вышестоящий ЦФО — руководители ЦФО план не меняют


def _status_row(db: Session, version_id: int, cfo_id: int) -> BudgetCfoStatus | None:
    return db.scalar(select(BudgetCfoStatus).where(BudgetCfoStatus.version_id == version_id,
                                                   BudgetCfoStatus.cfo_id == cfo_id))


def _cfo_statuses(db: Session, user: User, version: BudgetVersion) -> list[CfoStatusOut]:
    org_id = db.get(Budget, version.budget_id).organization_id
    scope = cfo_scope(db, user, org_id)
    visible = branch_ids(db, scope) if scope is not None else None
    cfos = [c for c in db.scalars(select(Cfo).where(Cfo.organization_id == org_id, Cfo.is_active).order_by(Cfo.path))
            if visible is None or c.id in visible]
    parents = {c.parent_id for c in cfos}
    totals = dict(db.execute(
        select(Operation.plan_cfo_id, func.coalesce(func.sum(Posting.amount), 0))
        .join(Posting, Posting.operation_id == Operation.id)
        .where(Operation.budget_version_id == version.id, Operation.plan_kind == "pl")
        .group_by(Operation.plan_cfo_id)).all())
    changed = dict(db.execute(
        select(Operation.plan_cfo_id, func.max(func.coalesce(Operation.updated_at, Operation.created_at)))
        .where(Operation.budget_version_id == version.id).group_by(Operation.plan_cfo_id)).all())
    statuses = {s.cfo_id: s for s in db.scalars(select(BudgetCfoStatus).where(BudgetCfoStatus.version_id == version.id))}
    names = {uid: full_name or login for uid, full_name, login in db.execute(
        select(User.id, User.full_name, User.login).where(
            User.id.in_([u for s in statuses.values() for u in (s.submitted_by, s.approved_by) if u])))}
    locked = plan_access.locked_cfo_ids(db, version)
    result = []
    for c in cfos:
        st = statuses.get(c.id)
        submitted = st.submitted_at if st else None
        result.append(CfoStatusOut(
            cfo_id=c.id, parent_id=c.parent_id, level=c.level, name=c.name, is_leaf=c.id not in parents,
            plan_total=Decimal(totals.get(c.id, 0)) + Decimal("0.00"), submitted_at=submitted,
            submitted_by=names.get(st.submitted_by) if st and st.submitted_by else None,
            changed_after=bool(submitted and changed.get(c.id) and changed[c.id] > submitted),
            approved_at=st.approved_at if st else None,
            approved_by=names.get(st.approved_by) if st and st.approved_by else None, locked=c.id in locked))
    return result


@router.get("/budget-versions/{version_id}/cfo-statuses", response_model=list[CfoStatusOut])
def list_cfo_statuses(version_id: int, user: CurrentUser, db: DbSession):
    """ЦФО организации (руководителю ЦФО — его ветка): свой план ОПиУ и отметка «план сдан»."""
    return _cfo_statuses(db, user, _get_version(db, user, version_id, Perm.VIEW))


def _cfo_for_status(db: Session, user: User, version_id: int, cfo_id: int) -> tuple[BudgetVersion, Cfo]:
    version = _get_version(db, user, version_id, Perm.DRAFT)
    org_id = db.get(Budget, version.budget_id).organization_id
    cfo = db.get(Cfo, cfo_id)
    if cfo is None or cfo.organization_id != org_id:
        raise _not_found("ЦФО")
    _check_plan_cfo(db, user, org_id, cfo_id)
    if version.status != "draft":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Отмечать сдачу плана можно только в черновике версии")
    if cfo.id in plan_access.locked_cfo_ids(db, version):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="План ЦФО уже утверждён")
    return version, cfo


@router.post("/budget-versions/{version_id}/cfo-statuses/{cfo_id}/submit", response_model=list[CfoStatusOut])
def submit_cfo_plan(version_id: int, cfo_id: int, user: CurrentUser, db: DbSession):
    """Отметка «план ЦФО сдан» (снизу вверх): ставит тот, кто вводит план этого ЦФО."""
    version, cfo = _cfo_for_status(db, user, version_id, cfo_id)
    row = _status_row(db, version.id, cfo.id) or BudgetCfoStatus(version_id=version.id, cfo_id=cfo.id)
    row.submitted_by, row.submitted_at = user.id, dt.datetime.now(dt.UTC)
    db.add(row)
    db.commit()
    return _cfo_statuses(db, user, version)


@router.post("/budget-versions/{version_id}/cfo-statuses/{cfo_id}/unsubmit", response_model=list[CfoStatusOut])
def unsubmit_cfo_plan(version_id: int, cfo_id: int, user: CurrentUser, db: DbSession):
    version, cfo = _cfo_for_status(db, user, version_id, cfo_id)
    row = _status_row(db, version.id, cfo.id)
    if row is None or row.submitted_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="План этого ЦФО не отмечен как сданный")
    row.submitted_by, row.submitted_at = None, None
    db.commit()
    return _cfo_statuses(db, user, version)


# --- Утверждение по ЦФО (docs/TZ.md, раздел 22, «Детали 12.5») ---


def _cfo_for_approval(db: Session, user: User, version_id: int, cfo_id: int) -> tuple[BudgetVersion, Cfo]:
    version = _get_version(db, user, version_id, Perm.SUPERVISE)
    cfo = db.get(Cfo, cfo_id)
    if cfo is None or cfo.organization_id != db.get(Budget, version.budget_id).organization_id:
        raise _not_found("ЦФО")
    if version.status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Версия в архиве")
    return version, cfo


@router.post("/budget-versions/{version_id}/cfo-statuses/{cfo_id}/approve", response_model=list[CfoStatusOut])
def approve_cfo_plan(version_id: int, cfo_id: int, user: CurrentUser, db: DbSession):
    """Утвердить план ЦФО вместе с подчинёнными: дальше его меняют только администратор и фин. директор."""
    version, cfo = _cfo_for_approval(db, user, version_id, cfo_id)
    row = _status_row(db, version.id, cfo.id) or BudgetCfoStatus(version_id=version.id, cfo_id=cfo.id)
    row.approved_by, row.approved_at = user.id, dt.datetime.now(dt.UTC)
    db.add(row)
    db.commit()
    return _cfo_statuses(db, user, version)


@router.post("/budget-versions/{version_id}/cfo-statuses/{cfo_id}/unapprove", response_model=list[CfoStatusOut])
def unapprove_cfo_plan(version_id: int, cfo_id: int, user: CurrentUser, db: DbSession):
    version, cfo = _cfo_for_approval(db, user, version_id, cfo_id)
    row = _status_row(db, version.id, cfo.id)
    if row is None or row.approved_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Этот ЦФО не утверждён (возможно, утверждён вышестоящий — снимите утверждение у него)")
    row.approved_by, row.approved_at = None, None
    db.commit()
    return _cfo_statuses(db, user, version)


class PlanChangeOut(BaseModel):
    changed_at: dt.datetime
    user: str
    kind: str
    cfo_id: int | None
    executor_id: int | None
    article_id: int
    article: str
    month: int
    old_amount: Decimal | None
    new_amount: Decimal | None


@router.get("/budget-versions/{version_id}/changes", response_model=list[PlanChangeOut])
def list_plan_changes(version_id: int, user: CurrentUser, db: DbSession):
    """История правок плана после утверждения: кто, когда, ЦФО, статья, месяц, было → стало (новые — сверху)."""
    version = _get_version(db, user, version_id, Perm.VIEW)
    org_id = db.get(Budget, version.budget_id).organization_id
    scope = cfo_scope(db, user, org_id)
    visible = branch_ids(db, scope) if scope is not None else None
    labels = article_labels(db, org_id)
    rows = db.execute(select(PlanChange, User).join(User, User.id == PlanChange.user_id)
                      .where(PlanChange.version_id == version.id)
                      .order_by(PlanChange.changed_at.desc(), PlanChange.id.desc()).limit(1000)).all()
    return [PlanChangeOut(changed_at=ch.changed_at, user=u.full_name or u.login, kind=ch.kind, cfo_id=ch.cfo_id,
                          executor_id=ch.executor_id, article_id=ch.article_id, article=labels.get(ch.article_id, ""),
                          month=ch.month, old_amount=ch.old_amount, new_amount=ch.new_amount)
            for ch, u in rows if visible is None or ch.cfo_id in visible]


# --- Сверху вниз: цель ЦФО → нижние ЦФО (docs/TZ.md, раздел 22, «Детали 12.3») ---


class TargetIn(BaseModel):
    month: Annotated[int, Field(ge=1, le=12)]
    amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] | None = None


class DistributeIn(BaseModel):
    cfo_id: int
    article_id: int
    targets: list[TargetIn] = Field(min_length=1, max_length=12)


class TargetOut(BaseModel):
    article_id: int
    month: int
    target: Decimal
    bottom: Decimal  # план ветки ЦФО по статье и месяцу
    deviation: Decimal  # по низу − цель


class DistributedMonth(BaseModel):
    month: int
    target: Decimal | None
    base_from: dt.date
    base_to: dt.date
    by_fact: bool
    parts: dict[int, Decimal]


class DistributeOut(BaseModel):
    months: list[DistributedMonth]
    targets: list[TargetOut]


def _target_cfo(db: Session, user: User, version: BudgetVersion, cfo_id: int) -> Cfo:
    budget = db.get(Budget, version.budget_id)
    cfo = db.get(Cfo, cfo_id)
    if cfo is None or cfo.organization_id != budget.organization_id:
        raise _not_found("ЦФО")
    _check_plan_cfo(db, user, budget.organization_id, cfo_id)
    return cfo


@router.get("/budget-versions/{version_id}/targets", response_model=list[TargetOut])
def list_targets(version_id: int, cfo_id: int, user: CurrentUser, db: DbSession):
    """Цели ЦФО и «по низу» (план его ветки) с отклонением."""
    version = _get_version(db, user, version_id, Perm.VIEW)
    return plan_methods.targets(db, version, _target_cfo(db, user, version, cfo_id))


@router.post("/budget-versions/{version_id}/targets/distribute", response_model=DistributeOut)
def distribute_targets(version_id: int, body: DistributeIn, user: CurrentUser, db: DbSession):
    """Цель по статье на месяцы → доли нижних ЦФО пропорционально факту за 6 месяцев (нет факта — поровну)."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    cfo = _target_cfo(db, user, version, body.cfo_id)
    article = db.get(Article, body.article_id)
    if article is None or article.organization_id != cfo.organization_id:
        raise _not_found("Статья")
    try:
        months = plan_methods.distribute(db, version, cfo, article, {t.month: t.amount for t in body.targets}, user,
                                         guard=_guard(db, user, version))
    except (plan.PlanError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return DistributeOut(months=[m.__dict__ for m in months], targets=plan_methods.targets(db, version, cfo))


# --- Ретроспективный: среднее за 1 / 3 / 6 / 12 месяцев (docs/TZ.md, раздел 22, «Детали 12.4») ---


class RetroIn(BaseModel):
    kind: Literal["pl", "cf"]
    cfo_id: int | None = None
    branch: bool = False  # вместе с подчинёнными ЦФО (каждый — по своему факту)
    base: Literal[1, 3, 6, 12]


class RetroOut(BaseModel):
    filled: int
    kept: int
    cfos: int


@router.post("/budget-versions/{version_id}/retrospective", response_model=RetroOut)
def fill_retrospective(version_id: int, body: RetroIn, user: CurrentUser, db: DbSession):
    """Пустые ячейки плана — средним фактом за base месяцев перед каждым месяцем; заполненные не меняются."""
    version = _get_version(db, user, version_id, Perm.DRAFT)
    cfo = _target_cfo(db, user, version, body.cfo_id) if body.cfo_id is not None else None
    if cfo is None:
        _check_plan_cfo(db, user, db.get(Budget, version.budget_id).organization_id, None)  # ДДС — не руководителю ЦФО
    try:
        result = plan_methods.retrospective(db, version, body.kind, cfo if body.kind == "pl" else None,
                                            body.branch, body.base, user, guard=_guard(db, user, version))
    except (plan.PlanError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return RetroOut(**result.__dict__)


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
    if kind == "inv":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Excel для плана инвестиций пока не поддерживается")
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
    if kind == "inv":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Excel для плана инвестиций пока не поддерживается")
    if kind == "cf" and branch is not None:
        raise _not_found("План ДДС")
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    try:
        result = await run_in_threadpool(plan.import_plan, db, version, kind, raw, user, branch,
                                         _guard(db, user, version))
    except (ExcelError, plan.PlanError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"rows": result.rows, "changed": result.changed, "errors": [e.__dict__ for e in result.errors]}
