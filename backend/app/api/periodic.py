"""Периодические платежи по договорам (docs/TZ.md, раздел 24, «Детали 14.3»).

Условия заводит и меняет финансовая служба (право проводить); видят все, кто видит заявки: руководитель ЦФО —
платежи своей ветки, руководитель ЦФУ — своего ЦФУ. Заявки месяца создаются сами (app/services/periodic.py).
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, executor_scope, get_organization_any
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Article, BankAccount, Cfo, Contract, ContractPayment, Counterparty, Executor, PaymentRequest, User
from app.services import periodic
from app.services.articles import is_group
from app.services.payment_requests import STATUS_TITLES

router = APIRouter(prefix="/api", tags=["Периодические платежи"])


class TermsIn(BaseModel):
    valid_from: dt.date
    valid_to: dt.date | None = None
    day: Annotated[int, Field(ge=1, le=31)]
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
    bank_account_id: int | None = None
    article_id: int | None = None
    cfo_id: int | None = None
    executor_id: int | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class TermsOut(BaseModel):
    id: int
    contract_id: int
    valid_from: dt.date
    valid_to: dt.date | None
    day: int
    amount: Decimal
    bank_account_id: int | None
    article_id: int | None
    cfo_id: int | None
    executor_id: int | None
    description: str


class MonthOut(BaseModel):
    period: dt.date
    pay_date: dt.date
    amount: Decimal
    request_id: int | None
    request_number: int | None
    status: str  # статус заявки или «нет заявки»
    status_title: str
    paid_on: dt.date | None


class PeriodicOut(TermsOut):
    contract_name: str
    counterparty_name: str
    article_name: str
    cfo_name: str
    executor_name: str
    months: list[MonthOut]


def _terms_out(t: ContractPayment) -> dict:
    return {k: getattr(t, k) for k in TermsOut.model_fields}


def _contract(db: Session, user: User, contract_id: int, *perms: Perm) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Договор не найден")
    try:
        get_organization_any(db, user, contract.organization_id, *perms)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Договор не найден") from None
        raise
    return contract


def _check(db: Session, contract: Contract, body: TermsIn) -> None:
    org = contract.organization_id
    if body.valid_to is not None and body.valid_to < body.valid_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="«Действует по» раньше «действует с»")
    bank = db.get(BankAccount, body.bank_account_id) if body.bank_account_id else None
    if body.bank_account_id and (bank is None or bank.legal_entity_id != contract.legal_entity_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Расчётный счёт — юрлица договора")
    if body.article_id:
        article = db.get(Article, body.article_id)
        if (article is None or article.organization_id != org or not article.is_cash_flow
                or article.cash_flow_direction != "outflow"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Статья должна быть статьёй ДДС выплат")
        if is_group(db, article.id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"«{article.name}» — группа статей, выберите подстатью")
    for model, value, title in ((Cfo, body.cfo_id, "ЦФО"), (Executor, body.executor_id, "ЦФУ")):
        if value is not None and ((obj := db.get(model, value)) is None or obj.organization_id != org):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{title} не найден в этой организации")


@router.get("/contracts/{contract_id}/payments", response_model=list[TermsOut])
def list_terms(contract_id: int, user: CurrentUser, db: DbSession):
    """Условия периодического платежа договора — вся история (последние — сверху)."""
    contract = _contract(db, user, contract_id, Perm.VIEW, Perm.REQUEST)
    return [_terms_out(t) for t in db.scalars(select(ContractPayment).where(ContractPayment.contract_id == contract.id)
                                              .order_by(ContractPayment.valid_from.desc()))]


@router.post("/contracts/{contract_id}/payments", response_model=TermsOut, status_code=201)
def add_terms(contract_id: int, body: TermsIn, user: CurrentUser, db: DbSession):
    """Новые условия с даты: прежние закрываются днём раньше; заявки месяцев подстраиваются."""
    contract = _contract(db, user, contract_id, Perm.POST)
    _check(db, contract, body)
    terms = ContractPayment(contract_id=contract.id, created_by=user.id, **body.model_dump())
    try:
        periodic.add_terms(db, contract, terms)
    except periodic.PeriodicError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    periodic.ensure(db, contract.organization_id)
    db.commit()
    db.refresh(terms)
    return _terms_out(terms)


def _terms(db: Session, user: User, terms_id: int) -> tuple[ContractPayment, Contract]:
    terms = db.get(ContractPayment, terms_id)
    if terms is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Условия не найдены")
    return terms, _contract(db, user, terms.contract_id, Perm.POST)


@router.put("/contract-payments/{terms_id}", response_model=TermsOut)
def update_terms(terms_id: int, body: TermsIn, user: CurrentUser, db: DbSession):
    """Исправить запись условий (опечатка, дата окончания); для смены суммы с даты — новая запись."""
    terms, contract = _terms(db, user, terms_id)
    _check(db, contract, body)
    for key, value in body.model_dump().items():
        setattr(terms, key, value)
    periodic.ensure(db, contract.organization_id)
    db.commit()
    db.refresh(terms)
    return _terms_out(terms)


@router.delete("/contract-payments/{terms_id}", status_code=204)
def delete_terms(terms_id: int, user: CurrentUser, db: DbSession) -> Response:
    terms, contract = _terms(db, user, terms_id)
    if db.scalar(select(PaymentRequest.id).where(PaymentRequest.periodic_id == terms.id,
                                                 PaymentRequest.status.in_(("approved", "paid"))).limit(1)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="По этим условиям уже есть согласованные или оплаченные заявки — закройте их датой «по»")
    db.delete(terms)
    db.flush()
    periodic.ensure(db, contract.organization_id)
    db.commit()
    return Response(status_code=204)


@router.get("/organizations/{organization_id}/periodic-payments", response_model=list[PeriodicOut])
def list_periodic(organization_id: int, user: CurrentUser, db: DbSession):
    """Действующие периодические платежи организации и их заявки на текущий и два следующих месяца
    (руководителю ЦФО — его ветки, руководителю ЦФУ — его ЦФУ)."""
    get_organization_any(db, user, organization_id, Perm.VIEW, Perm.REQUEST)
    periodic.ensure(db, organization_id)
    db.commit()
    root = cfo_scope(db, user, organization_id)
    branch = branch_ids(db, root) if root is not None else None
    executors = executor_scope(db, user, organization_id)
    months = periodic.months_ahead(dt.date.today())
    rows = db.execute(select(ContractPayment, Contract, Counterparty).join(Contract, Contract.id == ContractPayment.contract_id)
                      .join(Counterparty, Counterparty.id == Contract.counterparty_id)
                      .where(Contract.organization_id == organization_id,
                             (ContractPayment.valid_to.is_(None)) | (ContractPayment.valid_to >= months[0]))
                      .order_by(Counterparty.name, ContractPayment.valid_from)).all()
    names = {model: dict(db.execute(select(model.id, model.name).where(model.organization_id == organization_id)).all())
             for model in (Article, Cfo, Executor)}
    requests = {(r.contract_id, r.period): r for r in db.scalars(select(PaymentRequest).where(
        PaymentRequest.organization_id == organization_id, PaymentRequest.source == "periodic",
        PaymentRequest.period.in_(months)))}
    result = []
    for terms, contract, counterparty in rows:
        if branch is not None and terms.cfo_id not in branch:
            continue
        if executors is not None and terms.executor_id not in executors:
            continue
        month_rows = []
        for month in months:
            req = requests.get((contract.id, month))
            if req is None and periodic.active_terms([terms], month) is None:
                continue
            month_rows.append(MonthOut(
                period=month, pay_date=req.pay_date if req else periodic.pay_day(month, terms.day),
                amount=req.amount if req else terms.amount, request_id=req.id if req else None,
                request_number=req.number if req else None, status=req.status if req else "none",
                status_title=STATUS_TITLES.get(req.status, req.status) if req else "нет заявки",
                paid_on=req.paid_on if req else None))
        result.append(PeriodicOut(**_terms_out(terms), contract_name=contract.name or f"№ {contract.number}",
                                  counterparty_name=counterparty.name, article_name=names[Article].get(terms.article_id, ""),
                                  cfo_name=names[Cfo].get(terms.cfo_id, ""), executor_name=names[Executor].get(terms.executor_id, ""),
                                  months=month_rows))
    return result

