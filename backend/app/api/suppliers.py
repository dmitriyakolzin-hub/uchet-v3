"""Реестр накладных поставщиков (docs/TZ.md, раздел 24, «Детали 14.5»). Всё — право проводить (финансовый
менеджер, финансовый директор, администратор)."""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.api.payment_requests import XLSX_TYPE, RequestOut, _out
from app.core.permissions import Perm
from app.models import Contract, Counterparty, LegalEntity, SupplierInvoice
from app.services import group_payments, suppliers
from app.services.excel_import import ExcelError
from app.services.payment_requests import STATUS_TITLES

router = APIRouter(prefix="/api", tags=["Платёжный календарь"])

MAX_UPLOAD = 20 * 1024 * 1024
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class PaymentRef(BaseModel):
    id: int
    number: int
    status: str
    status_title: str
    pay_date: dt.date
    amount: Decimal


class InvoiceOut(BaseModel):
    id: int
    kind: str
    number: str
    doc_date: dt.date
    due_date: dt.date
    overdue_days: int
    amount: Decimal
    paid: Decimal
    planned: Decimal
    remaining: Decimal
    payments: list[PaymentRef]


class SupplierGroup(BaseModel):
    legal_entity_id: int
    legal_entity_name: str
    counterparty_id: int
    counterparty_name: str
    group_name: str  # контрагент — юрлицо группы: оплата без согласования
    deferral_days: int | None
    debt_limit: Decimal | None
    debt: Decimal
    overdue: Decimal
    remaining: Decimal
    over_limit: bool
    invoices: list[InvoiceOut]


class InvoicesOut(BaseModel):
    groups: list[SupplierGroup]
    debt: Decimal
    overdue: Decimal
    remaining: Decimal


class ImportOut(BaseModel):
    rows: int
    created: int
    duplicates: int
    zero: int
    errors: list[tuple[int, str]]
    missing: list[tuple[str, int]]


class PlanIn(BaseModel):
    legal_entity_id: int
    counterparty_id: int
    pay_date: dt.date
    amount: Amount | None = None  # пусто — весь долг по выбранным накладным
    invoice_ids: Annotated[list[int], Field(max_length=2000)] = []
    bank_account_id: int | None = None


@router.get("/organizations/{organization_id}/supplier-invoices", response_model=InvoicesOut)
def list_invoices(organization_id: int, user: CurrentUser, db: DbSession, legal_entity_id: int | None = None,
                  counterparty_id: int | None = None, open_only: bool = True):
    """Накладные по юрлицу и контрагенту: срок оплаты, просрочка, оплачено, запланировано, остаток; лимит договора."""
    get_organization_for(db, user, organization_id, Perm.POST)
    today = dt.date.today()
    all_states = suppliers.states(db, suppliers.invoices_of(db, organization_id, legal_entity_id, counterparty_id))
    entities = {e.id: e for e in db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id))}
    groups: dict[tuple[int, int], list] = {}
    for st in all_states:
        if open_only and st.debt <= 0:
            continue
        groups.setdefault((st.invoice.legal_entity_id, st.invoice.counterparty_id), []).append(st)
    counterparties = {c.id: c for c in db.scalars(select(Counterparty).where(
        Counterparty.id.in_({k[1] for k in groups})))}
    contracts = {c.id: c for c in db.scalars(select(Contract).where(
        Contract.id.in_({st.invoice.contract_id for sts in groups.values() for st in sts if st.invoice.contract_id})))}
    result = []
    for (entity_id, cp_id), sts in groups.items():
        terms = [contracts[i] for i in {st.invoice.contract_id for st in sts} if i in contracts]
        limit = max((c.debt_limit for c in terms if c.debt_limit), default=None)
        deferral = terms[0].deferral_days if len(terms) == 1 else None
        debt = sum((st.debt for st in sts), Decimal("0.00"))
        group = group_payments.receiver_by_inn(db, organization_id, entity_id, counterparties[cp_id].inn)
        result.append(SupplierGroup(
            legal_entity_id=entity_id, legal_entity_name=entities[entity_id].short_name, counterparty_id=cp_id,
            counterparty_name=counterparties[cp_id].name, group_name=group.short_name if group else "",
            deferral_days=deferral, debt_limit=limit, debt=debt,
            overdue=sum((st.debt for st in sts if st.due < today), Decimal("0.00")),
            remaining=sum((st.remaining for st in sts), Decimal("0.00")),
            over_limit=limit is not None and debt > limit,
            invoices=[InvoiceOut(
                id=st.invoice.id, kind=st.invoice.kind, number=st.invoice.number, doc_date=st.invoice.doc_date,
                due_date=st.due, overdue_days=max(0, (today - st.due).days) if st.debt > 0 else 0,
                amount=st.invoice.amount, paid=st.paid, planned=st.planned, remaining=st.remaining,
                payments=[PaymentRef(id=r.id, number=r.number, status=r.status, status_title=STATUS_TITLES[r.status],
                                     pay_date=r.pay_date, amount=a) for r, a in st.requests])
                for st in sts]))
    result.sort(key=lambda g: (g.counterparty_name.lower(), g.legal_entity_name))
    return InvoicesOut(groups=result, debt=sum((g.debt for g in result), Decimal("0.00")),
                       overdue=sum((g.overdue for g in result), Decimal("0.00")),
                       remaining=sum((g.remaining for g in result), Decimal("0.00")))


@router.get("/organizations/{organization_id}/supplier-invoices/template.xlsx")
def invoices_template(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.POST)
    return Response(suppliers.template(), media_type=XLSX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="supplier_invoices.xlsx"'})


@router.post("/organizations/{organization_id}/supplier-invoices/import", response_model=ImportOut)
async def import_invoices(organization_id: int, request: Request, user: CurrentUser, db: DbSession,
                          legal_entity_id: int | None = None):
    """Тело запроса — файл Excel; юрлицо-покупатель — параметр (или колонка «Юрлицо» в файле)."""
    raw = await request.body()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 20 МБ")

    def run():
        get_organization_for(db, user, organization_id, Perm.POST)
        try:
            result = suppliers.import_invoices(db, organization_id, user, raw, legal_entity_id)
        except (ExcelError, suppliers.SupplierError) as exc:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
        db.commit()
        return ImportOut(**result.__dict__)

    return await run_in_threadpool(run)


@router.delete("/supplier-invoices/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, user: CurrentUser, db: DbSession) -> Response:
    inv = db.get(SupplierInvoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Накладная не найдена")
    try:
        get_organization_for(db, user, inv.organization_id, Perm.POST)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Накладная не найдена") from None
        raise
    if suppliers.states(db, [inv])[0].requests:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="По накладной есть заявки на оплату — сначала удалите или отклоните их")
    db.delete(inv)
    db.commit()
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/supplier-payments", response_model=RequestOut, status_code=201)
def plan_payment(organization_id: int, body: PlanIn, user: CurrentUser, db: DbSession):
    """Запланировать оплату: заявка на дату оплаты, сумма — от старых накладных к новым (можно частично)."""
    get_organization_for(db, user, organization_id, Perm.POST)
    try:
        req = suppliers.plan_payment(db, organization_id, user, body.legal_entity_id, body.counterparty_id,
                                     body.pay_date, body.amount, body.invoice_ids or None, body.bank_account_id)
    except suppliers.SupplierError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    db.refresh(req)
    return _out(db, [req], False, user)[0]
