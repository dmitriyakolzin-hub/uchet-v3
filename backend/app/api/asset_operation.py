"""Эксплуатация имущества (docs/TZ.md, раздел 28, «Детали 19.5»; раздел 30, «Детали 22.3»): перемещения, работы.

Создать перемещение — оператор АХО, бухгалтер, ФД (право ASSETS_EDIT), МОЛ своих единиц, руководитель ЦФО — единиц
своей ветки, руководитель ЦФУ — единиц своего ЦФУ. Затем согласует АХО (оператор АХО или ФД; если в организации нет
оператора АХО — не нужно). Подтвердить — получатель (МОЛ), руководитель ЦФО ветки, куда перемещают, руководитель ЦФУ
нового МОЛ — или АХО за него; отменить — автор или АХО. Работы — право ASSET_SERVICE.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import org_permissions
from app.api.assets import Scope, _item
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import AssetItem, Counterparty, Employee, User
from app.models.asset import ASSET_STATUSES, TRANSFER_STATUSES, WORK_KINDS, AssetTransfer, AssetTransferItem, AssetWork
from app.services import asset_operation as service
from app.services import assets as asset_service
from app.services.asset_operation import OperationError

router = APIRouter(prefix="/api", tags=["Имущество"])

Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class TransferIn(BaseModel):
    ids: Annotated[list[int], Field(min_length=1, max_length=asset_service.MAX_COUNT)]
    on_date: dt.date
    to_cfo_id: int | None = None
    to_warehouse_id: int | None = None
    to_employee_id: int | None = None
    to_status: Literal["stock", "in_use", "repair"] | None = None
    reason: Reason = ""


class ReasonIn(BaseModel):
    reason: Reason = ""


class TransferItemOut(BaseModel):
    id: int
    number: str
    name: str
    from_place: str
    from_employee: str


class TransferOut(BaseModel):
    id: int
    number: int
    on_date: dt.date
    status: str
    status_title: str
    to_place: str
    to_warehouse: str
    to_employee: str
    to_status_title: str
    reason: str
    reject_reason: str
    created_by_name: str
    decided_by_name: str
    aho_approved: bool = True
    aho_approved_by_name: str = ""
    items: list[TransferItemOut]
    can_accept: bool
    can_cancel: bool
    can_approve: bool = False  # согласовать как АХО


class WorkIn(BaseModel):
    kind: Literal["modernization", "repair", "maintenance"]
    started_on: dt.date
    finished_on: dt.date | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    counterparty_id: int | None = None
    employee_id: int | None = None
    amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] | None = None


class FinishIn(BaseModel):
    finished_on: dt.date
    status: Literal["stock", "in_use"] = "in_use"


class WorkOut(BaseModel):
    id: int
    kind: str
    kind_title: str
    started_on: dt.date
    finished_on: dt.date | None
    description: str
    counterparty_name: str
    employee_name: str
    amount: Decimal | None


class WorksOut(BaseModel):
    works: list[WorkOut]
    modernization_total: Decimal
    cost_with_modernization: Decimal | None


def _run(db: Session, action) -> None:
    try:
        action()
    except OperationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


# --- перемещения ---


def _can_accept(scope: Scope, transfer: AssetTransfer) -> bool:
    if transfer.status != "pending":
        return False
    if transfer.aho_approved_at is None:  # до согласования АХО принимает (и тем согласует) только АХО
        return scope.is_aho
    return scope.receives(transfer.to_cfo_id, transfer.to_employee_id) or scope.can_edit


def _sees(db: Session, scope: Scope, transfer: AssetTransfer, user: User) -> bool:
    """Свои, адресованные пользователю (в т. ч. в его ветку / его ЦФУ) или с единицами из его области."""
    return (transfer.created_by == user.id or scope.receives(transfer.to_cfo_id, transfer.to_employee_id)
            or any(scope.visible(i) for i in service.transfer_items(db, transfer)))


def _transfers_out(db: Session, transfers: list[AssetTransfer], scope: Scope) -> list[TransferOut]:
    if not transfers:
        return []
    labels = asset_service.Labels.load(db, transfers[0].organization_id)
    users = {u.id: u.full_name or u.login for u in db.scalars(select(User).where(User.id.in_(
        {t.created_by for t in transfers} | {t.decided_by for t in transfers if t.decided_by}
        | {t.aho_approved_by for t in transfers if t.aho_approved_by})))}
    rows = db.execute(select(AssetTransferItem, AssetItem).join(AssetItem, AssetItem.id == AssetTransferItem.item_id)
                      .where(AssetTransferItem.transfer_id.in_([t.id for t in transfers]))
                      .order_by(AssetItem.number)).all()
    items: dict[int, list[TransferItemOut]] = {}
    for link, item in rows:
        items.setdefault(link.transfer_id, []).append(TransferItemOut(
            id=item.id, number=item.number, name=item.name,
            from_place=" · ".join(x for x in (labels.cfos.get(link.from_cfo_id, ""),
                                              labels.warehouses.get(link.from_warehouse_id, "")) if x),
            from_employee=labels.employees.get(link.from_employee_id, "")))
    return [TransferOut(
        id=t.id, number=t.number, on_date=t.on_date, status=t.status,
        status_title=("Ждёт согласования АХО" if t.status == "pending" and t.aho_approved_at is None
                      else TRANSFER_STATUSES[t.status]),
        aho_approved=t.aho_approved_at is not None, aho_approved_by_name=users.get(t.aho_approved_by, ""),
        can_approve=t.status == "pending" and t.aho_approved_at is None and scope.is_aho,
        to_place=labels.cfos.get(t.to_cfo_id, ""), to_warehouse=labels.warehouses.get(t.to_warehouse_id, ""),
        to_employee=labels.employees.get(t.to_employee_id, ""),
        to_status_title=ASSET_STATUSES.get(t.to_status or "", ""), reason=t.reason, reject_reason=t.reject_reason,
        created_by_name=users.get(t.created_by, ""), decided_by_name=users.get(t.decided_by, ""),
        items=items.get(t.id, []), can_accept=_can_accept(scope, t),
        can_cancel=t.status == "pending" and (t.created_by == scope.user_id or scope.can_edit),
    ) for t in transfers]


@router.get("/organizations/{organization_id}/asset-transfers", response_model=list[TransferOut])
def list_transfers(organization_id: int, user: CurrentUser, db: DbSession,
                   status_: Annotated[str | None, Query(alias="status")] = None, to_me: bool = False):
    """Перемещения, которые пользователь видит: с единицами из его области или адресованные ему."""
    scope = Scope(db, user, organization_id)
    query = select(AssetTransfer).where(AssetTransfer.organization_id == organization_id)
    if status_:
        query = query.where(AssetTransfer.status.in_(status_.split(",")))
    transfers = list(db.scalars(query.order_by(AssetTransfer.number.desc()).limit(500)))
    visible = [t for t in transfers if _sees(db, scope, t, user)]
    if to_me:  # «мне на подтверждение»: ждут моего согласования (АХО) или приёма
        visible = [t for t in visible if _can_accept(scope, t) and (scope.receives(t.to_cfo_id, t.to_employee_id)
                                                                   or (scope.is_aho and t.aho_approved_at is None))]
    return _transfers_out(db, visible, scope)


@router.post("/organizations/{organization_id}/asset-transfers", response_model=TransferOut, status_code=201)
def create_transfer(organization_id: int, body: TransferIn, user: CurrentUser, db: DbSession):
    scope = Scope(db, user, organization_id)
    items = list(db.scalars(select(AssetItem).where(AssetItem.organization_id == organization_id,
                                                    AssetItem.id.in_(body.ids)).order_by(AssetItem.number)))
    if len(items) != len(set(body.ids)) or not all(scope.visible(i) for i in items):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Часть единиц не найдена")
    if not all(scope.can_move(i) for i in items):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Передать может АХО, МОЛ — своё имущество, руководитель ЦФО "
                                                              "— своей ветки, руководитель ЦФУ — своего ЦФУ")
    holder: list[AssetTransfer] = []
    _run(db, lambda: holder.append(service.create_transfer(
        db, organization_id, user, items, **body.model_dump(exclude={"ids"}))))
    db.commit()
    return _transfers_out(db, holder, scope)[0]


def _transfer(db: Session, user: User, transfer_id: int) -> tuple[AssetTransfer, Scope]:
    transfer = db.get(AssetTransfer, transfer_id)
    if transfer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Перемещение не найдено")
    try:
        scope = Scope(db, user, transfer.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Перемещение не найдено") from None
        raise
    if not _sees(db, scope, transfer, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Перемещение не найдено")
    return transfer, scope


@router.post("/asset-transfers/{transfer_id}/approve", response_model=TransferOut)
def approve_transfer(transfer_id: int, user: CurrentUser, db: DbSession):
    """Согласование АХО (22.3): оператор АХО или финансовый директор."""
    transfer, scope = _transfer(db, user, transfer_id)
    if not scope.is_aho:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Согласует АХО")
    _run(db, lambda: service.approve_aho(db, transfer, user))
    db.commit()
    return _transfers_out(db, [transfer], scope)[0]


@router.post("/asset-transfers/{transfer_id}/accept", response_model=TransferOut)
def accept_transfer(transfer_id: int, user: CurrentUser, db: DbSession):
    transfer, scope = _transfer(db, user, transfer_id)
    if not _can_accept(scope, transfer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Подтверждает получатель (после согласования АХО) или АХО")
    on_behalf = not scope.receives(transfer.to_cfo_id, transfer.to_employee_id)
    _run(db, lambda: service.accept(db, transfer, user, on_behalf))
    db.commit()
    return _transfers_out(db, [transfer], scope)[0]


@router.post("/asset-transfers/{transfer_id}/reject", response_model=TransferOut)
def reject_transfer(transfer_id: int, body: ReasonIn, user: CurrentUser, db: DbSession):
    transfer, scope = _transfer(db, user, transfer_id)
    if not (transfer.status == "pending" and (scope.receives(transfer.to_cfo_id, transfer.to_employee_id)
                                              or scope.can_edit or scope.is_aho)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отказаться может получатель или АХО")
    _run(db, lambda: service.close(db, transfer, user, "rejected", body.reason))
    db.commit()
    return _transfers_out(db, [transfer], scope)[0]


@router.post("/asset-transfers/{transfer_id}/cancel", response_model=TransferOut)
def cancel_transfer(transfer_id: int, user: CurrentUser, db: DbSession):
    transfer, scope = _transfer(db, user, transfer_id)
    if transfer.created_by != user.id and not scope.can_edit:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отменить может тот, кто создал, или АХО")
    _run(db, lambda: service.close(db, transfer, user, "cancelled"))
    db.commit()
    return _transfers_out(db, [transfer], scope)[0]


# --- работы ---


def _works_out(db: Session, item: AssetItem) -> WorksOut:
    works = list(db.scalars(select(AssetWork).where(AssetWork.item_id == item.id)
                            .order_by(AssetWork.started_on.desc(), AssetWork.id.desc())))
    cps = {c.id: c.name for c in db.scalars(select(Counterparty).where(
        Counterparty.id.in_({w.counterparty_id for w in works if w.counterparty_id})))}
    emps = {e.id: e.full_name for e in db.scalars(select(Employee).where(
        Employee.id.in_({w.employee_id for w in works if w.employee_id})))}
    total = service.modernization_total(db, [item.id]).get(item.id, Decimal("0.00"))
    return WorksOut(
        works=[WorkOut(id=w.id, kind=w.kind, kind_title=WORK_KINDS[w.kind], started_on=w.started_on,
                       finished_on=w.finished_on, description=w.description,
                       counterparty_name=cps.get(w.counterparty_id, ""), employee_name=emps.get(w.employee_id, ""),
                       amount=w.amount) for w in works],
        modernization_total=total,
        cost_with_modernization=(item.purchase_price + total) if item.purchase_price is not None else None)


def _need_service(db: Session, user: User, item: AssetItem) -> None:
    if Perm.ASSET_SERVICE not in org_permissions(db, user, item.organization_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Работы вносят инженер, АХО, бухгалтер, ФД")


@router.get("/asset-items/{item_id}/works", response_model=WorksOut)
def list_works(item_id: int, user: CurrentUser, db: DbSession):
    item, _ = _item(db, user, item_id)
    return _works_out(db, item)


@router.post("/asset-items/{item_id}/works", response_model=WorksOut, status_code=201)
def add_work(item_id: int, body: WorkIn, user: CurrentUser, db: DbSession):
    item, _ = _item(db, user, item_id)
    _need_service(db, user, item)
    _run(db, lambda: service.add_work(db, item, user, **body.model_dump()))
    db.commit()
    return _works_out(db, item)


def _work(db: Session, user: User, work_id: int) -> tuple[AssetWork, AssetItem]:
    work = db.get(AssetWork, work_id)
    if work is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Работа не найдена")
    item, _ = _item(db, user, work.item_id)
    _need_service(db, user, item)
    return work, item


@router.post("/asset-works/{work_id}/finish", response_model=WorksOut)
def finish_work(work_id: int, body: FinishIn, user: CurrentUser, db: DbSession):
    work, item = _work(db, user, work_id)
    _run(db, lambda: service.finish_work(db, work, user, body.finished_on, body.status))
    db.commit()
    return _works_out(db, item)


@router.delete("/asset-works/{work_id}", status_code=204)
def delete_work(work_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Удалить ошибочно внесённую работу (в истории единицы остаётся запись об удалении)."""
    work, item = _work(db, user, work_id)
    asset_service.event(db, item, user, "work_removed", f"{WORK_KINDS[work.kind]} с {work.started_on:%d.%m.%Y}")
    db.delete(work)
    db.commit()
    return Response(status_code=204)
