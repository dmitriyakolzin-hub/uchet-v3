"""Загрузка из Excel (docs/TZ.md, раздел 11): шаблоны и загрузка операций и контрагентов."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
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
    raw = await _body(request)
    try:
        result = await run_in_threadpool(xl.import_operations, db, organization_id, user, raw)
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
