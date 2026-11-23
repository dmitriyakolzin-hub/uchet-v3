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
    Counterparty,
    Employee,
    Executor,
    LegalEntity,
    PaymentRequest,
    User,
)
from app.models.asset import (
    ASSET_REQUEST_STATUSES,
    PURCHASE_KINDS,
    AssetItem,
    AssetReceipt,
    AssetRequest,
    AssetRequestEvent,
)
from app.services import approval, asset_route, leasing
from app.services import assets as asset_service
from app.services import payment_requests as payments
from app.services.articles import is_group, system_article

EDITABLE = ("draft", "revision", "rejected")  # правит автор (или ФД); после правки — снова черновик
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
    _own(db, req, Executor, req.executor_id, "ЦФУ (кто закупает)")


def total(req: AssetRequest) -> Decimal | None:
    """Стоимость закупки: цена × количество; лизинг — платёж × месяцы (цена с удорожанием)."""
    if req.purchase_kind == "leasing" and req.lease_payment is not None and req.lease_months:
        return round_money(req.lease_payment * req.lease_months)
    if req.price is not None:
        return round_money(req.price * req.quantity)
    return None


def validate_decision(db: Session, req: AssetRequest) -> None:
    """Решение ФД: юрлицо, поставщик, вариант закупки, цена и дата оплаты. Лизинг — только отметка: лизингодатель,
    договор, платёж и срок заполняются потом, когда появится договор (docs/TZ.md, «Детали 21.2»)."""
    entity = _own(db, req, LegalEntity, req.legal_entity_id, "Юрлицо")
    if entity is None:
        raise AssetRequestError("Укажите юрлицо, которое покупает")
    lease = req.purchase_kind == "leasing"
    if _own(db, req, Counterparty, req.counterparty_id, "Поставщик") is None and not lease:
        raise AssetRequestError("Укажите поставщика")
    contract = _own(db, req, Contract, req.contract_id, "Договор")
    if contract is not None and (contract.counterparty_id != req.counterparty_id
                                 or contract.legal_entity_id != entity.id):
        raise AssetRequestError("Договор — с этим поставщиком и этим юрлицом")
    if contract is not None and (contract.kind == "leasing") != (req.purchase_kind == "leasing"):
        raise AssetRequestError("У лизинга — договор вида «Лизинг», у покупки — другой договор")
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
    if req.pay_date is None and not lease:
        raise AssetRequestError("Укажите дату оплаты")
    if lease:
        if req.lease_months and not 1 <= req.lease_months <= MAX_LEASE_MONTHS:
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
    принято). Лизинг — периодический платёж договора лизинга (app/services/leasing.py: set_lease)."""
    article_id = req.article_id
    if article_id is None:
        article = system_article(db, req.organization_id, "asset_purchase")
        article_id = article.id if article else None
    title = req.model_name or req.name
    if req.purchase_kind == "leasing":
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
    """Убрать оплаты решения (при отмене): нельзя, если что-то уже оплачено. Лизинг — доля заявки уходит из платежа
    договора (последняя заявка договора — условия удаляются)."""
    try:
        leasing.unset_lease(db, req)
    except leasing.LeasingError as exc:
        raise AssetRequestError(str(exc)) from None
    linked = list(db.scalars(select(PaymentRequest).where(PaymentRequest.asset_request_id == req.id)))
    if any(p.status == "paid" for p in linked):
        raise AssetRequestError("По заявке уже есть оплата — сначала снимите отметку об оплате в календаре")
    for p in linked:
        db.delete(p)
    db.flush()


DECISION_FIELDS = ("model_name", "counterparty_id", "contract_id", "employee_id", "purchase_kind", "price", "pay_date",
                   "bank_account_id", "article_id", "lease_payment", "lease_months", "legal_entity_id", "expected_on")


def decide(db: Session, req: AssetRequest, user: User, decision: dict) -> None:
    if asset_route.current_step(req) != "fd":
        raise AssetRequestError("Решение принимается, когда заявка дошла до финансового директора")
    for key, value in decision.items():
        setattr(req, key, value)
    if req.purchase_kind != "leasing":  # у лизинга цена покупки необязательна, платёж и срок — обязательны
        req.lease_payment, req.lease_months = None, None
    lease_now = req.purchase_kind == "leasing" and req.contract_id is not None and req.lease_payment is not None
    if req.purchase_kind == "leasing" and not lease_now:
        req.contract_id = None  # договор лизинга — потом, «Заполнить лизинг…»
    validate_decision(db, req)
    req.status, req.decided_by, req.decided_at, req.reject_reason = "approved", user.id, dt.datetime.now(dt.UTC), ""
    db.flush()
    create_payments(db, req, user)
    amount = total(req)
    event(db, req, user, "approved", f"{PURCHASE_KINDS[req.purchase_kind]}"
          + (" — ждём договор лизинга" if req.purchase_kind == "leasing" and not lease_now else "")
          + (f", {amount:,.2f} ₽".replace(",", " ").replace(".", ",") if amount is not None else ""))
    if lease_now:
        contract_id, req.contract_id = req.contract_id, None
        try:
            details = leasing.set_lease(db, req, user, contract_id=contract_id, payment=req.lease_payment,
                                        months=req.lease_months, first_payment=req.pay_date,
                                        bank_account_id=req.bank_account_id, article_id=req.article_id)
        except leasing.LeasingError as exc:
            raise AssetRequestError(str(exc)) from None
        event(db, req, user, "lease", details)


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


# --- приёмка (шаг 19.3) ---

RECEIVABLE = ("approved", "ordered", "shipped", "delivered")


def received_quantity(db: Session, req: AssetRequest) -> int:
    return db.scalar(select(func.coalesce(func.sum(AssetReceipt.quantity), 0))
                     .where(AssetReceipt.request_id == req.id)) or 0


def receive(db: Session, req: AssetRequest, user: User, *, received_on: dt.date, quantity: int,
            employee_id: int | None, cfo_id: int | None, warehouse_id: int | None, mol_id: int | None,
            status: str, discrepancy: str) -> tuple[AssetReceipt, list[AssetItem]]:
    """Приёмка: сколько пришло (не больше, чем осталось принять); каждая штука — единица имущества с местом, МОЛ
    и ценой из заявки. Принято всё — заявка «принята», иначе остаётся для следующей приёмки."""
    if req.status not in RECEIVABLE:
        raise AssetRequestError(f"Заявка: {ASSET_REQUEST_STATUSES[req.status].lower()} — принимать можно после "
                                "решения ФД")
    left = req.quantity - received_quantity(db, req)
    if quantity < 1 or quantity > left:
        raise AssetRequestError(f"Принять можно от 1 до {left} шт.")
    if status not in ("stock", "in_use"):
        raise AssetRequestError("После приёмки — «на складе» или «в эксплуатации»")
    _own(db, req, Employee, employee_id, "Кто принял")
    receipt = AssetReceipt(request_id=req.id, received_on=received_on, employee_id=employee_id, quantity=quantity,
                           discrepancy=discrepancy, created_by=user.id)
    db.add(receipt)
    db.flush()
    upgraded = None
    if req.purchase_kind == "leasing" and req.lease_payment is not None and req.lease_months:
        upgraded = round_money(req.lease_payment * req.lease_months / req.quantity)  # цена с удорожанием за штуку
    items = []
    for number in asset_service.next_numbers(db, req.organization_id, quantity):
        item = AssetItem(
            organization_id=req.organization_id, number=number, name=req.model_name or req.name,
            category_id=req.category_id, ownership="leasing" if req.purchase_kind == "leasing" else "own",
            legal_entity_id=req.legal_entity_id, cfo_id=cfo_id, warehouse_id=warehouse_id, employee_id=mol_id,
            status=status, purchase_price=req.price, upgraded_price=upgraded, acquired_on=received_on,
            note=f"принято по заявке на имущество № {req.number}", receipt_id=receipt.id, created_by=user.id,
            lease_contract_id=req.contract_id if req.purchase_kind == "leasing" and req.periodic_id else None)
        try:
            asset_service.validate(db, item)
        except asset_service.AssetError as exc:
            raise AssetRequestError(str(exc)) from None
        db.add(item)
        db.flush()
        asset_service.event(db, item, user, "received", f"заявка на имущество № {req.number}")
        items.append(item)
    total_received = received_quantity(db, req)
    if req.delivered_on is None:
        req.delivered_on = received_on
    req.status = "received" if total_received >= req.quantity else "delivered"
    details = f"{quantity} шт. ({items[0].number}" + (f" — {items[-1].number})" if len(items) > 1 else ")")
    if discrepancy:
        details += f"; расхождения: {discrepancy}"
    event(db, req, user, "received", details)
    return receipt, items


def undo_receipt(db: Session, receipt: AssetReceipt, user: User) -> None:
    """Отменить приёмку: её единицы удаляются, если с ними ещё ничего не делали (не выбыли, нет карточки ОС)."""
    req = db.get(AssetRequest, receipt.request_id)
    items = list(db.scalars(select(AssetItem).where(AssetItem.receipt_id == receipt.id)))
    if any(i.status == "disposed" or i.fixed_asset_id is not None for i in items):
        raise AssetRequestError("Часть единиц уже выбыла или стала основным средством — приёмку не отменить")
    for item in items:
        db.delete(item)
    db.delete(receipt)
    db.flush()
    if req.status == "received":  # принято не всё — снова можно принимать
        req.status = "delivered"
    event(db, req, user, "receipt_undone", f"{receipt.quantity} шт. от {receipt.received_on:%d.%m.%Y}")
