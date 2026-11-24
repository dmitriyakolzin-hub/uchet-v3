"""Заявки на имущество (docs/TZ.md, раздел 28, «Детали 19.2», «Детали 21.1»).

Видеть — право ASSETS (руководитель ЦФО — своей ветки и свои, МОЛ — где он ответственный); руководитель ЦФУ — заявки,
где его ЦФУ закупает. Подавать — ASSET_REQUEST (финансовый директор, руководитель ЦФО, инженер, оператор АХО).
Согласование — по маршруту (app/services/asset_route.py): руководители ЦФО снизу вверх, CEO (сверх бюджета),
решение — финансовый директор (SUPERVISE). Отклонить и вернуть на доработку — тот, чей шаг, или финансовый директор.
Статус поставки и приёмка — ASSETS_EDIT или ЦФУ, который закупает.
"""

import datetime as dt
from decimal import Decimal
from pathlib import PurePosixPath
from urllib.parse import quote
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import (
    branch_ids,
    cfo_scope,
    executor_scope,
    get_organization_any,
    org_permissions,
    own_only,
    role_in,
)
from app.api import assets as asset_files
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm, Role
from app.models import (
    AssetCategory,
    Contract,
    Counterparty,
    Cfo,
    Employee,
    Executor,
    InvestmentProject,
    LegalEntity,
    User,
)
from app.models.asset import (
    ASSET_REQUEST_STATUSES,
    BUDGET_MARKS,
    PURCHASE_KINDS,
    AssetItem,
    AssetReceipt,
    AssetReceiptFile,
    AssetRequest,
    AssetRequestEvent,
)
from app.services import asset_requests as service
from app.services import asset_route as route
from app.services import leasing
from app.services import assets as asset_service
from app.services.articles import labels as article_labels
from app.services.asset_requests import AssetRequestError
from app.services.payment_requests import STATUS_TITLES as PAYMENT_STATUS_TITLES

FILE_TYPES = asset_files.FILE_TYPES
MAX_FILE_BYTES = asset_files.MAX_FILE_BYTES

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
    needed_on: dt.date | None = None  # когда нужно: по бюджету этого месяца проверяется заявка
    executor_id: int | None = None  # кто закупает (ЦФУ); пусто — сам ЦФО
    # вид затрат (21.3): invest — инвестпроект + статья вложений, opex — статья расходов ОПиУ
    cost_kind: Literal["invest", "opex"] | None = None
    project_id: int | None = None
    budget_article_id: int | None = None


class CostIn(BaseModel):
    cost_kind: Literal["invest", "opex"]
    project_id: int | None = None
    budget_article_id: int | None = None


class OptionOut(BaseModel):
    id: int
    name: str


class BudgetOptionsOut(BaseModel):
    projects: list[OptionOut]
    invest_articles: list[OptionOut]
    opex_articles: list[OptionOut]


class CommentIn(BaseModel):
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class MarkIn(BaseModel):
    mark: Literal["within", "over"] | None = None  # None — снова по расчёту
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class DecisionIn(BaseModel):
    model_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    legal_entity_id: int
    counterparty_id: int | None = None  # у лизинга — потом, вместе с договором
    contract_id: int | None = None
    employee_id: int | None = None
    purchase_kind: Literal["prepay", "deferral", "leasing"]
    price: Money | None = None
    pay_date: dt.date | None = None
    bank_account_id: int | None = None
    article_id: int | None = None
    lease_payment: Money | None = None
    lease_months: Annotated[int, Field(ge=1, le=service.MAX_LEASE_MONTHS)] | None = None
    expected_on: dt.date | None = None


class LeaseIn(BaseModel):
    """Сведения о лизинге: договор (выбрать или завести) и платёж по этой заявке. Срок, первый платёж, счёт и статья —
    только для нового договора: у договора, где лизинг уже заполнен, они общие."""

    contract_id: int | None = None
    counterparty_id: int | None = None  # новый договор: лизингодатель, № и дата
    number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    contract_date: dt.date | None = None
    lease_payment: Money
    lease_months: Annotated[int, Field(ge=1, le=service.MAX_LEASE_MONTHS)] | None = None
    pay_date: dt.date | None = None  # первый платёж
    bank_account_id: int | None = None
    article_id: int | None = None


class ReasonIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class DeliveryIn(BaseModel):
    status: Literal["ordered", "shipped", "delivered"]
    on: dt.date | None = None  # дата доставки
    expected_on: dt.date | None = None


class PaymentOut(BaseModel):
    id: int
    number: int
    pay_date: dt.date | None = None
    amount: Decimal
    status: str
    status_title: str


class StepOut(BaseModel):
    key: str
    title: str
    state: Literal["done", "current", "next"]


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
    needed_on: dt.date | None
    executor_id: int | None
    executor_name: str
    amount: Decimal | None  # сколько заявка занимает бюджета: решение ФД или ориентир × количество
    cost_kind: str | None = None
    cost_kind_title: str = ""
    project_id: int | None = None
    project_name: str = ""
    budget_article_id: int | None = None
    budget_article_name: str = ""
    route: list[StepOut] = []
    step_title: str = ""  # у кого сейчас заявка
    can_act: bool = False  # пользователь — на текущем шаге (на шаге ФД — решение)
    budget_mark: str | None
    budget_mark_title: str
    budget_manual: bool
    budget_comment: str
    budget_cfo_name: str
    budget_plan: Decimal | None
    budget_used: Decimal | None
    budget_fact: Decimal | None = None
    budget_limit_left: Decimal | None = None
    budget_remaining: Decimal | None
    budget_excess: Decimal | None  # насколько сверх бюджета
    can_mark: bool = False  # финансовый директор меняет отметку о бюджете
    can_delivery: bool = False  # статус поставки
    lease_pending: bool = False  # решение «лизинг», ждём договор лизинга (21.2)
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
    received_quantity: int = 0
    receipts: list["ReceiptOut"] = []
    can_receive: bool = False  # приёмка: оператор АХО, бухгалтер, ФД, инженер, ответственный по заявке


class ReceiptFileOut(BaseModel):
    id: int
    filename: str
    size: int


class ReceiptOut(BaseModel):
    id: int
    received_on: dt.date
    employee_name: str
    quantity: int
    discrepancy: str
    numbers: list[str]
    files: list[ReceiptFileOut] = []


class ReceiveIn(BaseModel):
    received_on: dt.date
    quantity: Annotated[int, Field(ge=1, le=asset_service.MAX_COUNT)]
    employee_id: int | None = None  # кто принял
    cfo_id: int | None = None  # место
    warehouse_id: int | None = None
    mol_id: int | None = None  # МОЛ принятых единиц
    status: Literal["stock", "in_use"] = "stock"
    discrepancy: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class EventOut(BaseModel):
    at: dt.datetime
    user_name: str
    action: str
    details: str


# --- доступ ---


class Scope:
    def __init__(self, db: Session, user: User, organization_id: int):
        get_organization_any(db, user, organization_id, Perm.ASSETS, Perm.REQUEST)
        self.db = db
        self.user = user
        self.perms = org_permissions(db, user, organization_id)
        self.own = asset_service.own_employee_ids(db, user, organization_id)
        membership = role_in(db, user, organization_id)
        self.only_own = not user.is_admin and membership is not None and membership.role == Role.MOL
        root = cfo_scope(db, user, organization_id)
        self.branch = branch_ids(db, root) if root is not None else None
        # руководитель ЦФУ видит только заявки, где закупает его ЦФУ; без права на имущество — больше никто
        self.executors = executor_scope(db, user, organization_id)
        self.employee = own_only(db, user, organization_id)  # сотрудник — только свои заявки (22.2)
        if Perm.ASSETS not in self.perms and self.executors is None and not self.employee:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")

    def purchaser(self, req: AssetRequest) -> bool:
        return self.executors is not None and req.executor_id in self.executors

    def visible(self, req: AssetRequest) -> bool:
        if req.created_by == self.user.id:
            return True
        if self.employee:
            return False
        if self.executors is not None:
            return self.purchaser(req)
        if self.only_own:
            return req.employee_id in self.own
        if self.branch is not None:
            return req.cfo_id in self.branch
        return True

    def can_receive(self, req: AssetRequest) -> bool:
        return (Perm.ASSETS_EDIT in self.perms or (Perm.ASSET_REQUEST in self.perms and not self.employee)
                or self.purchaser(req)
                or (req.employee_id is not None and req.employee_id in self.own))

    def can_delivery(self, req: AssetRequest) -> bool:
        return Perm.ASSETS_EDIT in self.perms or self.purchaser(req)

    def can_act(self, req: AssetRequest) -> bool:
        return route.can_act(self.db, self.user, req, self.perms)

    def can_stop(self, req: AssetRequest) -> bool:
        """Отклонить или вернуть на доработку: тот, чей шаг, или финансовый директор."""
        return req.status == "pending" and (self.can_act(req) or Perm.SUPERVISE in self.perms)

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
    executors = _names(db, Executor, ids("executor_id"), lambda e: e.name)
    projects = _names(db, InvestmentProject, ids("project_id"), lambda p: p.name)
    articles = article_labels(db, requests[0].organization_id)
    cfo_names = dict(db.execute(select(Cfo.id, Cfo.name).where(Cfo.organization_id == requests[0].organization_id)).all())
    users = _names(db, User, ids("created_by") + ids("decided_by"), lambda u: u.full_name or u.login)
    receipts: dict[int, list[ReceiptOut]] = {}
    receipt_rows = list(db.scalars(select(AssetReceipt).where(AssetReceipt.request_id.in_(ids("id")))
                                   .order_by(AssetReceipt.received_on, AssetReceipt.id)))
    if receipt_rows:
        rids = [x.id for x in receipt_rows]
        numbers: dict[int, list[str]] = {}
        for rid, number in db.execute(select(AssetItem.receipt_id, AssetItem.number)
                                      .where(AssetItem.receipt_id.in_(rids)).order_by(AssetItem.number)).all():
            numbers.setdefault(rid, []).append(number)
        files: dict[int, list[ReceiptFileOut]] = {}
        for f in db.execute(select(AssetReceiptFile.id, AssetReceiptFile.receipt_id, AssetReceiptFile.filename,
                                   AssetReceiptFile.size).where(AssetReceiptFile.receipt_id.in_(rids))).all():
            files.setdefault(f.receipt_id, []).append(ReceiptFileOut(id=f.id, filename=f.filename, size=f.size))
        receivers = _names(db, Employee, [x.employee_id for x in receipt_rows], lambda e: e.full_name)
        for x in receipt_rows:
            receipts.setdefault(x.request_id, []).append(ReceiptOut(
                id=x.id, received_on=x.received_on, employee_name=receivers.get(x.employee_id, ""),
                quantity=x.quantity, discrepancy=x.discrepancy, numbers=numbers.get(x.id, []),
                files=files.get(x.id, [])))
    rows = []
    for r in requests:
        need = route.amount(r)
        remaining = (r.budget_plan - (r.budget_fact or Decimal("0.00")) - (r.budget_used or Decimal("0.00"))
                     if r.budget_plan is not None else None)
        excess = None
        if r.budget_mark == "over" and need is not None:
            excess = need - max(remaining, Decimal("0.00")) if remaining is not None else need
            if r.budget_limit_left is not None:
                excess = max(excess, need - max(r.budget_limit_left, Decimal("0.00")))
        steps = list(r.route or [])
        done = r.step if r.status == "pending" else (len(steps) if r.status not in service.EDITABLE else 0)
        rows.append(RequestOut(
            needed_on=r.needed_on, executor_id=r.executor_id, executor_name=executors.get(r.executor_id, ""),
            amount=need, cost_kind=r.cost_kind, cost_kind_title=route.COST_KINDS.get(r.cost_kind or "", ""),
            project_id=r.project_id, project_name=projects.get(r.project_id, ""),
            budget_article_id=r.budget_article_id, budget_article_name=articles.get(r.budget_article_id, ""),
            budget_fact=r.budget_fact, budget_limit_left=r.budget_limit_left,
            route=[StepOut(key=k, title=route.step_title(k, cfo_names),
                           state="done" if i < done else "current" if i == done and r.status == "pending" else "next")
                   for i, k in enumerate(steps)],
            step_title=route.step_title(route.current_step(r), cfo_names) if route.current_step(r) else "",
            can_act=scope.can_act(r), budget_mark=r.budget_mark,
            budget_mark_title=BUDGET_MARKS.get(r.budget_mark or "", ""), budget_manual=r.budget_manual,
            budget_comment=r.budget_comment, budget_cfo_name=cfo_names.get(r.budget_cfo_id, ""),
            budget_plan=r.budget_plan, budget_used=r.budget_used, budget_remaining=remaining, budget_excess=excess,
            can_mark=r.status == "pending" and Perm.SUPERVISE in scope.perms,
            can_delivery=r.status in ("approved", "ordered", "shipped") and scope.can_delivery(r),
            lease_pending=leasing.pending(r),
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
                      for p in service.linked_payments(db, r)] if r.status not in ("draft", "pending", "revision", "rejected") else [],
            can_edit=scope.can_edit(r),
            received_quantity=sum(x.quantity for x in receipts.get(r.id, [])), receipts=receipts.get(r.id, []),
            can_receive=r.status in service.RECEIVABLE and scope.can_receive(r),
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


def _run(db: Session, action):
    try:
        return action()
    except (AssetRequestError, route.RouteError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


# --- эндпоинты ---


@router.get("/organizations/{organization_id}/asset-requests", response_model=list[RequestOut])
def list_requests(organization_id: int, user: CurrentUser, db: DbSession,
                  status_: Annotated[str | None, Query(alias="status")] = None, mine: bool = False,
                  budget: Literal["within", "over"] | None = None, to_act: bool = False):
    """budget=over — заявки сверх бюджета (рассматриваются отдельно); to_act — ждут действия пользователя."""
    scope = Scope(db, user, organization_id)
    query = select(AssetRequest).where(AssetRequest.organization_id == organization_id)
    if status_:
        query = query.where(AssetRequest.status.in_(status_.split(",")))
    if mine:
        query = query.where(AssetRequest.created_by == user.id)
    if budget:
        query = query.where(AssetRequest.budget_mark == budget)
    requests = [r for r in db.scalars(query.order_by(AssetRequest.number.desc())) if scope.visible(r)]
    if to_act:
        requests = [r for r in requests if scope.can_act(r)]
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
    """На согласование: маршрут по ЦФО заявки и отметка о бюджете."""
    req, scope = _request(db, user, request_id)
    if not scope.can_edit(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Подать заявку может её автор или финансовый директор")
    _status(req, *service.EDITABLE)
    details = _run(db, lambda: route.submit(db, req, user))
    service.event(db, req, user, "submitted", details)
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/approve-step", response_model=RequestOut)
def approve_step(request_id: int, user: CurrentUser, db: DbSession, body: CommentIn | None = None):
    """Согласовать на шаге руководителя ЦФО или CEO; заявка переходит к следующему."""
    req, scope = _request(db, user, request_id)
    _status(req, "pending")
    if not scope.can_act(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Сейчас заявку согласует другой участник")
    step = route.current_step(req)
    title = route.step_title(step, dict(db.execute(select(Cfo.id, Cfo.name).where(Cfo.id == route.cfo_of(step))).all()))
    budget_text = _run(db, lambda: route.approve_step(db, req))
    service.event(db, req, user, "step_approved", "; ".join(x for x in (title, body.comment if body else "", budget_text) if x))
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/cost", response_model=RequestOut)
def set_cost(request_id: int, body: CostIn, user: CurrentUser, db: DbSession):
    """Финансовый директор указывает или поправляет вид затрат — бюджет заявки пересчитывается (21.3)."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    details = _run(db, lambda: route.set_cost(db, req, body.cost_kind, body.project_id, body.budget_article_id))
    service.event(db, req, user, "cost", details)
    return _save(db, req, scope)


@router.get("/organizations/{organization_id}/asset-requests/budget-options", response_model=BudgetOptionsOut)
def budget_options(organization_id: int, user: CurrentUser, db: DbSession):
    """Для формы заявки: инвестпроекты, статьи вложений и статьи расходов ОПиУ (без подстатей)."""
    from app.services import plan as plan_service

    Scope(db, user, organization_id)
    projects = db.scalars(select(InvestmentProject).where(InvestmentProject.organization_id == organization_id,
                                                          InvestmentProject.is_active,
                                                          InvestmentProject.status != "done")
                          .order_by(InvestmentProject.name))
    return BudgetOptionsOut(
        projects=[OptionOut(id=p.id, name=p.name) for p in projects],
        invest_articles=[OptionOut(id=a.id, name=a.display_name)
                         for a, _ in plan_service.plan_articles(db, organization_id, "inv")],
        opex_articles=[OptionOut(id=a.id, name=a.display_name)
                       for a, _ in plan_service.plan_articles(db, organization_id, "pl")
                       if a.pl_section in route.EXPENSE_SECTIONS])


@router.post("/asset-requests/{request_id}/revise", response_model=RequestOut)
def revise_request(request_id: int, body: ReasonIn, user: CurrentUser, db: DbSession):
    """Вернуть на доработку: автор правит и подаёт снова (маршрут — сначала)."""
    req, scope = _request(db, user, request_id)
    _status(req, "pending")
    if not scope.can_stop(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Вернуть заявку может тот, у кого она сейчас, "
                                                              "или финансовый директор")
    req.status, req.reject_reason = "revision", body.reason
    service.event(db, req, user, "revised", body.reason)
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/budget", response_model=RequestOut)
def set_budget_mark(request_id: int, body: MarkIn, user: CurrentUser, db: DbSession):
    """Финансовый директор меняет отметку «в рамках / сверх бюджета» (с комментарием) или возвращает расчёт."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    details = _run(db, lambda: route.set_mark(db, req, body.mark, body.comment))
    service.event(db, req, user, "budget", details)
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/recall", response_model=RequestOut)
def recall_request(request_id: int, user: CurrentUser, db: DbSession):
    req, scope = _request(db, user, request_id)
    if not scope.can_edit(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отозвать заявку может её автор")
    _status(req, "pending")
    req.status, req.step = "draft", 0
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
    _status(req, "pending")
    if not scope.can_stop(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отклонить заявку может тот, у кого она сейчас, "
                                                              "или финансовый директор")
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
    route.back_to_decision(req)
    req.decided_by, req.decided_at = None, None
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


@router.post("/asset-requests/{request_id}/lease", response_model=RequestOut)
def fill_lease(request_id: int, body: LeaseIn, user: CurrentUser, db: DbSession):
    """Финансовый директор заполняет лизинг, когда появился договор: договор и платёж → периодический платёж."""
    from app.models import Contract, Currency

    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    contract_id = body.contract_id
    if contract_id is None:
        cp = db.get(Counterparty, body.counterparty_id) if body.counterparty_id else None
        if cp is None or cp.organization_id != req.organization_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Выберите договор лизинга или укажите "
                                                                              "лизингодателя и № нового договора")
        if not body.number:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите № договора лизинга")
        if req.legal_entity_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="В решении не указано юрлицо")
        rub = db.scalar(select(Currency.id).where(Currency.code == "RUB"))
        contract = Contract(organization_id=req.organization_id, counterparty_id=cp.id,
                            legal_entity_id=req.legal_entity_id, currency_id=rub, kind="leasing", number=body.number,
                            contract_date=body.contract_date, name="")
        db.add(contract)
        db.flush()
        contract_id = contract.id
    details = _run(db, lambda: _lease(db, req, user, contract_id, body))
    service.event(db, req, user, "lease", details)
    return _save(db, req, scope)


def _lease(db: Session, req: AssetRequest, user: User, contract_id: int, body: LeaseIn) -> str:
    try:
        return leasing.set_lease(db, req, user, contract_id=contract_id, payment=body.lease_payment,
                                 months=body.lease_months, first_payment=body.pay_date,
                                 bank_account_id=body.bank_account_id, article_id=body.article_id)
    except leasing.LeasingError as exc:
        raise AssetRequestError(str(exc)) from None


@router.delete("/asset-requests/{request_id}/lease", response_model=RequestOut)
def remove_lease(request_id: int, user: CurrentUser, db: DbSession):
    """Снять сведения о лизинге (ошиблись договором): заявка снова «ждём договор лизинга»."""
    req, scope = _request(db, user, request_id)
    _need(scope, Perm.SUPERVISE)
    if req.purchase_kind != "leasing" or req.periodic_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Лизинг по заявке не заполнен")

    def undo():
        try:
            leasing.unset_lease(db, req)
        except leasing.LeasingError as exc:
            raise AssetRequestError(str(exc)) from None

    _run(db, undo)
    service.event(db, req, user, "lease_removed")
    return _save(db, req, scope)


@router.post("/asset-requests/{request_id}/delivery", response_model=RequestOut)
def set_delivery(request_id: int, body: DeliveryIn, user: CurrentUser, db: DbSession):
    """Статус поставки: заказано → отгружено → доставлено (оператор АХО, бухгалтер, финансовый директор, ЦФУ закупки)."""
    req, scope = _request(db, user, request_id)
    if not scope.can_delivery(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    _run(db, lambda: service.set_delivery(db, req, user, body.status, body.on, body.expected_on))
    return _save(db, req, scope)


@router.get("/asset-requests/{request_id}/events", response_model=list[EventOut])
def request_events(request_id: int, user: CurrentUser, db: DbSession):
    req, _ = _request(db, user, request_id)
    rows = db.execute(select(AssetRequestEvent, User).outerjoin(User, User.id == AssetRequestEvent.user_id)
                      .where(AssetRequestEvent.request_id == req.id).order_by(AssetRequestEvent.id)).all()
    return [EventOut(at=e.at, user_name=(u.full_name or u.login) if u else "система", action=e.action,
                     details=e.details) for e, u in rows]



# --- приёмка (шаг 19.3) ---


@router.post("/asset-requests/{request_id}/receive", response_model=RequestOut)
def receive(request_id: int, body: ReceiveIn, user: CurrentUser, db: DbSession):
    """Приёмка: каждая принятая штука становится единицей имущества (место, МОЛ, цена — из заявки и формы)."""
    req, scope = _request(db, user, request_id)
    if not scope.can_receive(req):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    _run(db, lambda: service.receive(db, req, user, **body.model_dump()))
    return _save(db, req, scope)


def _receipt(db: Session, user: User, receipt_id: int) -> tuple[AssetReceipt, AssetRequest, Scope]:
    receipt = db.get(AssetReceipt, receipt_id)
    if receipt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Приёмка не найдена")
    req, scope = _request(db, user, receipt.request_id)
    return receipt, req, scope


@router.delete("/asset-receipts/{receipt_id}", response_model=RequestOut)
def undo_receipt(receipt_id: int, user: CurrentUser, db: DbSession):
    receipt, req, scope = _receipt(db, user, receipt_id)
    _need(scope, Perm.ASSETS_EDIT)
    _run(db, lambda: service.undo_receipt(db, receipt, user))
    return _save(db, req, scope)


@router.post("/asset-receipts/{receipt_id}/files", response_model=ReceiptFileOut, status_code=201)
async def upload_receipt_file(receipt_id: int, filename: Annotated[str, Query(max_length=255)], request: Request,
                              user: CurrentUser, db: DbSession):
    """Акт приёмки, фото: PDF, JPG, PNG, Excel, Word до 10 МБ."""
    receipt, req, scope = _receipt(db, user, receipt_id)
    if not scope.can_receive(req) and Perm.ASSETS_EDIT not in scope.perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()[:255]
    content_type = FILE_TYPES.get(PurePosixPath(name).suffix.lower())
    if not name or content_type is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Можно приложить PDF, JPG, PNG, Excel или Word")
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    file = AssetReceiptFile(receipt_id=receipt.id, filename=name, content_type=content_type, size=len(raw), data=raw,
                            uploaded_by=user.id)
    db.add(file)
    service.event(db, req, user, "file", name)
    db.commit()
    db.refresh(file)
    return ReceiptFileOut(id=file.id, filename=file.filename, size=file.size)


@router.get("/asset-receipt-files/{file_id}")
def download_receipt_file(file_id: int, user: CurrentUser, db: DbSession):
    file = db.get(AssetReceiptFile, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    _receipt(db, user, file.receipt_id)
    return Response(file.data, media_type=file.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}"})


@router.delete("/asset-receipt-files/{file_id}", status_code=204)
def delete_receipt_file(file_id: int, user: CurrentUser, db: DbSession) -> Response:
    file = db.get(AssetReceiptFile, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    _, req, scope = _receipt(db, user, file.receipt_id)
    if file.uploaded_by != user.id and Perm.ASSETS_EDIT not in scope.perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Убрать файл может тот, кто его приложил, или АХО")
    service.event(db, req, user, "file_removed", file.filename)
    db.delete(file)
    db.commit()
    return Response(status_code=204)
