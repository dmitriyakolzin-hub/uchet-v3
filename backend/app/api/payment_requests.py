"""Заявки на оплату (docs/TZ.md, раздел 18, «Детали 8.1»).

Просмотр — право VIEW; подать и править черновик — DRAFT (руководитель ЦФО — только с ЦФО своей ветки и видит
только её заявки); согласовать, отклонить, перенести, отметить оплату — POST.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import (
    Article,
    BankAccount,
    Cfo,
    Contract,
    Counterparty,
    Executor,
    LegalEntity,
    Operation,
    PaymentRequest,
    User,
)
from app.services import payment_requests as service
from app.services.payment_requests import STATUS_TITLES, RequestError
from app.services.report_xlsx import XlsxColumn, build

router = APIRouter(prefix="/api", tags=["Платёжный календарь"])

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class RequestIn(BaseModel):
    direction: Literal["out", "in"] = "out"
    pay_date: dt.date
    legal_entity_id: int
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None
    counterparty_id: int | None = None
    contract_id: int | None = None
    article_id: int | None = None
    cfo_id: int | None = None
    executor_id: int | None = None
    amount: Amount
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class MoveIn(BaseModel):
    """Согласованную заявку можно перенести и выбрать счёт без повторного согласования."""

    pay_date: dt.date
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class PayIn(BaseModel):
    paid_on: dt.date
    operation_number: int | None = None  # № операции журнала, которой оплачено (необязательно)


class BudgetOut(BaseModel):
    month: str
    plan: Decimal | None
    fact: Decimal
    requested: Decimal
    remaining: Decimal | None
    over: Decimal


class RequestOut(BaseModel):
    id: int
    number: int
    direction: str
    pay_date: dt.date
    legal_entity_id: int
    legal_entity_name: str
    bank_account_id: int | None
    cash_cfo_id: int | None
    source_name: str
    counterparty_id: int | None
    counterparty_name: str
    contract_id: int | None
    contract_name: str
    article_id: int | None
    article_name: str
    cfo_id: int | None
    cfo_name: str
    executor_id: int | None
    executor_name: str
    amount: Decimal
    description: str
    status: str
    reject_reason: str
    created_by_name: str
    created_at: dt.datetime
    approved_by_name: str
    approved_at: dt.datetime | None
    paid_on: dt.date | None
    operation_id: int | None
    operation_number: int | None
    confirmed_by_statement: bool
    budget: BudgetOut | None


class ListOut(BaseModel):
    rows: list[RequestOut]
    total_out: Decimal
    total_in: Decimal


def _names(db: Session, model, ids, label) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids)))}


def _out(db: Session, requests: list[PaymentRequest], scoped: bool) -> list[RequestOut]:
    db.flush()
    budget = service.budget_checks(db, requests[0].organization_id, requests) if requests else {}

    def ids(attr):
        return [getattr(r, attr) for r in requests]

    entities = _names(db, LegalEntity, ids("legal_entity_id"), lambda e: e.short_name)
    banks = _names(db, BankAccount, ids("bank_account_id"), lambda b: f"{b.name} ··{b.number[-4:]}")
    cfos = _names(db, Cfo, ids("cash_cfo_id") + ids("cfo_id"), lambda c: c.name)
    counterparties = _names(db, Counterparty, ids("counterparty_id"), lambda c: c.name)
    contracts = _names(db, Contract, ids("contract_id"), lambda c: c.name or f"№ {c.number}")
    articles = _names(db, Article, ids("article_id"), lambda a: a.name)
    executors = _names(db, Executor, ids("executor_id"), lambda e: e.name)
    users = _names(db, User, ids("created_by") + ids("approved_by"), lambda u: u.full_name or u.login)
    operations = _names(db, Operation, ids("operation_id"), lambda o: o.number)
    rows = []
    for r in requests:
        if r.cash_cfo_id is not None:
            source = f"Касса: {cfos.get(r.cash_cfo_id, '')}"
        else:
            source = "" if scoped else banks.get(r.bank_account_id, "")
        check = budget.get(r.id)
        rows.append(RequestOut(
            id=r.id, number=r.number, direction=r.direction, pay_date=r.pay_date,
            legal_entity_id=r.legal_entity_id, legal_entity_name=entities.get(r.legal_entity_id, ""),
            bank_account_id=None if scoped else r.bank_account_id, cash_cfo_id=r.cash_cfo_id, source_name=source,
            counterparty_id=r.counterparty_id, counterparty_name=counterparties.get(r.counterparty_id, ""),
            contract_id=r.contract_id, contract_name=contracts.get(r.contract_id, ""),
            article_id=r.article_id, article_name=articles.get(r.article_id, ""),
            cfo_id=r.cfo_id, cfo_name=cfos.get(r.cfo_id, ""),
            executor_id=r.executor_id, executor_name=executors.get(r.executor_id, ""),
            amount=r.amount, description=r.description, status=r.status, reject_reason=r.reject_reason,
            created_by_name=users.get(r.created_by, ""), created_at=r.created_at,
            approved_by_name=users.get(r.approved_by, ""), approved_at=r.approved_at,
            paid_on=r.paid_on, operation_id=r.operation_id, operation_number=operations.get(r.operation_id),
            confirmed_by_statement=r.confirmed_by_statement,
            budget=BudgetOut(**check.__dict__) if check else None,
        ))
    return rows


def _branch(db: Session, user: User, organization_id: int) -> set[int] | None:
    root = cfo_scope(db, user, organization_id)
    return branch_ids(db, root) if root is not None else None


def _request(db: Session, user: User, request_id: int, perm: Perm) -> tuple[PaymentRequest, set[int] | None]:
    req = db.get(PaymentRequest, request_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    if req is None:
        raise not_found
    try:
        get_organization_for(db, user, req.organization_id, Perm.VIEW)
    except HTTPException:
        raise not_found from None
    branch = _branch(db, user, req.organization_id)
    if branch is not None and req.cfo_id not in branch:
        raise not_found
    get_organization_for(db, user, req.organization_id, perm)
    return req, branch


def _status(req: PaymentRequest, *allowed: str) -> None:
    if req.status not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Заявка {STATUS_TITLES[req.status]} — это действие недоступно")


def _save(db: Session, req: PaymentRequest, branch: set[int] | None) -> RequestOut:
    try:
        if branch is not None and req.bank_account_id is not None:
            raise RequestError("Расчётный счёт выбирает финансовый директор")
        if branch is not None and req.cash_cfo_id is not None and req.cash_cfo_id not in branch:
            raise RequestError("Касса — только ЦФО своей ветки")
        service.validate(db, req, branch)
    except RequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    db.refresh(req)
    return _out(db, [req], branch is not None)[0]


@router.get("/organizations/{organization_id}/payment-requests", response_model=ListOut)
def list_requests(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                  date_to: dt.date | None = None, status_: Annotated[str | None, Query(alias="status")] = None,
                  direction: Literal["out", "in"] | None = None):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    branch = _branch(db, user, organization_id)
    query = select(PaymentRequest).where(PaymentRequest.organization_id == organization_id)
    if date_from is not None:
        query = query.where(PaymentRequest.pay_date >= date_from)
    if date_to is not None:
        query = query.where(PaymentRequest.pay_date <= date_to)
    if status_:
        query = query.where(PaymentRequest.status.in_(status_.split(",")))
    if direction is not None:
        query = query.where(PaymentRequest.direction == direction)
    if branch is not None:
        query = query.where(PaymentRequest.cfo_id.in_(branch))
    requests = list(db.scalars(query.order_by(PaymentRequest.pay_date, PaymentRequest.number)))
    rows = _out(db, requests, branch is not None)
    total = {d: sum((r.amount for r in requests if r.direction == d), Decimal("0.00")) for d in ("out", "in")}
    return ListOut(rows=rows, total_out=total["out"], total_in=total["in"])


@router.post("/organizations/{organization_id}/payment-requests", response_model=RequestOut, status_code=201)
def create_request(organization_id: int, body: RequestIn, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.DRAFT)
    branch = _branch(db, user, organization_id)
    req = PaymentRequest(organization_id=organization_id, created_by=user.id, status="draft", **body.model_dump())
    req.number = service.next_number(db, organization_id)
    db.add(req)
    return _save(db, req, branch)


@router.get("/payment-requests/{request_id}", response_model=RequestOut)
def get_request(request_id: int, user: CurrentUser, db: DbSession):
    req, branch = _request(db, user, request_id, Perm.VIEW)
    return _out(db, [req], branch is not None)[0]


@router.put("/payment-requests/{request_id}", response_model=RequestOut)
def update_request(request_id: int, body: RequestIn, user: CurrentUser, db: DbSession):
    """Правится черновик или отклонённая заявка (она снова становится черновиком)."""
    req, branch = _request(db, user, request_id, Perm.DRAFT)
    _status(req, "draft", "rejected")
    for key, value in body.model_dump().items():
        setattr(req, key, value)
    req.status, req.reject_reason = "draft", ""
    return _save(db, req, branch)


@router.delete("/payment-requests/{request_id}", status_code=204)
def delete_request(request_id: int, user: CurrentUser, db: DbSession) -> None:
    req, _ = _request(db, user, request_id, Perm.DRAFT)
    _status(req, "draft")
    db.delete(req)
    db.commit()


@router.post("/payment-requests/{request_id}/submit", response_model=RequestOut)
def submit_request(request_id: int, user: CurrentUser, db: DbSession):
    """На согласование; ожидаемое поступление согласования не требует — сразу «согласована»."""
    req, branch = _request(db, user, request_id, Perm.DRAFT)
    _status(req, "draft", "rejected")
    if req.direction == "in":
        req.status, req.approved_by, req.approved_at = "approved", user.id, func.now()
    else:
        req.status = "pending"
    req.reject_reason = ""
    return _save(db, req, branch)


@router.post("/payment-requests/{request_id}/recall", response_model=RequestOut)
def recall_request(request_id: int, user: CurrentUser, db: DbSession):
    """Отозвать с согласования в черновик."""
    req, branch = _request(db, user, request_id, Perm.DRAFT)
    _status(req, "pending")
    req.status = "draft"
    return _save(db, req, branch)


@router.post("/payment-requests/{request_id}/approve", response_model=RequestOut)
def approve_request(request_id: int, user: CurrentUser, db: DbSession):
    req, branch = _request(db, user, request_id, Perm.POST)
    _status(req, "pending")
    req.status, req.approved_by, req.approved_at = "approved", user.id, func.now()
    return _save(db, req, branch)


@router.post("/payment-requests/{request_id}/reject", response_model=RequestOut)
def reject_request(request_id: int, body: RejectIn, user: CurrentUser, db: DbSession):
    req, branch = _request(db, user, request_id, Perm.POST)
    _status(req, "pending", "approved")
    req.status, req.reject_reason, req.approved_by, req.approved_at = "rejected", body.reason, None, None
    return _save(db, req, branch)


@router.post("/payment-requests/{request_id}/unapprove", response_model=RequestOut)
def unapprove_request(request_id: int, user: CurrentUser, db: DbSession):
    """Вернуть согласованную выплату на согласование."""
    req, branch = _request(db, user, request_id, Perm.POST)
    _status(req, "approved")
    if req.direction == "in":
        req.status = "draft"
    else:
        req.status = "pending"
    req.approved_by = req.approved_at = None
    return _save(db, req, branch)


@router.post("/payment-requests/{request_id}/move", response_model=RequestOut)
def move_request(request_id: int, body: MoveIn, user: CurrentUser, db: DbSession):
    req, _ = _request(db, user, request_id, Perm.POST)
    _status(req, "pending", "approved")
    req.pay_date, req.bank_account_id, req.cash_cfo_id = body.pay_date, body.bank_account_id, body.cash_cfo_id
    return _save(db, req, _branch(db, user, req.organization_id))


@router.post("/payment-requests/{request_id}/pay", response_model=RequestOut)
def pay_request(request_id: int, body: PayIn, user: CurrentUser, db: DbSession):
    """Отметить оплату вручную (касса, другой банк); по желанию — операция журнала."""
    req, branch = _request(db, user, request_id, Perm.POST)
    operation_id = None
    if body.operation_number is not None:
        operation_id = db.scalar(select(Operation.id).where(
            Operation.organization_id == req.organization_id, Operation.number == body.operation_number,
            Operation.budget_version_id.is_(None)))
        if operation_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail=f"Операция № {body.operation_number} не найдена в журнале")
    try:
        service.mark_paid(db, req, body.paid_on, operation_id)
    except RequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return _save(db, req, branch)


@router.post("/payment-requests/{request_id}/unpay", response_model=RequestOut)
def unpay_request(request_id: int, user: CurrentUser, db: DbSession):
    """Снять отметку об оплате: заявка снова «согласована», операция журнала остаётся."""
    req, branch = _request(db, user, request_id, Perm.POST)
    _status(req, "paid")
    req.status, req.paid_on, req.operation_id, req.confirmed_by_statement = "approved", None, None, False
    return _save(db, req, branch)


@router.get("/organizations/{organization_id}/payment-requests.xlsx")
def download_requests(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                      date_to: dt.date | None = None, status_: Annotated[str | None, Query(alias="status")] = None,
                      direction: Literal["out", "in"] | None = None):
    data = list_requests(organization_id, user, db, date_from, date_to, status_, direction)
    columns = [XlsxColumn("№", 7), XlsxColumn("Дата оплаты", 12), XlsxColumn("Вид", 12), XlsxColumn("Статус", 16),
               XlsxColumn("Юрлицо", 18), XlsxColumn("Счёт / касса", 22), XlsxColumn("Контрагент", 26),
               XlsxColumn("Договор", 18), XlsxColumn("Статья ДДС", 26), XlsxColumn("ЦФО", 20),
               XlsxColumn("Выплата", 15, money=True), XlsxColumn("Поступление", 15, money=True),
               XlsxColumn("Назначение", 40)]
    rows = [(0, [r.number, r.pay_date.strftime("%d.%m.%Y"), "Выплата" if r.direction == "out" else "Поступление",
                 STATUS_TITLES[r.status], r.legal_entity_name, r.source_name, r.counterparty_name, r.contract_name,
                 r.article_name, r.cfo_name, r.amount if r.direction == "out" else None,
                 r.amount if r.direction == "in" else None, r.description]) for r in data.rows]
    period = " — ".join(d.strftime("%d.%m.%Y") for d in (date_from, date_to) if d) or "все даты"
    content = build("Заявки на оплату", f"Период: {period}", columns, rows,
                    total=["Итого", None, None, None, None, None, None, None, None, None, data.total_out, data.total_in, None])
    return Response(content, media_type=XLSX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="payment_requests.xlsx"'})
