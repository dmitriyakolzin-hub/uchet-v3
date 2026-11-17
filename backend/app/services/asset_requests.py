"""Заявки на имущество (docs/TZ.md, раздел 28, «Детали 19.2»): проверка, решение финансового директора с оплатами
в платёжном календаре, отмена решения, статус поставки."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import (
    Article,
    AssetCategory,
    BankAccount,
    Cfo,
    Contract,
    ContractPayment,
    Counterparty,
    Employee,
    LegalEntity,
    PaymentRequest,
    User,
)
from app.models.asset import ASSET_REQUEST_STATUSES, PURCHASE_KINDS, AssetRequest, AssetRequestEvent
from app.services import approval
from app.services import payment_requests as payments
from app.services import periodic
from app.services.articles import is_group, system_article

EDITABLE = ("draft", "rejected")  # правит автор (или ФД); «отклонена» после правки снова черновик
DELIVERY = ("approved", "ordered", "shipped", "delivered")  # статусы поставки по порядку
MAX_LEASE_MONTHS = 120


class AssetRequestError(ValueError):
    pass


def next_number(db: Session, organization_id: int) -> int:
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    current = db.scalar(select(func.max(AssetRequest.number)).where(AssetRequest.organization_id == organization_id))
    return (current or 0) + 1


def event(db: Session, req: AssetRequest, user: User | None, action: str, details: str = "") -> None:
    db.add(AssetRequestEvent(request_id=req.id, user_id=user.id if user else None, action=action, details=details))


def _own(db: Session, req: AssetRequest, model, value, title: str):
    if value is None:
        return None
    record = db.get(model, value)
    if record is None or record.organization_id != req.organization_id:
        raise AssetRequestError(f"{title}: не найдено в этой организации")
    return record


def validate(db: Session, req: AssetRequest) -> None:
    """Что нужно: наименование, количество, ссылки — на записи своей организации."""
    if not req.name.strip():
        raise AssetRequestError("Укажите, что нужно")
    if req.quantity < 1:
        raise AssetRequestError("Количество — не меньше 1")
    _own(db, req, AssetCategory, req.category_id, "Категория")
    _own(db, req, Cfo, req.cfo_id, "ЦФО")
    _own(db, req, LegalEntity, req.legal_entity_id, "Юрлицо")


def total(req: AssetRequest) -> Decimal | None:
    """Стоимость закупки: цена × количество; лизинг — платёж × месяцы (цена с удорожанием)."""
    if req.purchase_kind == "leasing" and req.lease_payment is not None and req.lease_months:
        return round_money(req.lease_payment * req.lease_months)
    if req.price is not None:
        return round_money(req.price * req.quantity)
    return None


def validate_decision(db: Session, req: AssetRequest) -> None:
    """Решение ФД: юрлицо, поставщик, вариант закупки, цена и дата оплаты (лизинг — платёж, срок, первый платёж)."""
    entity = _own(db, req, LegalEntity, req.legal_entity_id, "Юрлицо")
    if entity is None:
        raise AssetRequestError("Укажите юрлицо, которое покупает")
    if _own(db, req, Counterparty, req.counterparty_id, "Поставщик") is None:
        raise AssetRequestError("Укажите поставщика")
    contract = _own(db, req, Contract, req.contract_id, "Договор")
    if contract is not None and (contract.counterparty_id != req.counterparty_id
                                 or contract.legal_entity_id != entity.id):
        raise AssetRequestError("Договор — с этим поставщиком и этим юрлицом")
    _own(db, req, Employee, req.employee_id, "Ответственный")
    bank = _own(db, req, BankAccount, req.bank_account_id, "Расчётный счёт")
    if bank is not None and bank.legal_entity_id != entity.id:
        raise AssetRequestError("Расчётный счёт — юрлица, которое покупает")
    article = _own(db, req, Article, req.article_id, "Статья ДДС")
    if article is not None and (not article.is_cash_flow or article.cash_flow_direction != "outflow"
                                or is_group(db, article.id)):
        raise AssetRequestError("Статья — статья ДДС выплат без подстатей")
    if req.purchase_kind not in PURCHASE_KINDS:
        raise AssetRequestError("Выберите вариант закупки: предоплата, отсрочка или лизинг")
    if req.pay_date is None:
        raise AssetRequestError("Укажите дату оплаты" + (" (первого платежа лизинга)" if req.purchase_kind == "leasing" else ""))
    if req.purchase_kind == "leasing":
        if req.lease_payment is None or req.lease_payment <= 0:
            raise AssetRequestError("Укажите ежемесячный платёж лизинга")
        if not req.lease_months or not 1 <= req.lease_months <= MAX_LEASE_MONTHS:
            raise AssetRequestError(f"Срок лизинга — от 1 до {MAX_LEASE_MONTHS} месяцев")
    elif req.price is None or req.price <= 0:
        raise AssetRequestError("Укажите цену за единицу")


def linked_payments(db: Session, req: AssetRequest) -> list[PaymentRequest]:
    """Заявки на оплату по этой заявке: разовая оплата и платежи лизинга (периодические)."""
    conditions = [PaymentRequest.asset_request_id == req.id]
    if req.periodic_id is not None:
        conditions.append(PaymentRequest.periodic_id == req.periodic_id)
    return list(db.scalars(select(PaymentRequest).where(or_(*conditions))
                           .order_by(PaymentRequest.pay_date, PaymentRequest.number)))


def create_payments(db: Session, req: AssetRequest, user: User) -> None:
    """Оплата в платёжный календарь: предоплата и отсрочка — заявка на оплату, сразу согласованная ФД (решение уже
    принято); лизинг — периодический платёж (заявки на 3 месяца вперёд создаются сами)."""
    article_id = req.article_id
    if article_id is None:
        article = system_article(db, req.organization_id, "asset_purchase")
        article_id = article.id if article else None
    title = req.model_name or req.name
    if req.purchase_kind == "leasing":
        start = req.pay_date
        last = periodic._shift(start, req.lease_months - 1)
        terms = ContractPayment(
            organization_id=req.organization_id, legal_entity_id=req.legal_entity_id,
            counterparty_id=req.counterparty_id, name=f"Лизинг: {title}"[:255], frequency="month", valid_from=start,
            valid_to=periodic._month_end(last), day=start.day, amount=req.lease_payment,
            bank_account_id=req.bank_account_id, article_id=article_id, cfo_id=req.cfo_id,
            description=f"заявка на имущество № {req.number}", created_by=user.id)
        db.add(terms)
        db.flush()
        req.periodic_id = terms.id
        periodic.ensure(db, req.organization_id)
        return
    pay = PaymentRequest(
        organization_id=req.organization_id, number=payments.next_number(db, req.organization_id), direction="out",
        pay_date=req.pay_date, legal_entity_id=req.legal_entity_id, bank_account_id=req.bank_account_id,
        counterparty_id=req.counterparty_id, contract_id=req.contract_id, article_id=article_id, cfo_id=req.cfo_id,
        amount=total(req), status="approved", source="asset", initiator="finance", route=["fd"], step=1,
        approved_by=user.id, approved_at=dt.datetime.now(dt.UTC), created_by=user.id, asset_request_id=req.id,
        description=(f"{PURCHASE_KINDS[req.purchase_kind]}: {title} × {req.quantity} "
                     f"(заявка на имущество № {req.number})")[:2000])
    db.add(pay)
    db.flush()
    approval.event(db, pay, user, "created", f"решение по заявке на имущество № {req.number}")
    approval.event(db, pay, user, "approved", "решение финансового директора", "fd")


def remove_payments(db: Session, req: AssetRequest) -> None:
    """Убрать оплаты решения (при отмене): нельзя, если что-то уже оплачено."""
    linked = linked_payments(db, req)
    if any(p.status == "paid" for p in linked):
        raise AssetRequestError("По заявке уже есть оплата — сначала снимите отметку об оплате в календаре")
    for p in linked:
        db.delete(p)
    if req.periodic_id is not None:
        terms = db.get(ContractPayment, req.periodic_id)
        req.periodic_id = None
        if terms is not None:
            db.delete(terms)
    db.flush()


DECISION_FIELDS = ("model_name", "counterparty_id", "contract_id", "employee_id", "purchase_kind", "price", "pay_date",
                   "bank_account_id", "article_id", "lease_payment", "lease_months", "legal_entity_id", "expected_on")


def decide(db: Session, req: AssetRequest, user: User, decision: dict) -> None:
    if req.status != "pending":
        raise AssetRequestError(f"Заявка: {ASSET_REQUEST_STATUSES[req.status].lower()} — решение принимается на заявке "
                                "«На решении ФД»")
    for key, value in decision.items():
        setattr(req, key, value)
    if req.purchase_kind != "leasing":  # у лизинга цена покупки необязательна, платёж и срок — обязательны
        req.lease_payment, req.lease_months = None, None
    validate_decision(db, req)
    req.status, req.decided_by, req.decided_at, req.reject_reason = "approved", user.id, dt.datetime.now(dt.UTC), ""
    db.flush()
    create_payments(db, req, user)
    amount = total(req)
    event(db, req, user, "approved", f"{PURCHASE_KINDS[req.purchase_kind]}"
          + (f", {amount:,.2f} ₽".replace(",", " ").replace(".", ",") if amount is not None else ""))


def set_delivery(db: Session, req: AssetRequest, user: User, status: str, on: dt.date | None,
                 expected_on: dt.date | None) -> None:
    if req.status not in DELIVERY:
        raise AssetRequestError("Статус поставки меняется после решения ФД и до приёмки")
    if status not in DELIVERY[1:]:
        raise AssetRequestError("Статус поставки: заказано, отгружено или доставлено")
    if status == "delivered" and on is None:
        raise AssetRequestError("Укажите дату доставки")
    before = ASSET_REQUEST_STATUSES[req.status]
    req.status = status
    req.delivered_on = on if status == "delivered" else None
    if expected_on is not None:
        req.expected_on = expected_on
    details = f"{before} → {ASSET_REQUEST_STATUSES[status]}"
    if status == "delivered":
        details += f" {on:%d.%m.%Y}"
    elif req.expected_on:
        details += f", ожидается {req.expected_on:%d.%m.%Y}"
    event(db, req, user, "delivery", details)
