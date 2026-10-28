"""Загрузка из Excel (docs/TZ.md, раздел 11): шаблоны и загрузка операций, контрагентов, сотрудников, статей."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from app.api.access import branch_ids, cfo_scope, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.services import article_import, employee_import
from app.services import excel_import as xl

router = APIRouter(prefix="/api/organizations/{organization_id}/import", tags=["Загрузка из Excel"])

MAX_FILE_BYTES = 10 * 1024 * 1024


class RowErrorOut(BaseModel):
    row: int
    error: str


class OperationsImportOut(BaseModel):
    rows: int
    created: list[int]
    incomplete: list[int]
    skipped_duplicates: int
    errors: list[RowErrorOut]


class CounterpartiesImportOut(BaseModel):
    rows: int
    created: list[str]
    skipped_existing: int
    errors: list[RowErrorOut]


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(
        content, media_type=xl.XLSX_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _body(request: Request) -> bytes:
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    return raw


@router.get("/templates/operations.xlsx")
def operations_template(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.DRAFT)
    return _xlsx(xl.operations_template(db, organization_id), "operations.xlsx")


@router.get("/templates/counterparties.xlsx")
def counterparties_template(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    return _xlsx(xl.counterparties_template(), "counterparties.xlsx")


@router.post("/operations", response_model=OperationsImportOut)
async def import_operations(organization_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Тело запроса — файл .xlsx как есть. Строки становятся черновиками операций."""
    get_organization_for(db, user, organization_id, Perm.DRAFT)
    scope = cfo_scope(db, user, organization_id)
    branch = None if scope is None else branch_ids(db, scope)
    raw = await _body(request)
    try:
        result = await run_in_threadpool(xl.import_operations, db, organization_id, user, raw, branch)
    except xl.ExcelError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return OperationsImportOut(
        rows=result.rows, created=result.created, incomplete=result.incomplete,
        skipped_duplicates=result.skipped_duplicates, errors=[e.__dict__ for e in result.errors],
    )


@router.post("/counterparties", response_model=CounterpartiesImportOut)
async def import_counterparties(organization_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Тело запроса — файл .xlsx как есть. Контрагенты с уже известным ИНН пропускаются."""
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    raw = await _body(request)
    try:
        result = await run_in_threadpool(xl.import_counterparties, db, organization_id, raw)
    except xl.ExcelError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return CounterpartiesImportOut(
        rows=result.rows, created=result.created, skipped_existing=result.skipped_existing,
        errors=[e.__dict__ for e in result.errors],
    )


# --- Сотрудники: список в Excel и загрузка (создание и обновление, оклады) ---


class EmployeesImportOut(BaseModel):
    rows: int
    created: list[str]
    updated: list[str]
    errors: list[RowErrorOut]


@router.get("/templates/employees.xlsx")
def employees_template(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    return _xlsx(employee_import.employees_xlsx(db, organization_id, with_rows=False), "employees_template.xlsx")


@router.get("/employees.xlsx")
def employees_list(organization_id: int, user: CurrentUser, db: DbSession):
    """Текущий список сотрудников в формате загрузки (с окладами на сегодня)."""
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    return _xlsx(employee_import.employees_xlsx(db, organization_id), "employees.xlsx")


@router.post("/employees", response_model=EmployeesImportOut)
async def import_employees(organization_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Тело запроса — файл .xlsx как есть. Сотрудник ищется по табельному, внутреннему № или ФИО."""
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    raw = await _body(request)
    try:
        result = await run_in_threadpool(employee_import.import_employees, db, organization_id, raw)
    except xl.ExcelError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return EmployeesImportOut(rows=result.rows, created=result.created, updated=result.updated,
                              errors=[e.__dict__ for e in result.errors])


# --- справочник статей (docs/TZ.md, раздел 19, «Детали 9.2») ---


class ArticleRowOut(BaseModel):
    row: int
    code: str | None
    name: str
    note: str
    kind: str
    parent_code: str | None
    match_id: int | None
    match_name: str
    match_reason: str
    warning: str
    flags: str


class ArticleRowIn(BaseModel):
    row: int
    code: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=30)] = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    note: Annotated[str, StringConstraints(max_length=100)] = ""
    kind: Literal["article", "indicator", "header"]
    match_id: int | None = None


class ArticlesImportOut(BaseModel):
    created: int
    merged: int
    updated: int
    skipped: int
    errors: list[RowErrorOut]


@router.post("/articles/preview", response_model=list[ArticleRowOut])
async def preview_articles(organization_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Разбор файла справочника без изменений: вид строк, родители по коду, предлагаемое совмещение."""
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    raw = await _body(request)
    try:
        rows = await run_in_threadpool(article_import.preview, db, organization_id, raw)
    except article_import.ArticleImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    finally:
        db.rollback()
    return [ArticleRowOut(**r.__dict__) for r in rows]


@router.post("/articles", response_model=ArticlesImportOut)
def import_articles(organization_id: int, body: Annotated[list[ArticleRowIn], Field(max_length=2000)],
                    user: CurrentUser, db: DbSession):
    """Загрузка проверенных строк: статьи создаются или совмещаются с существующими."""
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    rows = [article_import.Row(row=r.row, code=article_import.norm_code(r.code), name=r.name, note=r.note,
                               kind=r.kind, match_id=r.match_id) for r in body]
    result = article_import.apply(db, organization_id, rows)
    db.commit()
    return ArticlesImportOut(created=result.created, merged=result.merged, updated=result.updated,
                             skipped=result.skipped, errors=[RowErrorOut(row=n, error=e) for n, e in result.errors])
