"""Договоры лизинга (docs/TZ.md, раздел 28, «Детали 21.2»): по каждому договору — имущество, заявки и платежи.

Смотреть — право на имущество и на просмотр учёта (финансовый директор, бухгалтер, финансовый менеджер, CEO);
руководителю ЦФО — недоступно.
"""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Counterparty, LegalEntity
from app.models.asset import ASSET_REQUEST_STATUSES, ASSET_STATUSES
from app.services import leasing
from app.services.assets import cfo_labels, contract_label
from app.services.payment_requests import STATUS_TITLES

router = APIRouter(prefix="/api", tags=["Имущество"])


class LeaseItemOut(BaseModel):
    id: int
    number: str
    name: str
    cfo_name: str
    status_title: str
    upgraded_price: Decimal | None
    request_number: int | None


class LeaseRequestOut(BaseModel):
    id: int
    number: int
    name: str
    quantity: int
    lease_payment: Decimal | None
    status_title: str


class LeasePaymentOut(BaseModel):
    number: int
    pay_date: dt.date
    amount: Decimal
    status: str
    status_title: str


class LeaseContractOut(BaseModel):
    id: int
    title: str
    legal_entity_id: int
    counterparty_id: int
    counterparty_name: str
    legal_entity_name: str
    payment: Decimal | None  # в месяц
    months: int | None
    start: dt.date | None
    end: dt.date | None
    total: Decimal | None  # платёж × срок
    paid: Decimal
    remaining: Decimal | None
    requests: list[LeaseRequestOut]
    items: list[LeaseItemOut]
    payments: list[LeasePaymentOut]


@router.get("/organizations/{organization_id}/leasing", response_model=list[LeaseContractOut])
def leasing_contracts(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.ASSETS)
    get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    views = leasing.overview(db, organization_id)
    cfos = cfo_labels(db, organization_id)
    counterparties = dict(db.execute(select(Counterparty.id, Counterparty.name)
                                     .where(Counterparty.organization_id == organization_id)).all())
    entities = dict(db.execute(select(LegalEntity.id, LegalEntity.short_name)
                               .where(LegalEntity.organization_id == organization_id)).all())
    result = []
    for v in views:
        by_receipt = {}
        for r in v.requests:
            for item in leasing.request_items(db, r):
                by_receipt[item.id] = r.number
        result.append(LeaseContractOut(
            id=v.contract.id, title=contract_label(v.contract), legal_entity_id=v.contract.legal_entity_id,
            counterparty_id=v.contract.counterparty_id,
            counterparty_name=counterparties.get(v.contract.counterparty_id, ""),
            legal_entity_name=entities.get(v.contract.legal_entity_id, ""),
            payment=v.terms.amount if v.terms else None, months=v.months,
            start=v.terms.valid_from if v.terms else None, end=v.terms.valid_to if v.terms else None,
            total=v.total, paid=v.paid, remaining=(v.total - v.paid) if v.total is not None else None,
            requests=[LeaseRequestOut(id=r.id, number=r.number, name=r.model_name or r.name, quantity=r.quantity,
                                      lease_payment=r.lease_payment, status_title=ASSET_REQUEST_STATUSES[r.status])
                      for r in v.requests],
            items=[LeaseItemOut(id=i.id, number=i.number, name=i.name, cfo_name=cfos.get(i.cfo_id, ""),
                                status_title=ASSET_STATUSES[i.status], upgraded_price=i.upgraded_price,
                                request_number=by_receipt.get(i.id)) for i in v.items],
            payments=[LeasePaymentOut(number=p.number, pay_date=p.pay_date, amount=p.amount, status=p.status,
                                      status_title=STATUS_TITLES[p.status]) for p in v.payments]))
    return result
