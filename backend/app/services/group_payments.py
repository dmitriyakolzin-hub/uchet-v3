"""Платежи внутри группы (docs/TZ.md, раздел 24, «Детали 14.5»; решение владельца — вариант «б»).

Выплата контрагенту, у которого ИНН одного из юрлиц той же организации, — перевод внутри группы: согласования
не требует (подача сразу делает её согласованной), а у юрлица-получателя появляется парная заявка-поступление
(linked_id → выплата). Поступление меняется вместе с выплатой: дата, сумма; выплату вернули или удалили —
неоплаченное поступление удаляется. Отмечает оплату каждая сторона сама (выпиской своего банка или вручную).
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankAccount, Counterparty, LegalEntity, PaymentRequest, PaymentRequestEvent
from app.services import payment_requests as service
from app.services.articles import system_article

ACTIVE = ("approved", "paid")


def receiver_by_inn(db: Session, organization_id: int, payer_id: int, inn: str | None) -> LegalEntity | None:
    """Другое юрлицо той же организации с этим ИНН."""
    if not inn:
        return None
    return db.scalar(select(LegalEntity).where(LegalEntity.organization_id == organization_id, LegalEntity.inn == inn,
                                               LegalEntity.id != payer_id).limit(1))


def receiver(db: Session, req: PaymentRequest) -> LegalEntity | None:
    """Юрлицо группы, которому платит выплата (по ИНН контрагента), или None."""
    if req.direction != "out" or req.counterparty_id is None or req.linked_id is not None:
        return None
    counterparty = db.get(Counterparty, req.counterparty_id)
    return receiver_by_inn(db, req.organization_id, req.legal_entity_id, counterparty.inn if counterparty else None)


def mirror_of(db: Session, req: PaymentRequest) -> PaymentRequest | None:
    return db.scalar(select(PaymentRequest).where(PaymentRequest.linked_id == req.id)) if req.id else None


def sync(db: Session, req: PaymentRequest) -> None:
    """Привести поступление получателя в соответствие с выплатой."""
    db.flush()
    mirror = mirror_of(db, req)
    target = receiver(db, req)
    if target is None or req.status not in ACTIVE:
        if mirror is not None and mirror.status != "paid":
            db.delete(mirror)
            db.flush()
        return
    if mirror is not None and mirror.status == "paid":
        return
    payer = db.get(LegalEntity, req.legal_entity_id)
    counterparty = db.scalar(select(Counterparty).where(Counterparty.organization_id == req.organization_id,
                                                        Counterparty.inn == payer.inn).limit(1))
    ours = db.get(Counterparty, req.counterparty_id)
    bank = db.scalar(select(BankAccount).where(BankAccount.legal_entity_id == target.id,
                                               BankAccount.number == ours.account_number)) \
        if ours.account_number else None
    article = system_article(db, req.organization_id, "default_in")
    fields = dict(legal_entity_id=target.id, pay_date=req.pay_date, amount=req.amount,
                  counterparty_id=counterparty.id if counterparty else None,
                  bank_account_id=bank.id if bank else None,
                  description=f"Внутри группы от {payer.short_name} (заявка № {req.number})"
                              + (f": {req.description}" if req.description else ""))
    if mirror is None:
        mirror = PaymentRequest(organization_id=req.organization_id, number=service.next_number(db, req.organization_id),
                                direction="in", source="request", initiator="finance", route=[], step=0,
                                status="approved", created_by=req.created_by, approved_by=req.approved_by,
                                approved_at=req.approved_at or dt.datetime.now(dt.UTC), linked_id=req.id,
                                article_id=article.id if article else None, **fields)
        db.add(mirror)
        db.flush()
        db.add(PaymentRequestEvent(request_id=mirror.id, user_id=req.created_by, action="created",
                                   comment=f"платёж внутри группы по заявке № {req.number}"))
        return
    for key, value in fields.items():
        if key == "bank_account_id" and mirror.bank_account_id is not None:
            continue  # счёт поступления могли выбрать вручную
        setattr(mirror, key, value)
