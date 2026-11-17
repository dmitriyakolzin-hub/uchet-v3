"""Инвентаризация имущества (docs/TZ.md, раздел 28, «Детали 19.7»).

Создать, завершить, удалить — оператор АХО, бухгалтер, ФД (ASSETS_EDIT); отмечать найденное — они же, инженер
(ASSET_SERVICE) и МОЛ своей инвентаризации; смотреть — по области единиц (руководитель ЦФО — ветка, МОЛ — свои).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import org_permissions
from app.api.assets import Scope
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import AssetItem, User
from app.models.asset import ASSET_STATUSES, INVENTORY_STATUSES, AssetInventory, AssetInventoryLine
from app.services import asset_inventory as service
from app.services import assets as asset_service
from app.services.asset_inventory import InventoryError

router = APIRouter(prefix="/api", tags=["Имущество"])


class InventoryIn(BaseModel):
    on_date: dt.date
    cfo_id: int | None = None
    warehouse_id: int | None = None
    employee_id: int | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class ScanIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CloseIn(BaseModel):
    move_surplus: bool = False  # найденное не на месте — перенести сюда
    lost_to_disposal: bool = False  # ненайденное — на выбытие (гибель / утрата)


class LineOut(BaseModel):
    id: int
    item_id: int
    number: str
    name: str
    place: str
    employee_name: str
    status_title: str
    expected: bool
    found: bool
    found_at: dt.datetime | None


class InventoryOut(BaseModel):
    id: int
    number: int
    on_date: dt.date
    place: str
    warehouse: str
    employee_name: str
    note: str
    status: str
    status_title: str
    expected_count: int
    found_count: int
    missing_count: int
    surplus_count: int
    created_by_name: str
    lines: list[LineOut] = []
    can_scan: bool
    can_close: bool


class ScanOut(BaseModel):
    result: str  # found / surplus / again
    message: str
    inventory: InventoryOut


def _run(db: Session, action) -> None:
    try:
        action()
    except InventoryError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


def _lines(db: Session, inv: AssetInventory) -> list[tuple[AssetInventoryLine, AssetItem]]:
    return list(db.execute(select(AssetInventoryLine, AssetItem).join(AssetItem, AssetItem.id == AssetInventoryLine.item_id)
                           .where(AssetInventoryLine.inventory_id == inv.id).order_by(AssetItem.number)).all())


def _can_scan(inv: AssetInventory, scope: Scope, perms: frozenset) -> bool:
    return inv.status == "open" and (Perm.ASSETS_EDIT in perms or Perm.ASSET_SERVICE in perms
                                     or (inv.employee_id is not None and inv.employee_id in scope.own))


def _visible(db: Session, inv: AssetInventory, scope: Scope) -> bool:
    if inv.created_by == scope.user_id or (inv.employee_id is not None and inv.employee_id in scope.own):
        return True
    if scope.only_own:
        return False
    if scope.branch is not None:
        return inv.cfo_id in scope.branch
    return True


def _out(db: Session, inventories: list[AssetInventory], scope: Scope, perms: frozenset,
         with_lines: bool = False) -> list[InventoryOut]:
    if not inventories:
        return []
    labels = asset_service.Labels.load(db, inventories[0].organization_id)
    users = {u.id: u.full_name or u.login for u in db.scalars(select(User).where(
        User.id.in_({i.created_by for i in inventories if i.created_by})))}
    result = []
    for inv in inventories:
        rows = _lines(db, inv)
        out = InventoryOut(
            id=inv.id, number=inv.number, on_date=inv.on_date, place=labels.cfos.get(inv.cfo_id, ""),
            warehouse=labels.warehouses.get(inv.warehouse_id, ""), employee_name=labels.employees.get(inv.employee_id, ""),
            note=inv.note, status=inv.status, status_title=INVENTORY_STATUSES[inv.status],
            expected_count=sum(1 for line, _ in rows if line.expected),
            found_count=sum(1 for line, _ in rows if line.expected and line.found),
            missing_count=sum(1 for line, _ in rows if line.expected and not line.found),
            surplus_count=sum(1 for line, _ in rows if not line.expected),
            created_by_name=users.get(inv.created_by, ""), can_scan=_can_scan(inv, scope, perms),
            can_close=inv.status == "open" and Perm.ASSETS_EDIT in perms)
        if with_lines:
            out.lines = [LineOut(id=line.id, item_id=item.id, number=item.number, name=item.name,
                                 place=" · ".join(x for x in (labels.cfos.get(item.cfo_id, ""),
                                                              labels.warehouses.get(item.warehouse_id, "")) if x),
                                 employee_name=labels.employees.get(item.employee_id, ""),
                                 status_title=ASSET_STATUSES[item.status], expected=line.expected, found=line.found,
                                 found_at=line.found_at) for line, item in rows]
        result.append(out)
    return result


def _inventory(db: Session, user: User, inventory_id: int) -> tuple[AssetInventory, Scope, frozenset]:
    inv = db.get(AssetInventory, inventory_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Инвентаризация не найдена")
    if inv is None:
        raise not_found
    try:
        scope = Scope(db, user, inv.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    if not _visible(db, inv, scope):
        raise not_found
    return inv, scope, org_permissions(db, user, inv.organization_id)


def _edit(perms: frozenset) -> None:
    if Perm.ASSETS_EDIT not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Инвентаризацию проводят АХО, бухгалтер, ФД")


@router.get("/organizations/{organization_id}/asset-inventories", response_model=list[InventoryOut])
def list_inventories(organization_id: int, user: CurrentUser, db: DbSession):
    scope = Scope(db, user, organization_id)
    perms = org_permissions(db, user, organization_id)
    items = [i for i in db.scalars(select(AssetInventory).where(AssetInventory.organization_id == organization_id)
                                   .order_by(AssetInventory.number.desc()).limit(200)) if _visible(db, i, scope)]
    return _out(db, items, scope, perms)


@router.post("/organizations/{organization_id}/asset-inventories", response_model=InventoryOut, status_code=201)
def create_inventory(organization_id: int, body: InventoryIn, user: CurrentUser, db: DbSession):
    scope = Scope(db, user, organization_id)
    perms = org_permissions(db, user, organization_id)
    _edit(perms)
    holder: list[AssetInventory] = []
    _run(db, lambda: holder.append(service.create(db, organization_id, user, **body.model_dump())))
    db.commit()
    return _out(db, holder, scope, perms, with_lines=True)[0]


@router.get("/asset-inventories/{inventory_id}", response_model=InventoryOut)
def get_inventory(inventory_id: int, user: CurrentUser, db: DbSession):
    inv, scope, perms = _inventory(db, user, inventory_id)
    return _out(db, [inv], scope, perms, with_lines=True)[0]


@router.post("/asset-inventories/{inventory_id}/scan", response_model=ScanOut)
def scan(inventory_id: int, body: ScanIn, user: CurrentUser, db: DbSession):
    inv, scope, perms = _inventory(db, user, inventory_id)
    if not _can_scan(inv, scope, perms):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отмечать найденное могут АХО, инженер, бухгалтер, ФД "
                                                              "или МОЛ этой инвентаризации")
    holder: list = []
    _run(db, lambda: holder.append(service.scan(db, inv, user, body.code)))
    db.commit()
    _, item, result = holder[0]
    message = {"found": f"Найдено: {item.number} {item.name}",
               "surplus": f"Не на своём месте: {item.number} {item.name}",
               "again": f"Уже отмечено: {item.number} {item.name}"}[result]
    return ScanOut(result=result, message=message, inventory=_out(db, [inv], scope, perms, with_lines=True)[0])


@router.delete("/asset-inventory-lines/{line_id}", response_model=InventoryOut)
def unscan(line_id: int, user: CurrentUser, db: DbSession):
    """Снять отметку «найдено» (отсканировали по ошибке)."""
    line = db.get(AssetInventoryLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    inv, scope, perms = _inventory(db, user, line.inventory_id)
    if not _can_scan(inv, scope, perms):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    _run(db, lambda: service.unscan(db, inv, line))
    db.commit()
    return _out(db, [inv], scope, perms, with_lines=True)[0]


class CloseOut(BaseModel):
    inventory: InventoryOut
    disposal_number: int | None


@router.post("/asset-inventories/{inventory_id}/close", response_model=CloseOut)
def close_inventory(inventory_id: int, body: CloseIn, user: CurrentUser, db: DbSession):
    inv, scope, perms = _inventory(db, user, inventory_id)
    _edit(perms)
    holder: list = []
    _run(db, lambda: holder.append(service.close(db, inv, user, move_surplus=body.move_surplus,
                                                 lost_to_disposal=body.lost_to_disposal)))
    db.commit()
    return CloseOut(inventory=_out(db, [inv], scope, perms, with_lines=True)[0], disposal_number=holder[0])


@router.delete("/asset-inventories/{inventory_id}", status_code=204)
def delete_inventory(inventory_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Удалить незавершённую инвентаризацию (создали по ошибке)."""
    inv, _, perms = _inventory(db, user, inventory_id)
    _edit(perms)
    if inv.status != "open":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Завершённую инвентаризацию не удалить")
    db.delete(inv)
    db.commit()
    return Response(status_code=204)
