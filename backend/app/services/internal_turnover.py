"""Внутренние обороты между направлениями — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.1»).

Передача от ЦФО-продавца ЦФО-покупателю — одна проводка Дт 90.02 [покупатель, «Внутренняя закупка»] —
Кт 90.01 [продавец, «Внутренняя выручка»]: у продавца выручка, у покупателя расход, у компании в целом — ноль.
Деньги и баланс не меняются. Только внутри одного юрлица: продажи между юрлицами группы — настоящие продажи.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Cfo, Operation, User
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import journal
from app.services.articles import system_article
from app.services.plan import _legal_entity


class InternalError(ValueError):
    pass


def articles(db: Session, organization_id: int):
    revenue = system_article(db, organization_id, "internal_revenue")
    purchase = system_article(db, organization_id, "internal_purchase")
    if revenue is None or purchase is None:
        raise InternalError("Нет системных статей «Внутренняя выручка» и «Внутренняя закупка»")
    return revenue, purchase


def create_transfer(db: Session, organization_id: int, user: User, *, on_date: dt.date, from_cfo_id: int,
                    to_cfo_id: int, amount: Decimal, description: str) -> Operation:
    """Проведённая операция внутренней передачи."""
    cfos = {}
    for cfo_id, title in ((from_cfo_id, "Продавец (ЦФО)"), (to_cfo_id, "Покупатель (ЦФО)")):
        cfo = db.get(Cfo, cfo_id)
        if cfo is None or cfo.organization_id != organization_id:
            raise InternalError(f"{title}: не найден в этой организации")
        cfos[cfo_id] = cfo
    if from_cfo_id == to_cfo_id:
        raise InternalError("Продавец и покупатель — один и тот же ЦФО")
    if amount <= 0:
        raise InternalError("Сумма должна быть больше нуля")
    entity = _legal_entity(db, organization_id, cfos[from_cfo_id])
    if _legal_entity(db, organization_id, cfos[to_cfo_id]) != entity:
        raise InternalError("ЦФО относятся к разным юрлицам — это продажа внутри группы, а не внутренняя передача")
    revenue, purchase = articles(db, organization_id)
    codes = dict(db.execute(select(Account.code, Account.id).where(Account.code.in_(("90.01", "90.02")))).all())
    text = description.strip() or f"Внутренняя передача: {cfos[from_cfo_id].name} → {cfos[to_cfo_id].name}"
    try:
        return journal.create_posted(db, organization_id, OperationIn(
            legal_entity_id=entity, op_date=on_date, article_id=revenue.id, description=text,
            postings=[PostingIn(debit_account_id=codes["90.02"], credit_account_id=codes["90.01"], amount=amount,
                                debit=AnalyticsIn(cfo=to_cfo_id, pl_item=purchase.id),
                                credit=AnalyticsIn(cfo=from_cfo_id, pl_item=revenue.id))]), user)
    except journal.JournalError as exc:
        raise InternalError(f"Проводка не прошла проверку журнала: {exc}") from None
