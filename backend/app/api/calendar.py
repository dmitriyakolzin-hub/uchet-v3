"""Платёжный календарь: ожидаемые платежи (docs/TZ.md, раздел 18, «Детали 8.2»).
Просмотр — право VIEW; руководителю ЦФО недоступно (деньги организации целиком)."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Article, BankAccount, Cfo, Counterparty, LegalEntity
from app.services import calendar

router = APIRouter(prefix="/api", tags=["Платёжный календарь"])

MAX_DAYS = 400


class ExpectedItem(BaseModel):
    source: str
    source_id: int
    title: str
    date: dt.date
    direction: str
    amount: Decimal
    status: str
    overdue: bool
    legal_entity_id: int | None
    legal_entity_name: str
    bank_account_id: int | None
    cash_cfo_id: int | None
    source_name: str  # расчётный счёт или касса
    counterparty_id: int | None
    counterparty_name: str
    article_id: int | None
    article_name: str


class ExpectedOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    rows: list[ExpectedItem]
    total_in: Decimal
    total_out: Decimal


def _names(db: Session, model, ids, label) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    return {r.id: label(r) for r in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}


def period(date_from: dt.date, date_to: dt.date) -> None:
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Конец периода раньше начала")
    if (date_to - date_from).days > MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Период календаря — не больше 13 месяцев")


def expected_out(db: Session, items: list[calendar.Item]) -> list[ExpectedItem]:
    entities = _names(db, LegalEntity, [i.legal_entity_id for i in items], lambda e: e.short_name)
    banks = _names(db, BankAccount, [i.bank_account_id for i in items], lambda b: f"{b.name} ··{b.number[-4:]}")
    cfos = _names(db, Cfo, [i.cash_cfo_id for i in items], lambda c: f"Касса: {c.name}")
    counterparties = _names(db, Counterparty, [i.counterparty_id for i in items], lambda c: c.name)
    articles = _names(db, Article, [i.article_id for i in items], lambda a: a.name)
    return [ExpectedItem(
        **i.__dict__, legal_entity_name=entities.get(i.legal_entity_id, ""),
        source_name=banks.get(i.bank_account_id) or cfos.get(i.cash_cfo_id, ""),
        counterparty_name=counterparties.get(i.counterparty_id, ""), article_name=articles.get(i.article_id, ""),
    ) for i in items]


@router.get("/organizations/{organization_id}/payment-calendar/expected", response_model=ExpectedOut)
def expected(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date, date_to: dt.date,
             include_pending: bool = False, legal_entity_id: int | None = None):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    forbid_scoped(db, user, organization_id)
    period(date_from, date_to)
    if legal_entity_id is not None:
        entity = db.get(LegalEntity, legal_entity_id)
        if entity is None or entity.organization_id != organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Юрлицо не найдено в этой организации")
    items = calendar.expected(db, organization_id, date_from, date_to, include_pending=include_pending,
                              legal_entity_id=legal_entity_id)
    total = {d: sum((i.amount for i in items if i.direction == d), Decimal("0.00")) for d in ("in", "out")}
    return ExpectedOut(date_from=date_from, date_to=date_to, rows=expected_out(db, items),
                       total_in=total["in"], total_out=total["out"])
