"""Заявки на оплату (docs/TZ.md, раздел 18, «Детали 8.1»).

Видеть и подавать заявки — просмотр, черновики или право «заявки» (руководитель ЦФО — только с ЦФО своей ветки,
руководитель ЦФУ — только своего ЦФУ). Согласовать, отклонить, вернуть на доработку — тот, чей сейчас шаг маршрута
(app/services/approval.py, docs/TZ.md, раздел 24); перенести и отметить оплату — POST; вернуть согласованную на
согласование — SUPERVISE.
"""

import datetime as dt
from decimal import Decimal
from pathlib import PurePosixPath
from urllib.parse import quote
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, executor_scope, get_organization_any, get_organization_for, org_permissions
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
    Membership,
    Operation,
    PaymentRequest,
    PaymentRequestEvent,
    PaymentRequestFile,
    User,
)
from app.services import approval, group_payments, periodic, suppliers
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


class FileOut(BaseModel):
    id: int
    filename: str
    size: int
    uploaded_at: dt.datetime


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
    source: str = "request"
    initiator: str = "finance"
    route: list[str] = []  # шаги маршрута: executor, cfo, fm, ceo, fd
    route_titles: list[str] = []
    step: int = 0
    step_title: str = ""  # у кого сейчас на согласовании
    can_act: bool = False  # текущий пользователь может согласовать / отклонить / вернуть на доработку
    files: list[FileOut] = []  # счёт и другие файлы заявки
    sent_at: dt.datetime | None = None  # реестр на оплату: отправлено в банк
    linked_id: int | None = None  # поступление внутри группы: выплата плательщика
    linked_number: int | None = None
    group_name: str = ""  # выплата внутри группы: юрлицо-получатель
    periodic_id: int | None = None  # периодический платёж: условия договора и месяц
    period: dt.date | None = None


class ListOut(BaseModel):
    rows: list[RequestOut]
    total_out: Decimal
    total_in: Decimal


def _names(db: Session, model, ids, label) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids)))}


def _out(db: Session, requests: list[PaymentRequest], scoped: bool, user: User | None = None) -> list[RequestOut]:
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
    linked = _names(db, PaymentRequest, ids("linked_id"), lambda x: x.number)
    files: dict[int, list[FileOut]] = {}
    for f in db.execute(select(PaymentRequestFile.id, PaymentRequestFile.request_id, PaymentRequestFile.filename,
                               PaymentRequestFile.size, PaymentRequestFile.uploaded_at)
                        .where(PaymentRequestFile.request_id.in_([r.id for r in requests]))
                        .order_by(PaymentRequestFile.id)).all():
        files.setdefault(f.request_id, []).append(FileOut(id=f.id, filename=f.filename, size=f.size,
                                                          uploaded_at=f.uploaded_at))
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
            source=r.source, initiator=r.initiator, route=list(r.route or []),
            route_titles=[approval.STEP_TITLES[x] for x in r.route or []], step=r.step,
            step_title=approval.STEP_TITLES.get(approval.current_step(r) or "", ""),
            can_act=user is not None and approval.can_act(db, user, r),
            files=files.get(r.id, []), sent_at=r.sent_at, linked_id=r.linked_id, linked_number=linked.get(r.linked_id),
            group_name=(g.short_name if (g := group_payments.receiver(db, r)) else ""),
            periodic_id=r.periodic_id, period=r.period,
        ))
    return rows


def _branch(db: Session, user: User, organization_id: int) -> set[int] | None:
    root = cfo_scope(db, user, organization_id)
    return branch_ids(db, root) if root is not None else None


def _visible(req: PaymentRequest, user: User, branch: set[int] | None, executors: set[int] | None) -> bool:
    if branch is not None and req.cfo_id not in branch:
        return False
    if executors is not None and req.executor_id not in executors and req.created_by != user.id:
        return False
    return True


def _request(db: Session, user: User, request_id: int, *perms: Perm) -> tuple[PaymentRequest, set[int] | None]:
    req = db.get(PaymentRequest, request_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    if req is None:
        raise not_found
    try:
        get_organization_any(db, user, req.organization_id, Perm.VIEW, Perm.REQUEST)
    except HTTPException:
        raise not_found from None
    branch = _branch(db, user, req.organization_id)
    if not _visible(req, user, branch, executor_scope(db, user, req.organization_id)):
        raise not_found
    if perms:
        get_organization_any(db, user, req.organization_id, *perms)
    return req, branch


def _scoped(db: Session, user: User, organization_id: int) -> bool:
    """Ограничен веткой ЦФО или своим ЦФУ: расчётный счёт не видит и не выбирает."""
    return _branch(db, user, organization_id) is not None or executor_scope(db, user, organization_id) is not None


def _status(req: PaymentRequest, *allowed: str) -> None:
    if req.status not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Заявка {STATUS_TITLES[req.status]} — это действие недоступно")


def _save(db: Session, req: PaymentRequest, branch: set[int] | None, user: User) -> RequestOut:
    executors = executor_scope(db, user, req.organization_id)
    scoped = branch is not None or executors is not None
    try:
        if scoped and req.bank_account_id is not None:
            raise RequestError("Расчётный счёт выбирает финансовая служба")
        if branch is not None and req.cash_cfo_id is not None and req.cash_cfo_id not in branch:
            raise RequestError("Касса — только ЦФО своей ветки")
        if executors is not None and req.executor_id not in executors:
            raise RequestError("ЦФУ заявки — только ваш ЦФУ")
        service.validate(db, req, branch)
        group_payments.sync(db, req)
    except RequestError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    db.refresh(req)
    return _out(db, [req], scoped, user)[0]


def _check_author(db: Session, user: User, organization_id: int) -> None:
    """Подавать и править заявки: право черновиков или руководитель ЦФУ (CEO — только согласует)."""
    if executor_scope(db, user, organization_id) is None:
        get_organization_for(db, user, organization_id, Perm.DRAFT)


def _not_linked(req: PaymentRequest) -> None:
    if req.linked_id is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Поступление внутри группы меняется вместе с выплатой плательщика")


def _check_act(db: Session, user: User, req: PaymentRequest) -> None:
    _status(req, "pending")
    if not approval.can_act(db, user, req):
        step = approval.STEP_TITLES.get(approval.current_step(req) or "", "")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Заявка ждёт согласования: {step}")


@router.get("/organizations/{organization_id}/payment-requests", response_model=ListOut)
def list_requests(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                  date_to: dt.date | None = None, status_: Annotated[str | None, Query(alias="status")] = None,
                  direction: Literal["out", "in"] | None = None, to_approve: bool = False, mine: bool = False,
                  channel: Literal["bank", "cash"] | None = None):
    """Заявки организации (руководителю ЦФО — его ветки, руководителю ЦФУ — его ЦФУ и поданные им);
    to_approve — только те, что ждут согласования текущего пользователя."""
    get_organization_any(db, user, organization_id, Perm.VIEW, Perm.REQUEST)
    if periodic.ensure(db, organization_id):  # заявки периодических платежей на текущий и два следующих месяца
        db.commit()
    branch = _branch(db, user, organization_id)
    executors = executor_scope(db, user, organization_id)
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
    if executors is not None:
        query = query.where((PaymentRequest.executor_id.in_(executors)) | (PaymentRequest.created_by == user.id))
    if to_approve:
        query = query.where(PaymentRequest.status == "pending")
    if mine:
        query = query.where(PaymentRequest.created_by == user.id)
    if channel == "cash":  # кассовые заявки
        query = query.where(PaymentRequest.cash_cfo_id.is_not(None))
    elif channel == "bank":
        query = query.where(PaymentRequest.cash_cfo_id.is_(None))
    requests = list(db.scalars(query.order_by(PaymentRequest.pay_date, PaymentRequest.number)))
    if to_approve:
        requests = [r for r in requests if approval.can_act(db, user, r)]
    rows = _out(db, requests, branch is not None or executors is not None, user)
    total = {d: sum((r.amount for r in requests if r.direction == d), Decimal("0.00")) for d in ("out", "in")}
    return ListOut(rows=rows, total_out=total["out"], total_in=total["in"])


@router.post("/organizations/{organization_id}/payment-requests", response_model=RequestOut, status_code=201)
def create_request(organization_id: int, body: RequestIn, user: CurrentUser, db: DbSession):
    get_organization_any(db, user, organization_id, Perm.DRAFT, Perm.REQUEST)
    _check_author(db, user, organization_id)
    branch = _branch(db, user, organization_id)
    data = body.model_dump()
    executors = executor_scope(db, user, organization_id)
    if executors is not None and data["executor_id"] is None:  # руководитель ЦФУ — свой ЦФУ по умолчанию
        data["executor_id"] = db.scalar(select(Membership.executor_id).where(
            Membership.user_id == user.id, Membership.organization_id == organization_id))
    req = PaymentRequest(organization_id=organization_id, created_by=user.id, status="draft",
                         initiator=approval.initiator_of(db, user, organization_id), **data)
    req.number = service.next_number(db, organization_id)
    db.add(req)
    db.flush()
    approval.event(db, req, user, "created")
    return _save(db, req, branch, user)


@router.get("/payment-requests/{request_id}", response_model=RequestOut)
def get_request(request_id: int, user: CurrentUser, db: DbSession):
    req, _ = _request(db, user, request_id)
    return _out(db, [req], _scoped(db, user, req.organization_id), user)[0]


class EventOut(BaseModel):
    at: dt.datetime
    user_name: str
    action: str
    step: str
    step_title: str
    comment: str


@router.get("/payment-requests/{request_id}/events", response_model=list[EventOut])
def request_events(request_id: int, user: CurrentUser, db: DbSession):
    """История заявки: подана, согласована (кем и на каком шаге), отклонена, на доработку, оплачена."""
    req, _ = _request(db, user, request_id)
    rows = db.execute(select(PaymentRequestEvent, User).join(User, User.id == PaymentRequestEvent.user_id)
                      .where(PaymentRequestEvent.request_id == req.id).order_by(PaymentRequestEvent.id)).all()
    return [EventOut(at=e.at, user_name=u.full_name or u.login, action=e.action, step=e.step,
                     step_title=approval.STEP_TITLES.get(e.step, ""), comment=e.comment) for e, u in rows]


@router.put("/payment-requests/{request_id}", response_model=RequestOut)
def update_request(request_id: int, body: RequestIn, user: CurrentUser, db: DbSession):
    """Правится черновик, отклонённая заявка или возвращённая на доработку (она снова становится черновиком)."""
    req, branch = _request(db, user, request_id, Perm.DRAFT, Perm.REQUEST)
    _check_author(db, user, req.organization_id)
    _status(req, "draft", "rejected", "revision")
    data = body.model_dump()
    if req.source == "supplier" and req.status == "rejected":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Отклонённая оплата поставщику не правится — запланируйте оплату в реестре накладных")
    if req.source == "supplier" and any(data[k] != getattr(req, k) for k in ("amount", "legal_entity_id", "counterparty_id")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Сумма и контрагент оплаты поставщику — из накладных: удалите заявку и запланируйте заново")
    for key, value in data.items():
        setattr(req, key, value)
    req.status, req.reject_reason = "draft", ""
    return _save(db, req, branch, user)


@router.delete("/payment-requests/{request_id}", status_code=204)
def delete_request(request_id: int, user: CurrentUser, db: DbSession) -> None:
    req, _ = _request(db, user, request_id, Perm.DRAFT, Perm.REQUEST)
    _check_author(db, user, req.organization_id)
    _status(req, "draft")
    db.delete(req)
    db.commit()


@router.post("/payment-requests/{request_id}/submit", response_model=RequestOut)
def submit_request(request_id: int, user: CurrentUser, db: DbSession):
    """На согласование по маршруту; ожидаемое поступление согласования не требует — сразу «согласована»."""
    req, branch = _request(db, user, request_id, Perm.DRAFT, Perm.REQUEST)
    _check_author(db, user, req.organization_id)
    _status(req, "draft", "rejected", "revision")
    if req.source == "supplier" and req.status == "rejected":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Отклонённая оплата поставщику не подаётся снова — запланируйте оплату в реестре накладных")
    if req.direction == "in":
        req.status, req.approved_by, req.approved_at, req.reject_reason = "approved", user.id, func.now(), ""
        approval.event(db, req, user, "approved")
    else:
        approval.submit(db, req, user)
    return _save(db, req, branch, user)


@router.post("/payment-requests/{request_id}/recall", response_model=RequestOut)
def recall_request(request_id: int, user: CurrentUser, db: DbSession):
    """Отозвать с согласования в черновик (автор или тот, кто правит черновики)."""
    req, branch = _request(db, user, request_id, Perm.REQUEST)
    _status(req, "pending")
    if req.created_by != user.id and Perm.DRAFT not in org_permissions(db, user, req.organization_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Отозвать заявку может её автор")
    req.status = "draft"
    approval.event(db, req, user, "recalled")
    return _save(db, req, branch, user)


class ApproveIn(BaseModel):
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


@router.post("/payment-requests/{request_id}/approve", response_model=RequestOut)
def approve_request(request_id: int, user: CurrentUser, db: DbSession, body: ApproveIn | None = None):
    """Согласовать на своём шаге: заявка уходит на следующий шаг, после финансового директора — в реестр."""
    req, branch = _request(db, user, request_id)
    _check_act(db, user, req)
    approval.approve(db, req, user, body.comment if body else "")
    return _save(db, req, branch, user)


@router.post("/payment-requests/{request_id}/reject", response_model=RequestOut)
def reject_request(request_id: int, body: RejectIn, user: CurrentUser, db: DbSession):
    """Отклонить: на своём шаге маршрута; согласованную — финансовый директор и администратор."""
    req, branch = _request(db, user, request_id)
    _not_linked(req)
    if req.status == "approved":
        get_organization_for(db, user, req.organization_id, Perm.SUPERVISE)
    else:
        _check_act(db, user, req)
    approval.event(db, req, user, "rejected", body.reason, approval.current_step(req) or "")
    req.status, req.reject_reason, req.approved_by, req.approved_at = "rejected", body.reason, None, None
    if req.source == "supplier":
        suppliers.release(db, req)  # накладные снова ждут оплаты; оплату планируют заново
    return _save(db, req, branch, user)


@router.post("/payment-requests/{request_id}/revise", response_model=RequestOut)
def revise_request(request_id: int, body: RejectIn, user: CurrentUser, db: DbSession):
    """Вернуть на доработку: автор правит и подаёт снова — маршрут начинается сначала."""
    req, branch = _request(db, user, request_id)
    _check_act(db, user, req)
    approval.event(db, req, user, "revision", body.reason, approval.current_step(req) or "")
    req.status, req.reject_reason = "revision", body.reason
    return _save(db, req, branch, user)


@router.post("/payment-requests/{request_id}/unapprove", response_model=RequestOut)
def unapprove_request(request_id: int, user: CurrentUser, db: DbSession):
    """Вернуть согласованную выплату на согласование финансового директора."""
    req, branch = _request(db, user, request_id, Perm.SUPERVISE)
    _not_linked(req)
    _status(req, "approved")
    if req.direction == "in":
        req.status = "draft"
    else:
        req.status = "pending"
        req.route = list(req.route or []) or ["fd"]
        req.step = len(req.route) - 1
    req.approved_by = req.approved_at = req.sent_at = req.sent_by = None
    approval.event(db, req, user, "unapproved")
    return _save(db, req, branch, user)


@router.post("/payment-requests/{request_id}/move", response_model=RequestOut)
def move_request(request_id: int, body: MoveIn, user: CurrentUser, db: DbSession):
    req, _ = _request(db, user, request_id, Perm.POST)
    _status(req, "pending", "approved")
    if req.linked_id is not None and body.pay_date != req.pay_date:
        _not_linked(req)
    if body.pay_date != req.pay_date:
        req.adjusted = True  # периодический платёж больше не возвращает дату по договору
    req.pay_date, req.bank_account_id, req.cash_cfo_id = body.pay_date, body.bank_account_id, body.cash_cfo_id
    approval.event(db, req, user, "moved", f"{body.pay_date:%d.%m.%Y}")
    return _save(db, req, _branch(db, user, req.organization_id), user)


class AmountIn(BaseModel):
    amount: Amount
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


@router.post("/payment-requests/{request_id}/amount", response_model=RequestOut)
def change_amount(request_id: int, body: AmountIn, user: CurrentUser, db: DbSession):
    """Поправить сумму в календаре (docs/TZ.md, раздел 24, «Детали 14.6»): заявка на согласовании или согласованная,
    статус не меняется, в истории — «было → стало». Корректировка — финансовый директор и администратор
    (docs/TZ.md, раздел 26, «Детали 16.2»)."""
    req, branch = _request(db, user, request_id, Perm.SUPERVISE)
    _status(req, "pending", "approved")
    _not_linked(req)
    if req.source == "supplier":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Сумма оплаты поставщику — из накладных: запланируйте оплату заново")
    if body.amount != req.amount:
        comment = f"{req.amount:.2f} → {body.amount:.2f}" + (f": {body.comment}" if body.comment else "")
        req.amount, req.adjusted, req.sent_at, req.sent_by = body.amount, True, None, None
        approval.event(db, req, user, "amount", comment)
    return _save(db, req, branch, user)


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
    approval.event(db, req, user, "paid")
    return _save(db, req, branch, user)


@router.post("/payment-requests/{request_id}/unpay", response_model=RequestOut)
def unpay_request(request_id: int, user: CurrentUser, db: DbSession):
    """Снять отметку об оплате: заявка снова «согласована», операция журнала остаётся."""
    req, branch = _request(db, user, request_id, Perm.POST)
    _status(req, "paid")
    req.status, req.paid_on, req.operation_id, req.confirmed_by_statement = "approved", None, None, False
    approval.event(db, req, user, "unpaid")
    return _save(db, req, branch, user)


# --- Файлы заявки: счёт, договор, акт (docs/TZ.md, раздел 24, «Детали 14.2») ---

MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".xlsx": XLSX_TYPE, ".xls": "application/vnd.ms-excel", ".doc": "application/msword",
              ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _can_attach(db: Session, user: User, req: PaymentRequest) -> None:
    """Прикладывать и убирать файлы: автор заявки (право черновиков, руководитель ЦФУ) или финансовая служба."""
    perms = org_permissions(db, user, req.organization_id)
    if Perm.POST not in perms:
        _check_author(db, user, req.organization_id)
    if req.status == "paid":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Заявка оплачена — файлы не меняются")


@router.post("/payment-requests/{request_id}/files", response_model=FileOut, status_code=201)
async def upload_file(request_id: int, filename: Annotated[str, Query(max_length=255)], request: Request,
                      user: CurrentUser, db: DbSession):
    """Тело запроса — файл как есть (PDF, JPG, PNG, Excel, Word), до 10 МБ."""
    req, _ = _request(db, user, request_id)
    _can_attach(db, user, req)
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
    file = PaymentRequestFile(request_id=req.id, filename=name, content_type=content_type, size=len(raw), data=raw,
                              uploaded_by=user.id)
    db.add(file)
    approval.event(db, req, user, "file", name)
    db.commit()
    db.refresh(file)
    return FileOut(id=file.id, filename=file.filename, size=file.size, uploaded_at=file.uploaded_at)


def _file(db: Session, user: User, file_id: int) -> tuple[PaymentRequestFile, PaymentRequest]:
    file = db.get(PaymentRequestFile, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    req, _ = _request(db, user, file.request_id)  # видимость — как у заявки
    return file, req


@router.get("/payment-request-files/{file_id}")
def download_file(file_id: int, user: CurrentUser, db: DbSession):
    file, _ = _file(db, user, file_id)
    quoted = quote(file.filename)
    return Response(file.data, media_type=file.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})


@router.delete("/payment-request-files/{file_id}", status_code=204)
def delete_file(file_id: int, user: CurrentUser, db: DbSession) -> Response:
    file, req = _file(db, user, file_id)
    _can_attach(db, user, req)
    if file.uploaded_by != user.id and Perm.POST not in org_permissions(db, user, req.organization_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Убрать файл может тот, кто его приложил, или финансовая служба")
    approval.event(db, req, user, "file_removed", file.filename)
    db.delete(file)
    db.commit()
    return Response(status_code=204)


@router.get("/organizations/{organization_id}/payment-requests.xlsx")
def download_requests(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                      date_to: dt.date | None = None, status_: Annotated[str | None, Query(alias="status")] = None,
                      direction: Literal["out", "in"] | None = None):
    data = list_requests(organization_id, user, db, date_from, date_to, status_, direction, False, False, None)
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
