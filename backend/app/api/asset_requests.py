"""Заявки на имущество (docs/TZ.md, раздел 28, «Детали 19.2»).

Видеть — право ASSETS (руководитель ЦФО — своей ветки и свои, МОЛ — где он ответственный); подавать — ASSET_REQUEST
(финансовый директор, инженер, оператор АХО); решение, отклонение, отмена — SUPERVISE (финансовый директор);
статус поставки — ASSETS_EDIT.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, get_organization_for, org_permissions, role_in
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm, Role
from app.models import (
    AssetCategory,
    Contract,
    Counterparty,
    Employee,
    LegalEntity,
    User,
)
from app.models.asset import ASSET_REQUEST_STATUSES, PURCHASE_KINDS, AssetRequest, AssetRequestEvent
from app.services import asset_requests as service
from app.services import assets as asset_service
from app.services.asset_requests import AssetRequestError
from app.services.payment_requests import STATUS_TITLES as PAYMENT_STATUS_TITLES

router = APIRouter(prefix="/api", tags=["Имущество"])

Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class RequestIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    quantity: Annotated[int, Field(ge=1, le=asset_service.MAX_COUNT)] = 1
    category_id: int | None = None
    cfo_id: int | None = None
    legal_entity_id: int | None = None
    justification: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    estimated_price: Money | None = None


class DecisionIn(BaseModel):
    model_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    legal_entity_id: int
    counterparty_id: int
    contract_id: int | None = None
    employee_id: int | None = None
    purchase_kind: Literal["prepay", "deferral", "leasing"]
    price: Money | None = None
    pay_date: dt.date
    bank_account_id: int | None = None
    article_id: int | None = None
    lease_payment: Money | None = None
    lease_months: Annotated[int, Field(ge=1, le=service.MAX_LEASE_MONTHS)] | None = None
    expected_on: dt.date | None = None


class ReasonIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class DeliveryIn(BaseModel):
    status: Literal["ordered", "shipped", "delivered"]
    on: dt.date | None = None  # дата доставки
    expected_on: dt.date | None = None


class PaymentOut(BaseModel):
    id: int
    number: int
    pay_date: dt.date
    amount: Decimal
    status: str
    status_title: str


class RequestOut(BaseModel):
    id: int
    organization_id: int
    number: int
    status: str
    status_title: str
    name: str
    quantity: int
    category_id: int | None
    category_name: str
    cfo_id: int | None
    cfo_name: str
    legal_entity_id: int | None
    legal_entity_name: str
    justification: str
    estimated_price: Decimal | None
    model_name: str
    counterparty_id: int | None
    counterparty_name: str
    contract_id: int | None
    contract_name: str
    employee_id: int | None
    employee_name: str
    purchase_kind: str | None
    purchase_kind_title: str
    price: Decimal | None
    total: Decimal | None
    pay_date: dt.date | None
    bank_account_id: int | None
    article_id: int | None
    lease_payment: Decimal | None
    lease_months: int | None
    reject_reason: str
    expected_on: dt.date | None
    delivered_on: dt.date | None
    created_by_name: str
    created_at: dt.datetime
    decided_by_name: str
    decided_at: dt.datetime | None
    payments: list[PaymentOut] = []
    can_edit: bool = False  # правка и подача (автор или ФД)


class EventOut(BaseModel):
    at: dt.datetime
    user_name: str
    action: str
    details: str


# --- доступ ---


class Scope:
    def __init__(self, db: Session, user: User, organization_id: int):
        get_organization_for(db, user, organization_id, Perm.ASSETS)
        self.user = user
        self.perms = org_permissions(db, user, organization_id)
        self.own = asset_service.own_employee_ids(db, user, organization_id)
        membership = role_in(db, user, organization_id)
        self.only_own = not user.is_admin and membership is not None and membership.role == Role.MOL
        root = cfo_scope(db, user, organization_id)
        self.branch = branch_ids(db, root) if root is not None else None

    def visible(self, req: AssetRequest) -> bool:
        if req.created_by == self.user.id:
            return True
        if self.only_own:
            return req.employee_id in self.own
        if self.branch is not None:
            return req.cfo_id in self.branch
        return True

    def can_edit(self, req: AssetRequest) -> bool:
        return Perm.SUPERVISE in self.perms or (Perm.ASSET_REQUEST in self.perms and req.created_by == self.user.id)


def _request(db: Session, user: User, request_id: int) -> tuple[AssetRequest, Scope]:
    req = db.get(AssetRequest, request_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Заявка на имущество не найдена")
    if req is None:
        raise not_found
    try:
        scope = Scope(db, user, req.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    if not scope.visible(req):
        raise not_found
    return req, scope


def _need(scope: Scope, perm: Perm) -> None:
    if perm not in scope.perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")


def _status(req: AssetRequest, *allowed: str) -> None:
    if req.status not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Заявка: {ASSET_REQUEST_STATUSES[req.status].lower()} — это действие недоступно")


def _names(db: Session, model, ids, label) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}


def _out(db: Session, requests: list[AssetRequest], scope: Scope) -> list[RequestOut]:
    if not requests:
        return []

    def ids(attr):
        return [getattr(r, attr) for r in requests]

    cfos = asset_service.cfo_labels(db, requests[0].organization_id)
    categories = _names(db, AssetCategory, ids("category_id"), lambda c: c.name)
    entities = _names(db, LegalEntity, ids("legal_entity_id"), lambda e: e.short_name)
    counterparties = _names(db, Counterparty, ids("counterparty_id"), lambda c: c.name)
    contracts = _names(db, Contract, ids("contract_id"), lambda c: c.name or f"№ {c.number}")
    employees = _names(db, Employee, ids("employee_id"), lambda e: e.full_name)
    users = _names(db, User, ids("created_by") + ids("decided_by"), lambda u: u.full_name or u.login)
    rows = []
    for r in requests:
        rows.append(RequestOut(
            id=r.id, organization_id=r.organization_id, number=r.number, status=r.status,
            status_title=ASSET_REQUEST_STATUSES[r.status], name=r.name, quantity=r.quantity,
            category_id=r.category_id, category_name=categories.get(r.category_id, ""), cfo_id=r.cfo_id,
            cfo_name=cfos.get(r.cfo_id, ""), legal_entity_id=r.legal_entity_id,
            legal_entity_name=entities.get(r.legal_entity_id, ""), justification=r.justification,
            estimated_price=r.estimated_price, model_name=r.model_name, counterparty_id=r.counterparty_id,
            counterparty_name=counterparties.get(r.counterparty_id, ""), contract_id=r.contract_id,
            contract_name=contracts.get(r.contract_id, ""), employee_id=r.employee_id,
            employee_name=employees.get(r.employee_id, ""), purchase_kind=r.purchase_kind,
            purchase_kind_title=PURCHASE_KINDS.get(r.purchase_kind or "", ""), price=r.price, total=service.total(r),
            pay_date=r.pay_date, bank_account_id=r.bank_account_id, article_id=r.article_id,
            lease_payment=r.lease_payment, lease_months=r.lease_months, reject_reason=r.reject_reason,
            expected_on=r.expected_on, delivered_on=r.delivered_on, created_by_name=users.get(r.created_by, ""),
            created_at=r.created_at, decided_by_name=users.get(r.decided_by, ""), decided_at=r.decided_at,
            payments=[PaymentOut(id=p.id, number=p.number, pay_date=p.pay_date, amount=p.amount, status=p.status,
                                 status_title=PAYMENT_STATUS_TITLES[p.status])
                      for p in service.linked_payments(db, r)] if r.status not in ("draft", "pending", "rejected") else [],
            can_edit=scope.can_edit(r),
        ))
    return rows


def _save(db: Session, req: AssetRequest, scope: Scope) -> RequestOut:
    try:
        service.validate(db, req)
    except AssetRequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    db.refresh(req)
    return _out(db, [req], scope)[0]


def _run(db: Session, action) -> None:
    try:
        action()
    except AssetRequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


# --- эндпоинты ---


@router.get("/organizations/{organization_id}/asset-requests", response_model=list[RequestOut])
def list_requests(organization_id: int, user: CurrentUser, db: DbSession,
                  status_: Annotated[str | None, Query(alias="status")] = None, mine: bool = False):
    scope = Scope(db, user, organization_id)
    query = select(AssetRequest).where(AssetRequest.organization_id == organization_id)
    if status_:
        query = query.where(AssetRequest.status.in_(status_.split(",")))
    if mine:
        query = query.where(AssetRequest.created_by == user.id)
    requests = [r for r in db.scalars(query.order_by(AssetRequest.number.desc())) if scope.visible(r)]
    return _out(db, requests, scope)


@router.post("/organizations/{organization_id}/asset-requests", response_model=RequestOut, status_code=201)
def create_request(organization_id: int, body: RequestIn, user: CurrentUser, db: DbSession):
    scope = Scope(db, user, organization_id)
    _need(scope, Perm.ASSET_REQUEST)
    if scope.branch is not None and body.cfo_id not in scope.branch:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите ЦФО своей ветки")
    req = AssetRequest(organization_id=organization_id, number=service.next_number(db, organization_id),
                       created_by=user.id, **body.model_dump())
    db.add(req)
    db.flush()
    service.event(db, req, user, "created")
    return _save(db, req, scope)


@router.get("/asset-requests/{request_id}", response_model=RequestOut)
def get_request(request_id: int, user: CurrentUser, db: DbSession):
    req, scope = _request(db, user, request_id)
    return _out(db, [req], scope)[0]


@router.put("/asset-requests/{request_id}", response_model=RequestOut)
def update_request(request_id: int, body: RequestIn, user: CurrentUser, db: DbSession):
    req, scope = _request(db, user, request_id)
    if not scope.can_edit(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Править заявку может её автор или финансовый директор")
    _status(req, *service.EDITABLE)
    for key, value in body.model_dump().items():
        setattr(req, key, value)
    req.status = "draft"
    service.event(db, req, user, "edited")
    return _save(db, req, scope)


@router.delete("/asset-requests/{request_id}", status_code=204)
def delete_request(request_id: int, user: CurrentUser, db: DbSession) -> Response:
    req, scope = _request(db, user, request_id)
    if not scope.can_edit(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Удалить заявку может её автор или финансовый директор")
    _status(req, *service.EDITABLE)
    db.delete(req)
    db.commit()
    return Response(status_code=204)


@router.post("/asset-requests/{request_id}/submit", response_model=RequestOut)
def submit_request(request_id: int, user: CurrentUser, db: DbSession):
    """На решение финансового директора."""
    req, scope = _request(db, user, request_id)
    if not scope.can_edit(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Подать заявку может её автор или финансовый директор")
    _status(req, *service.EDITABLE)
    req.status, req.reject_reason = "pending", ""
    service.event(db, req, user, "submitted")
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/recall", response_model=RequestOut)
def recall_request(request_id: int, user: CurrentUser, db: DbSession):
    req, scope = _request(db, user, request_id)
    if not scope.can_edit(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отозвать заявку может её автор")
    _status(req, "pending")
    req.status = "draft"
    service.event(db, req, user, "recalled")
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/approve", response_model=RequestOut)
def approve_request(request_id: int, body: DecisionIn, user: CurrentUser, db: DbSession):
    """Решение финансового директора: наименование, поставщик, ответственный, вариант закупки → оплаты в календаре."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    _run(db, lambda: service.decide(db, req, user, body.model_dump()))
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/reject", response_model=RequestOut)
def reject_request(request_id: int, body: ReasonIn, user: CurrentUser, db: DbSession):
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    _status(req, "pending")
    req.status, req.reject_reason = "rejected", body.reason
    req.decided_by, req.decided_at = user.id, dt.datetime.now(dt.UTC)
    service.event(db, req, user, "rejected", body.reason)
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/unapprove", response_model=RequestOut)
def unapprove_request(request_id: int, user: CurrentUser, db: DbSession):
    """Вернуть на решение: оплаты убираются из календаря (если ещё ничего не оплачено)."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    _status(req, "approved")
    _run(db, lambda: service.remove_payments(db, req))
    req.status, req.decided_by, req.decided_at = "pending", None, None
    service.event(db, req, user, "unapproved")
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/cancel", response_model=RequestOut)
def cancel_request(request_id: int, body: ReasonIn, user: CurrentUser, db: DbSession):
    """Отменить закупку (поставщик не поставит, передумали): оплаты убираются, если ещё ничего не оплачено."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    _status(req, "pending", "approved", "ordered", "shipped", "delivered")
    _run(db, lambda: service.remove_payments(db, req))
    req.status = "cancelled"
    service.event(db, req, user, "cancelled", body.reason)
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/delivery", response_model=RequestOut)
def set_delivery(request_id: int, body: DeliveryIn, user: CurrentUser, db: DbSession):
    """Статус поставки: заказано → отгружено → доставлено (оператор АХО, бухгалтер, финансовый директор)."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.ASSETS_EDIT)
    _run(db, lambda: service.set_delivery(db, req, user, body.status, body.on, body.expected_on))
    return _save(db, req, scope)


@router.get("/asset-requests/{request_id}/events", response_model=list[EventOut])
def request_events(request_id: int, user: CurrentUser, db: DbSession):
    req, _ = _request(db, user, request_id)
    rows = db.execute(select(AssetRequestEvent, User).outerjoin(User, User.id == AssetRequestEvent.user_id)
                      .where(AssetRequestEvent.request_id == req.id).order_by(AssetRequestEvent.id)).all()
    return [EventOut(at=e.at, user_name=(u.full_name or u.login) if u else "система", action=e.action,
                     details=e.details) for e, u in rows]

