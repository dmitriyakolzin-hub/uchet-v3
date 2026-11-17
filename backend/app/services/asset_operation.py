"""Эксплуатация имущества (docs/TZ.md, раздел 28, «Детали 19.5»): перемещения и передача с подтверждением получателем,
работы по единице — модернизация, ремонт, ТО."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import AssetItem, Cfo, Counterparty, Employee, User, Warehouse
from app.models.asset import ASSET_STATUSES, WORK_KINDS, AssetTransfer, AssetTransferItem, AssetWork
from app.services import assets as asset_service

ZERO = Decimal("0.00")


class OperationError(ValueError):
    pass


def _own(db: Session, organization_id: int, model, value, title: str):
    if value is None:
        return None
    record = db.get(model, value)
    if record is None or record.organization_id != organization_id:
        raise OperationError(f"{title}: не найдено в этой организации")
    return record


def pending_item_ids(db: Session, organization_id: int) -> set[int]:
    """Единицы в неподтверждённых перемещениях."""
    return set(db.scalars(select(AssetTransferItem.item_id).join(
        AssetTransfer, AssetTransfer.id == AssetTransferItem.transfer_id).where(
        AssetTransfer.organization_id == organization_id, AssetTransfer.status == "pending")))


def create_transfer(db: Session, organization_id: int, user: User, items: list[AssetItem], *, on_date: dt.date,
                    to_cfo_id: int | None, to_warehouse_id: int | None, to_employee_id: int | None,
                    to_status: str | None, reason: str) -> AssetTransfer:
    """Перемещение: куда (место, склад), кому (МОЛ), в каком статусе; пустое поле — не меняется. Единицы переходят,
    когда получатель (МОЛ) подтверждает."""
    _own(db, organization_id, Cfo, to_cfo_id, "Место (ЦФО)")
    _own(db, organization_id, Warehouse, to_warehouse_id, "Склад")
    _own(db, organization_id, Employee, to_employee_id, "Кому (МОЛ)")
    if to_cfo_id is None and to_warehouse_id is None and to_employee_id is None and to_status is None:
        raise OperationError("Укажите, куда, кому или в каком статусе передать")
    if to_status not in (None, "stock", "in_use", "repair"):
        raise OperationError("Статус после передачи: на складе, в эксплуатации или в ремонте")
    busy = pending_item_ids(db, organization_id)
    for item in items:
        if item.status == "disposed":
            raise OperationError(f"{item.number} выбыла")
        if item.id in busy:
            raise OperationError(f"{item.number} уже перемещается — дождитесь подтверждения или отмените")
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    number = (db.scalar(select(func.max(AssetTransfer.number)).where(
        AssetTransfer.organization_id == organization_id)) or 0) + 1
    transfer = AssetTransfer(organization_id=organization_id, number=number, on_date=on_date, to_cfo_id=to_cfo_id,
                             to_warehouse_id=to_warehouse_id, to_employee_id=to_employee_id, to_status=to_status,
                             reason=reason, created_by=user.id)
    db.add(transfer)
    db.flush()
    for item in items:
        db.add(AssetTransferItem(transfer_id=transfer.id, item_id=item.id, from_cfo_id=item.cfo_id,
                                 from_warehouse_id=item.warehouse_id, from_employee_id=item.employee_id))
        asset_service.event(db, item, user, "transfer_created", f"перемещение № {number}")
    db.flush()
    return transfer


def transfer_items(db: Session, transfer: AssetTransfer) -> list[AssetItem]:
    return list(db.scalars(select(AssetItem).join(AssetTransferItem, AssetTransferItem.item_id == AssetItem.id)
                           .where(AssetTransferItem.transfer_id == transfer.id).order_by(AssetItem.number)))


def accept(db: Session, transfer: AssetTransfer, user: User, on_behalf: bool) -> None:
    """Получатель подтвердил (или АХО за него): единицы — на новом месте, у нового МОЛ."""
    if transfer.status != "pending":
        raise OperationError("Перемещение уже закрыто")
    labels = asset_service.Labels.load(db, transfer.organization_id)
    changes: dict = {}
    if transfer.to_cfo_id is not None:
        changes["cfo_id"] = transfer.to_cfo_id
    if transfer.to_warehouse_id is not None:
        changes["warehouse_id"] = transfer.to_warehouse_id
    if transfer.to_employee_id is not None:
        changes["employee_id"] = transfer.to_employee_id
    if transfer.to_status is not None:
        changes["status"] = transfer.to_status
    for item in transfer_items(db, transfer):
        if item.status == "disposed":
            raise OperationError(f"{item.number} выбыла — отмените перемещение")
        before = asset_service.snapshot(item)
        for key, value in changes.items():
            setattr(item, key, value)
        details = asset_service.changes_text(labels, before, changes)
        who = "подтверждено за получателя" if on_behalf else "получатель подтвердил"
        asset_service.event(db, item, user, "transferred",
                            f"перемещение № {transfer.number} ({who})" + (f": {details}" if details else ""))
    transfer.status, transfer.decided_by, transfer.decided_at = "accepted", user.id, dt.datetime.now(dt.UTC)
    db.flush()


def close(db: Session, transfer: AssetTransfer, user: User, status: str, reason: str = "") -> None:
    """Отказ получателя или отмена отправителем: единицы остаются где были."""
    if transfer.status != "pending":
        raise OperationError("Перемещение уже закрыто")
    transfer.status, transfer.reject_reason = status, reason
    transfer.decided_by, transfer.decided_at = user.id, dt.datetime.now(dt.UTC)
    for item in transfer_items(db, transfer):
        asset_service.event(db, item, user, "transfer_" + status, f"перемещение № {transfer.number}"
                            + (f": {reason}" if reason else ""))
    db.flush()


# --- работы: модернизация, ремонт, ТО ---


def add_work(db: Session, item: AssetItem, user: User, *, kind: str, started_on: dt.date,
             finished_on: dt.date | None, description: str, counterparty_id: int | None, employee_id: int | None,
             amount: Decimal | None) -> AssetWork:
    """Ремонт без даты окончания — единица «в ремонте», пока его не завершат. Модернизация увеличивает стоимость
    единицы (цена приобретения + модернизации), поэтому сумма у неё обязательна."""
    if kind not in WORK_KINDS:
        raise OperationError("Вид работы: модернизация, ремонт или ТО")
    if item.status == "disposed":
        raise OperationError(f"{item.number} выбыла")
    if finished_on is not None and finished_on < started_on:
        raise OperationError("Окончание раньше начала")
    if kind == "modernization" and not amount:
        raise OperationError("Укажите стоимость модернизации")
    _own(db, item.organization_id, Counterparty, counterparty_id, "Подрядчик")
    _own(db, item.organization_id, Employee, employee_id, "Кто делал")
    work = AssetWork(item_id=item.id, kind=kind, started_on=started_on, finished_on=finished_on,
                     description=description, counterparty_id=counterparty_id, employee_id=employee_id, amount=amount,
                     created_by=user.id)
    db.add(work)
    details = f"{WORK_KINDS[kind]} с {started_on:%d.%m.%Y}" + (f" по {finished_on:%d.%m.%Y}" if finished_on else "")
    if amount:
        details += f", {amount:,.2f} ₽".replace(",", " ").replace(".", ",")
    if description:
        details += f": {description}"
    if kind == "repair" and finished_on is None and item.status != "repair":
        details += f"; статус: {ASSET_STATUSES[item.status]} → {ASSET_STATUSES['repair']}"
        item.status = "repair"
    asset_service.event(db, item, user, "work", details)
    db.flush()
    return work


def finish_work(db: Session, work: AssetWork, user: User, finished_on: dt.date, status: str = "in_use") -> None:
    if work.finished_on is not None:
        raise OperationError("Работа уже завершена")
    if finished_on < work.started_on:
        raise OperationError("Окончание раньше начала")
    item = db.get(AssetItem, work.item_id)
    work.finished_on = finished_on
    details = f"{WORK_KINDS[work.kind]} завершён(о) {finished_on:%d.%m.%Y}"
    open_repairs = db.scalar(select(func.count()).where(AssetWork.item_id == item.id, AssetWork.kind == "repair",
                                                        AssetWork.finished_on.is_(None), AssetWork.id != work.id))
    if item.status == "repair" and not open_repairs:
        details += f"; статус: {ASSET_STATUSES['repair']} → {ASSET_STATUSES[status]}"
        item.status = status
    asset_service.event(db, item, user, "work_finished", details)
    db.flush()


def modernization_total(db: Session, item_ids: list[int]) -> dict[int, Decimal]:
    rows = db.execute(select(AssetWork.item_id, func.sum(AssetWork.amount)).where(
        AssetWork.item_id.in_(item_ids), AssetWork.kind == "modernization").group_by(AssetWork.item_id)).all()
    return {item_id: Decimal(total) for item_id, total in rows if total}
