"""Выбытие имущества (docs/TZ.md, раздел 28, «Детали 19.6»).

Предложить — инженер, оператор АХО, бухгалтер, ФД (право ASSET_SERVICE или ASSETS_EDIT); согласовать, отклонить,
отменить проведённое — ФД (SUPERVISE); отменить предложение — автор или ФД.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import org_permissions
from app.api.assets import Scope
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import AssetItem, Contract, Counterparty, Employee, Operation, User
from app.models.asset import ACCOUNTING, DISPOSAL_KINDS, DISPOSAL_STATUSES, AssetDisposal
from app.services import asset_disposal as service
from app.services import assets as asset_service
from app.services.asset_disposal import DisposalError
from app.services.journal import JournalError

router = APIRouter(prefix="/api", tags=["Имущество"])

Price = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class DisposalIn(BaseModel):
    ids: Annotated[list[int], Field(min_length=1, max_length=asset_service.MAX_COUNT)]
    kind: Literal["sale", "writeoff", "loss"]
    on_date: dt.date
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    counterparty_id: int | None = None
    contract_id: int | None = None
    vat_rate: int = 0
    employee_id: int | None = None
    prices: dict[int, Price] = {}  # продажа: цена каждой единицы (с НДС)


class ReasonIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class DisposalItemOut(BaseModel):
    id: int
    number: str
    name: str
    accounting_title: str
    price: Decimal | None
    operation_number: int | None


class DisposalOut(BaseModel):
    id: int
    number: int
    kind: str
    kind_title: str
    on_date: dt.date
    reason: str
    counterparty_name: str
    contract_name: str
    vat_rate: int
    employee_name: str
    status: str
    status_title: str
    reject_reason: str
    created_by_name: str
    decided_by_name: str
    total: Decimal
    items: list[DisposalItemOut]
    aho_approved: bool = True
    aho_approved_by_name: str = ""
    can_aho_approve: bool = False  # согласовать как АХО (22.3)
    can_decide: bool
    can_cancel: bool
    can_undo: bool


def _run(db: Session, action) -> None:
    try:
        action()
    except (DisposalError, JournalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


def _names(db: Session, model, ids, label) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}


def _out(db: Session, disposals: list[AssetDisposal], scope: Scope, perms: frozenset) -> list[DisposalOut]:
    result = []
    cps = _names(db, Counterparty, [d.counterparty_id for d in disposals], lambda c: c.name)
    contracts = _names(db, Contract, [d.contract_id for d in disposals], lambda c: c.name or f"№ {c.number}")
    emps = _names(db, Employee, [d.employee_id for d in disposals], lambda e: e.full_name)
    users = _names(db, User, [d.created_by for d in disposals] + [d.decided_by for d in disposals]
                   + [d.aho_approved_by for d in disposals], lambda u: u.full_name or u.login)
    supervise = Perm.SUPERVISE in perms
    for d in disposals:
        rows = service.lines(db, d)
        ops = _names(db, Operation, [link.operation_id for link, _ in rows], lambda o: o.number)
        items = [DisposalItemOut(id=i.id, number=i.number, name=i.name,
                                 accounting_title=ACCOUNTING.get(i.accounting or "", "не учтена"), price=link.price,
                                 operation_number=ops.get(link.operation_id)) for link, i in rows]
        result.append(DisposalOut(
            id=d.id, number=d.number, kind=d.kind, kind_title=DISPOSAL_KINDS[d.kind], on_date=d.on_date,
            reason=d.reason, counterparty_name=cps.get(d.counterparty_id, ""),
            contract_name=contracts.get(d.contract_id, ""), vat_rate=d.vat_rate,
            employee_name=emps.get(d.employee_id, ""), status=d.status,
            status_title=("Ждёт согласования АХО" if d.status == "pending" and d.aho_approved_at is None
                          else DISPOSAL_STATUSES[d.status]),
            aho_approved=d.aho_approved_at is not None, aho_approved_by_name=users.get(d.aho_approved_by, ""),
            can_aho_approve=d.status == "pending" and d.aho_approved_at is None and scope.is_aho,
            reject_reason=d.reject_reason, created_by_name=users.get(d.created_by, ""),
            decided_by_name=users.get(d.decided_by, ""),
            total=sum((link.price or Decimal("0.00") for link, _ in rows), Decimal("0.00")), items=items,
            can_decide=d.status == "pending" and supervise and d.aho_approved_at is not None,
            can_cancel=d.status == "pending" and (d.created_by == scope.user_id or supervise),
            can_undo=d.status == "done" and supervise,
        ))
    return result


def _visible(db: Session, scope: Scope, disposal: AssetDisposal) -> bool:
    return disposal.created_by == scope.user_id or any(scope.visible(i) for _, i in service.lines(db, disposal))


@router.get("/organizations/{organization_id}/asset-disposals", response_model=list[DisposalOut])
def list_disposals(organization_id: int, user: CurrentUser, db: DbSession,
                   status_: Annotated[str | None, Query(alias="status")] = None):
    scope = Scope(db, user, organization_id)
    query = select(AssetDisposal).where(AssetDisposal.organization_id == organization_id)
    if status_:
        query = query.where(AssetDisposal.status.in_(status_.split(",")))
    disposals = [d for d in db.scalars(query.order_by(AssetDisposal.number.desc()).limit(500)) if _visible(db, scope, d)]
    return _out(db, disposals, scope, org_permissions(db, user, organization_id))


@router.post("/organizations/{organization_id}/asset-disposals", response_model=DisposalOut, status_code=201)
def create_disposal(organization_id: int, body: DisposalIn, user: CurrentUser, db: DbSession):
    scope = Scope(db, user, organization_id)
    perms = org_permissions(db, user, organization_id)
    if Perm.ASSET_SERVICE not in perms and Perm.ASSETS_EDIT not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Предлагают выбытие инженер, АХО, бухгалтер, ФД")
    items = list(db.scalars(select(AssetItem).where(AssetItem.organization_id == organization_id,
                                                    AssetItem.id.in_(body.ids)).order_by(AssetItem.number)))
    if len(items) != len(set(body.ids)) or not all(scope.visible(i) for i in items):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Часть единиц не найдена")
    holder: list[AssetDisposal] = []
    _run(db, lambda: holder.append(service.create(db, organization_id, user, items,
                                                  **body.model_dump(exclude={"ids"}))))
    db.commit()
    return _out(db, holder, scope, perms)[0]


def _disposal(db: Session, user: User, disposal_id: int) -> tuple[AssetDisposal, Scope, frozenset]:
    disposal = db.get(AssetDisposal, disposal_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Выбытие не найдено")
    if disposal is None:
        raise not_found
    try:
        scope = Scope(db, user, disposal.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    if not _visible(db, scope, disposal):
        raise not_found
    return disposal, scope, org_permissions(db, user, disposal.organization_id)


def _supervise(perms: frozenset) -> None:
    if Perm.SUPERVISE not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Решение по выбытию принимает финансовый директор")


@router.post("/asset-disposals/{disposal_id}/aho-approve", response_model=DisposalOut)
def aho_approve_disposal(disposal_id: int, user: CurrentUser, db: DbSession):
    """Согласование АХО (22.3): оператор АХО или финансовый директор; дальше решает финансовый директор."""
    disposal, scope, perms = _disposal(db, user, disposal_id)
    if not scope.is_aho:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Согласует АХО")
    _run(db, lambda: service.approve_aho(db, disposal, user))
    db.commit()
    return _out(db, [disposal], scope, perms)[0]


@router.post("/asset-disposals/{disposal_id}/approve", response_model=DisposalOut)
def approve_disposal(disposal_id: int, user: CurrentUser, db: DbSession):
    disposal, scope, perms = _disposal(db, user, disposal_id)
    _supervise(perms)
    _run(db, lambda: service.approve(db, disposal, user))
    db.commit()
    return _out(db, [disposal], scope, perms)[0]


@router.post("/asset-disposals/{disposal_id}/reject", response_model=DisposalOut)
def reject_disposal(disposal_id: int, body: ReasonIn, user: CurrentUser, db: DbSession):
    disposal, scope, perms = _disposal(db, user, disposal_id)
    if not (scope.is_aho and disposal.aho_approved_at is None):  # АХО может не согласовать (22.3)
        _supervise(perms)
    _run(db, lambda: service.close(db, disposal, user, "rejected", body.reason))
    db.commit()
    return _out(db, [disposal], scope, perms)[0]


@router.post("/asset-disposals/{disposal_id}/cancel", response_model=DisposalOut)
def cancel_disposal(disposal_id: int, user: CurrentUser, db: DbSession):
    disposal, scope, perms = _disposal(db, user, disposal_id)
    if disposal.created_by != user.id and Perm.SUPERVISE not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отменить может автор или финансовый директор")
    _run(db, lambda: service.close(db, disposal, user, "cancelled"))
    db.commit()
    return _out(db, [disposal], scope, perms)[0]


@router.post("/asset-disposals/{disposal_id}/undo", response_model=DisposalOut)
def undo_disposal(disposal_id: int, user: CurrentUser, db: DbSession):
    disposal, scope, perms = _disposal(db, user, disposal_id)
    _supervise(perms)
    _run(db, lambda: service.undo(db, disposal, user))
    db.commit()
    return _out(db, [disposal], scope, perms)[0]
