"""Движение имущества (docs/TZ.md, раздел 28, «Детали 19.1»): реестр, карточка единицы, история, файлы, Excel.

Смотреть — право ASSETS (руководитель ЦФО — своей ветки, МОЛ — только своё); заводить и править — ASSETS_EDIT.
"""

import datetime as dt
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, get_organization_for, org_permissions, role_in
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import ROLE_TITLES, Perm, Role
from app.models import AssetEvent, AssetItem, AssetItemFile, Membership, User
from app.models.asset import ASSET_STATUSES, OWNERSHIP
from app.services import assets as service
from app.services.excel_import import XLSX_TYPE, ExcelError

router = APIRouter(prefix="/api", tags=["Имущество"])

Price = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
ManualStatus = Literal["stock", "in_use", "repair"]


class ItemIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] = ""  # пусто — присвоится сам
    category_id: int | None = None
    ownership: Literal["own", "leasing", "rent"] = "own"
    legal_entity_id: int | None = None
    serial_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    cfo_id: int | None = None
    warehouse_id: int | None = None
    employee_id: int | None = None
    status: ManualStatus = "stock"
    purchase_price: Price | None = None
    acquired_on: dt.date | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class CreateIn(ItemIn):
    count: Annotated[int, Field(ge=1, le=service.MAX_COUNT)] = 1  # сколько одинаковых единиц


class BulkIn(BaseModel):
    """Групповое изменение: меняются только переданные поля (null — очистить склад или МОЛ)."""

    ids: Annotated[list[int], Field(min_length=1, max_length=service.MAX_COUNT)]
    cfo_id: int | None = None
    warehouse_id: int | None = None
    employee_id: int | None = None
    category_id: int | None = None
    status: ManualStatus | None = None


class FileOut(BaseModel):
    id: int
    filename: str
    size: int
    uploaded_at: dt.datetime


class ItemOut(BaseModel):
    id: int
    organization_id: int
    number: str
    name: str
    category_id: int | None
    category_name: str
    ownership: str
    ownership_title: str
    legal_entity_id: int | None
    legal_entity_name: str
    serial_number: str
    cfo_id: int | None
    cfo_name: str
    warehouse_id: int | None
    warehouse_name: str
    employee_id: int | None
    employee_name: str
    status: str
    status_title: str
    purchase_price: Decimal | None
    upgraded_price: Decimal | None
    lease_remaining: Decimal | None
    acquired_on: dt.date | None
    note: str
    fixed_asset_id: int | None
    files: list[FileOut] = []
    mine: bool = False  # МОЛ — сотрудник, связанный с текущим пользователем


class EventOut(BaseModel):
    at: dt.datetime
    user_name: str
    action: str
    details: str


class MemberOut(BaseModel):
    user_id: int
    name: str
    role_title: str


# --- доступ ---


class Scope:
    """Что пользователь видит в организации: всё, ветку ЦФО или только своё (МОЛ)."""

    def __init__(self, db: Session, user: User, organization_id: int):
        get_organization_for(db, user, organization_id, Perm.ASSETS)
        self.own = service.own_employee_ids(db, user, organization_id)
        membership = role_in(db, user, organization_id)
        self.only_own = not user.is_admin and membership is not None and membership.role == Role.MOL
        root = cfo_scope(db, user, organization_id)
        self.branch = branch_ids(db, root) if root is not None else None
        self.can_edit = Perm.ASSETS_EDIT in org_permissions(db, user, organization_id)

    def visible(self, item: AssetItem) -> bool:
        if self.only_own:
            return item.employee_id in self.own
        if self.branch is not None:
            return item.cfo_id in self.branch or item.employee_id in self.own
        return True

    def condition(self):
        if self.only_own:
            return AssetItem.employee_id.in_(self.own or [0])
        if self.branch is not None:
            return AssetItem.cfo_id.in_(self.branch) | AssetItem.employee_id.in_(self.own or [0])
        return None


def _item(db: Session, user: User, item_id: int, edit: bool = False) -> tuple[AssetItem, Scope]:
    item = db.get(AssetItem, item_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Единица имущества не найдена")
    if item is None:
        raise not_found
    try:
        scope = Scope(db, user, item.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    if not scope.visible(item):
        raise not_found
    if edit and not scope.can_edit:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return item, scope


def _out(db: Session, items: list[AssetItem], scope: Scope) -> list[ItemOut]:
    if not items:
        return []
    labels = service.Labels.load(db, items[0].organization_id)
    files: dict[int, list[FileOut]] = {}
    for f in db.execute(select(AssetItemFile.id, AssetItemFile.item_id, AssetItemFile.filename, AssetItemFile.size,
                               AssetItemFile.uploaded_at).where(AssetItemFile.item_id.in_([i.id for i in items]))
                        .order_by(AssetItemFile.id)).all():
        files.setdefault(f.item_id, []).append(FileOut(id=f.id, filename=f.filename, size=f.size,
                                                       uploaded_at=f.uploaded_at))
    return [ItemOut(
        id=i.id, organization_id=i.organization_id, number=i.number, name=i.name, category_id=i.category_id,
        category_name=labels.categories.get(i.category_id, ""), ownership=i.ownership,
        ownership_title=OWNERSHIP[i.ownership], legal_entity_id=i.legal_entity_id,
        legal_entity_name=labels.entities.get(i.legal_entity_id, ""), serial_number=i.serial_number, cfo_id=i.cfo_id,
        cfo_name=labels.cfos.get(i.cfo_id, ""), warehouse_id=i.warehouse_id,
        warehouse_name=labels.warehouses.get(i.warehouse_id, ""), employee_id=i.employee_id,
        employee_name=labels.employees.get(i.employee_id, ""), status=i.status, status_title=ASSET_STATUSES[i.status],
        purchase_price=i.purchase_price, upgraded_price=i.upgraded_price, lease_remaining=i.lease_remaining,
        acquired_on=i.acquired_on, note=i.note, fixed_asset_id=i.fixed_asset_id, files=files.get(i.id, []),
        mine=i.employee_id in scope.own,
    ) for i in items]


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _validate(db: Session, item: AssetItem) -> None:
    try:
        service.validate(db, item)
    except service.AssetError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


# --- реестр ---


def _query(db: Session, organization_id: int, scope: Scope, *, legal_entity_id=None, cfo_id=None, employee_id=None,
           category_id=None, status_=None, ownership=None, search=None, mine=False):
    query = select(AssetItem).where(AssetItem.organization_id == organization_id)
    condition = scope.condition()
    if condition is not None:
        query = query.where(condition)
    if legal_entity_id is not None:
        query = query.where(AssetItem.legal_entity_id == legal_entity_id)
    if cfo_id is not None:  # место — вместе с подчинёнными ЦФО
        query = query.where(AssetItem.cfo_id.in_(branch_ids(db, cfo_id)))
    if employee_id is not None:
        query = query.where(AssetItem.employee_id == employee_id)
    if category_id is not None:
        query = query.where(AssetItem.category_id == category_id)
    if status_:
        query = query.where(AssetItem.status.in_(status_.split(",")))
    if ownership:
        query = query.where(AssetItem.ownership == ownership)
    if search:
        query = query.where(service.search_condition(search))
    if mine:
        query = query.where(AssetItem.employee_id.in_(scope.own or [0]))
    return query.order_by(AssetItem.number)


def _filters(legal_entity_id: int | None = None, cfo_id: int | None = None, employee_id: int | None = None,
             category_id: int | None = None, status_: Annotated[str | None, Query(alias="status")] = None,
             ownership: Literal["own", "leasing", "rent"] | None = None, search: str | None = None,
             mine: bool = False) -> dict:
    return {"legal_entity_id": legal_entity_id, "cfo_id": cfo_id, "employee_id": employee_id,
            "category_id": category_id, "status_": status_, "ownership": ownership, "search": search, "mine": mine}


@router.get("/organizations/{organization_id}/asset-items", response_model=list[ItemOut])
def list_items(organization_id: int, user: CurrentUser, db: DbSession, legal_entity_id: int | None = None,
               cfo_id: int | None = None, employee_id: int | None = None, category_id: int | None = None,
               status_: Annotated[str | None, Query(alias="status")] = None,
               ownership: Literal["own", "leasing", "rent"] | None = None, search: str | None = None,
               mine: bool = False):
    scope = Scope(db, user, organization_id)
    if service.ensure(db, organization_id):  # новые карточки ОС → единицы
        db.commit()
    filters = _filters(legal_entity_id, cfo_id, employee_id, category_id, status_, ownership, search, mine)
    items = list(db.scalars(_query(db, organization_id, scope, **filters)))
    return _out(db, items, scope)


@router.get("/organizations/{organization_id}/asset-items.xlsx")
def download_registry(organization_id: int, user: CurrentUser, db: DbSession, legal_entity_id: int | None = None,
                      cfo_id: int | None = None, employee_id: int | None = None, category_id: int | None = None,
                      status_: Annotated[str | None, Query(alias="status")] = None,
                      ownership: Literal["own", "leasing", "rent"] | None = None, search: str | None = None,
                      mine: bool = False):
    scope = Scope(db, user, organization_id)
    filters = _filters(legal_entity_id, cfo_id, employee_id, category_id, status_, ownership, search, mine)
    items = list(db.scalars(_query(db, organization_id, scope, **filters)))
    content = service.registry_xlsx(db, items, organization_id)
    return Response(content, media_type=XLSX_TYPE, headers={"Content-Disposition": 'attachment; filename="assets.xlsx"'})


@router.post("/organizations/{organization_id}/asset-items", response_model=list[ItemOut], status_code=201)
def create_items(organization_id: int, body: CreateIn, user: CurrentUser, db: DbSession):
    """count одинаковых единиц; номер — только у одной единицы, иначе присваиваются сами."""
    get_organization_for(db, user, organization_id, Perm.ASSETS_EDIT)
    scope = Scope(db, user, organization_id)
    data = body.model_dump(exclude={"count", "number"})
    if body.number and body.count > 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Инвентарный № указывается только для одной единицы")
    numbers = [body.number] if body.number else service.next_numbers(db, organization_id, body.count)
    items = []
    for number in numbers:
        item = AssetItem(organization_id=organization_id, number=number, created_by=user.id, **data)
        _validate(db, item)
        db.add(item)
        db.flush()
        service.event(db, item, user, "created")
        items.append(item)
    _commit(db)
    return _out(db, items, scope)


@router.get("/asset-items/{item_id}", response_model=ItemOut)
def get_item(item_id: int, user: CurrentUser, db: DbSession):
    item, scope = _item(db, user, item_id)
    return _out(db, [item], scope)[0]


@router.put("/asset-items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, body: ItemIn, user: CurrentUser, db: DbSession):
    item, scope = _item(db, user, item_id, edit=True)
    if item.status == "disposed":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Единица выбыла — карточка не меняется")
    data = body.model_dump()
    if not data["number"]:
        data["number"] = item.number
    before = service.snapshot(item)
    for key, value in data.items():
        setattr(item, key, value)
    _validate(db, item)
    details = service.changes_text(service.Labels.load(db, item.organization_id), before, data)
    if details:
        service.event(db, item, user, "edited", details)
    _commit(db)
    db.refresh(item)
    return _out(db, [item], scope)[0]


@router.delete("/asset-items/{item_id}", status_code=204)
def delete_item(item_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Удалить заведённую по ошибке единицу (не связанную с карточкой ОС)."""
    item, _ = _item(db, user, item_id, edit=True)
    if item.fixed_asset_id is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Единица связана с карточкой основного средства — её не удалить")
    db.delete(item)
    _commit(db)
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/asset-items/bulk", response_model=list[ItemOut])
def bulk_update(organization_id: int, body: BulkIn, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.ASSETS_EDIT)
    scope = Scope(db, user, organization_id)
    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "ids"}
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Не выбрано, что изменить")
    items = list(db.scalars(select(AssetItem).where(AssetItem.organization_id == organization_id,
                                                    AssetItem.id.in_(body.ids)).order_by(AssetItem.number)))
    if len(items) != len(set(body.ids)) or not all(scope.visible(i) for i in items):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Часть единиц не найдена")
    labels = service.Labels.load(db, organization_id)
    for item in items:
        if item.status == "disposed":
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{item.number} выбыла — не меняется")
        before = service.snapshot(item)
        for key, value in changes.items():
            setattr(item, key, value)
        _validate(db, item)
        details = service.changes_text(labels, before, changes)
        if details:
            service.event(db, item, user, "bulk_edited", details)
    _commit(db)
    return _out(db, items, scope)


@router.get("/asset-items/{item_id}/events", response_model=list[EventOut])
def item_events(item_id: int, user: CurrentUser, db: DbSession):
    item, _ = _item(db, user, item_id)
    rows = db.execute(select(AssetEvent, User).outerjoin(User, User.id == AssetEvent.user_id)
                      .where(AssetEvent.item_id == item.id).order_by(AssetEvent.id)).all()
    return [EventOut(at=e.at, user_name=(u.full_name or u.login) if u else "система", action=e.action,
                     details=e.details) for e, u in rows]


# --- Excel: ввод имеющегося имущества ---


@router.get("/organizations/{organization_id}/import/templates/asset-items.xlsx")
def asset_template(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.ASSETS_EDIT)
    return Response(service.registry_xlsx(db, None, organization_id), media_type=XLSX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="assets_template.xlsx"'})


class ImportOut(BaseModel):
    rows: int
    created: int
    errors: list[tuple[int, str]]


@router.post("/organizations/{organization_id}/import/asset-items", response_model=ImportOut)
async def import_items(organization_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Тело запроса — файл Excel как есть; строки с ошибками не загружаются."""
    get_organization_for(db, user, organization_id, Perm.ASSETS_EDIT)
    raw = await request.body()
    try:
        result = service.import_items(db, organization_id, user, raw)
    except ExcelError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return ImportOut(rows=result.rows, created=result.created, errors=[(e.row, e.error) for e in result.errors])


# --- файлы единицы: акт, фото, паспорт ---

MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".xlsx": XLSX_TYPE, ".xls": "application/vnd.ms-excel", ".doc": "application/msword",
              ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@router.post("/asset-items/{item_id}/files", response_model=FileOut, status_code=201)
async def upload_file(item_id: int, filename: Annotated[str, Query(max_length=255)], request: Request,
                      user: CurrentUser, db: DbSession):
    item, _ = _item(db, user, item_id, edit=True)
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()[:255]
    content_type = FILE_TYPES.get(PurePosixPath(name).suffix.lower())
    if not name or content_type is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Можно приложить PDF, JPG, PNG, Excel или Word")
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    file = AssetItemFile(item_id=item.id, filename=name, content_type=content_type, size=len(raw), data=raw,
                         uploaded_by=user.id)
    db.add(file)
    service.event(db, item, user, "file", name)
    db.commit()
    db.refresh(file)
    return FileOut(id=file.id, filename=file.filename, size=file.size, uploaded_at=file.uploaded_at)


def _file(db: Session, user: User, file_id: int, edit: bool = False) -> tuple[AssetItemFile, AssetItem]:
    file = db.get(AssetItemFile, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    item, _ = _item(db, user, file.item_id, edit=edit)
    return file, item


@router.get("/asset-item-files/{file_id}")
def download_file(file_id: int, user: CurrentUser, db: DbSession):
    file, _ = _file(db, user, file_id)
    return Response(file.data, media_type=file.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}"})


@router.delete("/asset-item-files/{file_id}", status_code=204)
def delete_file(file_id: int, user: CurrentUser, db: DbSession) -> Response:
    file, item = _file(db, user, file_id, edit=True)
    service.event(db, item, user, "file_removed", file.filename)
    db.delete(file)
    db.commit()
    return Response(status_code=204)


# --- пользователи организации: для связи сотрудника с пользователем (МОЛ) ---


@router.get("/organizations/{organization_id}/members", response_model=list[MemberOut])
def list_members(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    rows = db.execute(select(User, Membership.role).join(Membership, Membership.user_id == User.id)
                      .where(Membership.organization_id == organization_id, User.is_active)
                      .order_by(User.full_name, User.login)).all()
    return [MemberOut(user_id=u.id, name=u.full_name or u.login, role_title=ROLE_TITLES[Role(role)])
            for u, role in rows]
