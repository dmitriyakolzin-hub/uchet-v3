"""Инвентаризация имущества (docs/TZ.md, раздел 28, «Детали 19.7»).

При создании — снимок «что должно быть»: невыбывшие единицы по месту (ЦФО с подчинёнными), складу и / или МОЛ.
Сканирование QR (в нём ссылка с id единицы) или ввод инв. / серийного номера отмечает «найдено»; единица не из этого
места — «найдено не на своём месте». Завершение: найденное не на месте можно перенести сюда, ненайденное — отправить
на выбытие (гибель / утрата, согласует ФД).
"""

import datetime as dt
import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.access import branch_ids
from app.models import AssetItem, Cfo, Employee, User, Warehouse
from app.models.asset import AssetInventory, AssetInventoryLine
from app.services import asset_disposal, asset_operation
from app.services import assets as asset_service


class InventoryError(ValueError):
    pass


def _own(db: Session, organization_id: int, model, value, title: str):
    if value is None:
        return None
    record = db.get(model, value)
    if record is None or record.organization_id != organization_id:
        raise InventoryError(f"{title}: не найдено в этой организации")
    return record


def expected_query(db: Session, inv: AssetInventory):
    query = select(AssetItem).where(AssetItem.organization_id == inv.organization_id, AssetItem.status != "disposed")
    if inv.cfo_id is not None:
        query = query.where(AssetItem.cfo_id.in_(branch_ids(db, inv.cfo_id)))
    if inv.warehouse_id is not None:
        query = query.where(AssetItem.warehouse_id == inv.warehouse_id)
    if inv.employee_id is not None:
        query = query.where(AssetItem.employee_id == inv.employee_id)
    return query


def create(db: Session, organization_id: int, user: User, *, on_date: dt.date, cfo_id: int | None,
           warehouse_id: int | None, employee_id: int | None, note: str) -> AssetInventory:
    _own(db, organization_id, Cfo, cfo_id, "Место (ЦФО)")
    _own(db, organization_id, Warehouse, warehouse_id, "Склад")
    _own(db, organization_id, Employee, employee_id, "МОЛ")
    if cfo_id is None and warehouse_id is None and employee_id is None:
        raise InventoryError("Укажите, что проверяем: место, склад или МОЛ")
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    number = (db.scalar(select(func.max(AssetInventory.number)).where(
        AssetInventory.organization_id == organization_id)) or 0) + 1
    inv = AssetInventory(organization_id=organization_id, number=number, on_date=on_date, cfo_id=cfo_id,
                         warehouse_id=warehouse_id, employee_id=employee_id, note=note, created_by=user.id)
    db.add(inv)
    db.flush()
    for item in db.scalars(expected_query(db, inv)):
        db.add(AssetInventoryLine(inventory_id=inv.id, item_id=item.id, expected=True))
    db.flush()
    return inv


ITEM_LINK = re.compile(r"[?&#]item=(\d+)")


def find_item(db: Session, organization_id: int, code: str) -> AssetItem:
    """По QR (ссылка с item=ID), инвентарному или серийному номеру."""
    code = code.strip()
    if not code:
        raise InventoryError("Пустой код")
    match = ITEM_LINK.search(code)
    if match:
        item = db.get(AssetItem, int(match.group(1)))
        if item is None or item.organization_id != organization_id:
            raise InventoryError("Единица из этого QR-кода не найдена в организации")
        return item
    found = list(db.scalars(select(AssetItem).where(AssetItem.organization_id == organization_id,
                                                    func.lower(AssetItem.number) == code.lower())))
    if not found:
        found = list(db.scalars(select(AssetItem).where(AssetItem.organization_id == organization_id,
                                                        AssetItem.serial_number != "",
                                                        func.lower(AssetItem.serial_number) == code.lower())))
    if not found:
        raise InventoryError(f"«{code}» — нет такой единицы (инв. или серийного номера)")
    if len(found) > 1:
        raise InventoryError(f"«{code}» — несколько единиц с таким серийным номером, введите инв. номер")
    return found[0]


def scan(db: Session, inv: AssetInventory, user: User, code: str) -> tuple[AssetInventoryLine, AssetItem, str]:
    """Отметить найденное. Результат: found — числилась здесь, surplus — не на своём месте, again — уже отмечена."""
    if inv.status != "open":
        raise InventoryError("Инвентаризация завершена")
    item = find_item(db, inv.organization_id, code)
    if item.status == "disposed":
        raise InventoryError(f"{item.number} «{item.name}» выбыла — проверьте, откуда она здесь")
    line = db.scalar(select(AssetInventoryLine).where(AssetInventoryLine.inventory_id == inv.id,
                                                      AssetInventoryLine.item_id == item.id))
    if line is not None and line.found:
        return line, item, "again"
    if line is None:
        line = AssetInventoryLine(inventory_id=inv.id, item_id=item.id, expected=False)
        db.add(line)
    line.found, line.found_at, line.found_by = True, dt.datetime.now(dt.UTC), user.id
    db.flush()
    return line, item, "found" if line.expected else "surplus"


def unscan(db: Session, inv: AssetInventory, line: AssetInventoryLine) -> None:
    if inv.status != "open":
        raise InventoryError("Инвентаризация завершена")
    if line.expected:
        line.found, line.found_at, line.found_by = False, None, None
    else:
        db.delete(line)
    db.flush()


def close(db: Session, inv: AssetInventory, user: User, *, move_surplus: bool, lost_to_disposal: bool) -> int | None:
    """Завершить. Возвращает номер акта выбытия, если ненайденное отправлено на выбытие."""
    if inv.status != "open":
        raise InventoryError("Инвентаризация уже завершена")
    rows = db.execute(select(AssetInventoryLine, AssetItem).join(AssetItem, AssetItem.id == AssetInventoryLine.item_id)
                      .where(AssetInventoryLine.inventory_id == inv.id)).all()
    labels = asset_service.Labels.load(db, inv.organization_id)
    if move_surplus:
        changes = {k: v for k, v in (("cfo_id", inv.cfo_id), ("warehouse_id", inv.warehouse_id),
                                     ("employee_id", inv.employee_id)) if v is not None}
        moving = asset_operation.pending_item_ids(db, inv.organization_id)
        for line, item in rows:
            if line.found and not line.expected and item.status != "disposed" and item.id not in moving:
                before = asset_service.snapshot(item)
                for key, value in changes.items():
                    setattr(item, key, value)
                details = asset_service.changes_text(labels, before, changes)
                asset_service.event(db, item, user, "inventory_moved",
                                    f"по итогам инвентаризации № {inv.number}" + (f": {details}" if details else ""))
    disposal_number = None
    if lost_to_disposal:
        busy = asset_disposal.pending_item_ids(db, inv.organization_id) | \
            asset_operation.pending_item_ids(db, inv.organization_id)
        lost = [item for line, item in rows
                if line.expected and not line.found and item.status != "disposed" and item.id not in busy]
        if lost:
            try:
                disposal = asset_disposal.create(
                    db, inv.organization_id, user, lost, kind="loss", on_date=inv.on_date,
                    reason=f"Не найдено при инвентаризации № {inv.number}", counterparty_id=None, contract_id=None,
                    vat_rate=0, employee_id=inv.employee_id, prices={})
            except asset_disposal.DisposalError as exc:
                raise InventoryError(str(exc)) from None
            disposal_number = disposal.number
    for line, item in rows:
        if line.expected and not line.found:
            asset_service.event(db, item, user, "inventory_missing", f"не найдена при инвентаризации № {inv.number}")
    inv.status, inv.closed_by, inv.closed_at = "closed", user.id, dt.datetime.now(dt.UTC)
    db.flush()
    return disposal_number
