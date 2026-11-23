"""Договоры лизинга (docs/TZ.md, раздел 28, «Детали 21.2»).

Решение финансового директора «лизинг» — без поставщика и платежей: заявка ждёт договор. Когда договор подписан,
финансовый директор заполняет лизинг: договор (вид «лизинг») с лизингодателем, платёж по заявке в месяц, срок, первый
платёж. Условия платежа — одни на договор (периодический платёж «Лизинг: договор № …»); если договор уже есть
у другой заявки — срок и даты берутся из него, платёж договора = сумма платежей его заявок. Единицы имущества заявки
получают договор лизинга и цену с удорожанием (платёж заявки × срок / количество).
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import BankAccount, Contract, ContractPayment, PaymentRequest, User
from app.models.asset import AssetItem, AssetReceipt, AssetRequest
from app.services import periodic
from app.services.articles import is_group, system_article

ZERO = Decimal("0.00")
MAX_MONTHS = 120


class LeasingError(ValueError):
    pass


def months_of(terms: ContractPayment) -> int:
    end = terms.valid_to or terms.valid_from
    return (end.year * 12 + end.month) - (terms.valid_from.year * 12 + terms.valid_from.month) + 1


def lease_terms(db: Session, contract_id: int) -> ContractPayment | None:
    """Условия платежа договора лизинга (последние)."""
    return db.scalar(select(ContractPayment).where(ContractPayment.contract_id == contract_id)
                     .order_by(ContractPayment.valid_from.desc(), ContractPayment.id.desc()).limit(1))


def pending(req: AssetRequest) -> bool:
    """Решение «лизинг» принято, договор ещё не заполнен."""
    return req.purchase_kind == "leasing" and req.periodic_id is None and req.status not in (
        "draft", "pending", "revision", "rejected", "cancelled")


def request_items(db: Session, req: AssetRequest) -> list[AssetItem]:
    return list(db.scalars(select(AssetItem).join(AssetReceipt, AssetReceipt.id == AssetItem.receipt_id)
                           .where(AssetReceipt.request_id == req.id)))


def upgraded_price(req: AssetRequest) -> Decimal | None:
    if req.lease_payment is None or not req.lease_months:
        return None
    return round_money(req.lease_payment * req.lease_months / req.quantity)


def set_lease(db: Session, req: AssetRequest, user: User, *, contract_id: int, payment: Decimal,
              months: int | None, first_payment: dt.date | None, bank_account_id: int | None,
              article_id: int | None) -> str:
    """Заполнить лизинг по заявке; возвращает текст для истории."""
    if not pending(req):
        raise LeasingError("Лизинг заполняется после решения финансового директора «лизинг», один раз")
    contract = db.get(Contract, contract_id)
    if contract is None or contract.organization_id != req.organization_id:
        raise LeasingError("Договор не найден в этой организации")
    if contract.kind != "leasing":
        raise LeasingError("Выберите договор вида «Лизинг»")
    if req.legal_entity_id is not None and contract.legal_entity_id != req.legal_entity_id:
        raise LeasingError("Договор лизинга — того юрлица, которое покупает по заявке")
    if payment is None or payment <= 0:
        raise LeasingError("Укажите ежемесячный платёж по этой заявке")
    terms = lease_terms(db, contract.id)
    if terms is None:
        if not months or not 1 <= months <= MAX_MONTHS:
            raise LeasingError(f"Срок лизинга — от 1 до {MAX_MONTHS} месяцев")
        if first_payment is None:
            raise LeasingError("Укажите дату первого платежа")
        bank = db.get(BankAccount, bank_account_id) if bank_account_id else None
        if bank_account_id and (bank is None or bank.legal_entity_id != contract.legal_entity_id):
            raise LeasingError("Расчётный счёт — юрлица договора")
        if article_id is not None:
            from app.models import Article

            article = db.get(Article, article_id)
            if article is None or article.organization_id != req.organization_id or not article.is_cash_flow \
                    or article.cash_flow_direction != "outflow" or is_group(db, article.id):
                raise LeasingError("Статья — статья ДДС выплат без подстатей")
        else:
            default = system_article(db, req.organization_id, "asset_purchase")
            article_id = default.id if default else None
        last = periodic._shift(first_payment, months - 1)
        terms = ContractPayment(
            organization_id=req.organization_id, legal_entity_id=contract.legal_entity_id,
            counterparty_id=contract.counterparty_id, contract_id=contract.id,
            name=f"Лизинг: договор № {contract.number}"[:255], frequency="month", valid_from=first_payment,
            valid_to=periodic._month_end(last), day=first_payment.day, amount=payment, bank_account_id=bank_account_id,
            article_id=article_id, cfo_id=req.cfo_id, description=f"заявка на имущество № {req.number}",
            created_by=user.id)
        db.add(terms)
        db.flush()
        text = f"новые условия: {_money(payment)} в месяц × {months} мес. с {first_payment:%d.%m.%Y}"
    else:
        terms.amount = terms.amount + payment
        terms.description = (f"{terms.description}, № {req.number}" if terms.description else
                             f"заявка на имущество № {req.number}")[:500]
        text = f"платёж договора теперь {_money(terms.amount)} в месяц (+{_money(payment)})"
    req.contract_id, req.counterparty_id, req.legal_entity_id = contract.id, contract.counterparty_id, \
        contract.legal_entity_id
    req.lease_payment, req.lease_months, req.pay_date, req.periodic_id = payment, months_of(terms), \
        terms.valid_from, terms.id
    if bank_account_id and req.bank_account_id is None:
        req.bank_account_id = bank_account_id
    price = upgraded_price(req)
    for item in request_items(db, req):
        item.lease_contract_id, item.upgraded_price = contract.id, price
    db.flush()
    periodic.ensure(db, req.organization_id)
    return f"договор лизинга № {contract.number}: {text}"


def unset_lease(db: Session, req: AssetRequest) -> None:
    """Снять сведения о лизинге: доля заявки уходит из платежа договора; последняя заявка договора — условия
    и неоплаченные заявки на оплату удаляются (если по ним уже платили — нельзя)."""
    if req.periodic_id is None:
        return
    terms = db.get(ContractPayment, req.periodic_id)
    others = db.scalar(select(func.count(AssetRequest.id)).where(AssetRequest.periodic_id == req.periodic_id,
                                                                 AssetRequest.id != req.id)) or 0
    if terms is not None:
        if others and terms.contract_id is not None:
            terms.amount = terms.amount - (req.lease_payment or ZERO)
            if terms.amount <= 0:
                raise LeasingError("Платёж договора после снятия заявки — не больше нуля; проверьте платежи заявок")
        else:
            payments = list(db.scalars(select(PaymentRequest).where(PaymentRequest.periodic_id == terms.id)))
            if any(p.status == "paid" for p in payments):
                raise LeasingError("По лизингу уже есть оплата — сначала снимите отметку об оплате в календаре")
            for p in payments:
                db.delete(p)
            req.periodic_id = None
            db.flush()
            db.delete(terms)
    req.periodic_id = None
    req.lease_payment, req.lease_months, req.contract_id = None, None, None
    for item in request_items(db, req):
        item.lease_contract_id, item.upgraded_price = None, None
    db.flush()
    periodic.ensure(db, req.organization_id)


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


# --- обзор договоров лизинга ---


@dataclass
class ContractView:
    contract: Contract
    terms: ContractPayment | None
    months: int | None
    total: Decimal | None  # платёж × срок
    paid: Decimal
    requests: list[AssetRequest] = field(default_factory=list)
    items: list[AssetItem] = field(default_factory=list)
    payments: list[PaymentRequest] = field(default_factory=list)


def overview(db: Session, organization_id: int) -> list[ContractView]:
    contracts = list(db.scalars(select(Contract).where(Contract.organization_id == organization_id,
                                                       Contract.kind == "leasing")
                                .order_by(Contract.contract_date.desc().nulls_last(), Contract.number)))
    result = []
    for c in contracts:
        terms = lease_terms(db, c.id)
        payments = list(db.scalars(select(PaymentRequest).where(PaymentRequest.periodic_id == terms.id)
                                   .order_by(PaymentRequest.pay_date))) if terms else []
        months = months_of(terms) if terms else None
        result.append(ContractView(
            contract=c, terms=terms, months=months, total=round_money(terms.amount * months) if terms else None,
            paid=sum((p.amount for p in payments if p.status == "paid"), ZERO),
            requests=list(db.scalars(select(AssetRequest).where(AssetRequest.contract_id == c.id,
                                                                AssetRequest.purchase_kind == "leasing")
                                     .order_by(AssetRequest.number))),
            items=list(db.scalars(select(AssetItem).where(AssetItem.lease_contract_id == c.id)
                                  .order_by(AssetItem.number))),
            payments=payments))
    return result


def remaining_by_item(db: Session, items: list[AssetItem]) -> dict[int, Decimal]:
    """Остаток лизинга единицы с договором: цена с удорожанием × неоплаченная доля договора."""
    result: dict[int, Decimal] = {}
    shares: dict[int, Decimal | None] = {}
    for item in items:
        if item.lease_contract_id is None or item.upgraded_price is None:
            continue
        if item.lease_contract_id not in shares:
            terms = lease_terms(db, item.lease_contract_id)
            share = None
            if terms is not None:
                total = terms.amount * months_of(terms)
                paid = db.scalar(select(func.coalesce(func.sum(PaymentRequest.amount), 0)).where(
                    PaymentRequest.periodic_id == terms.id, PaymentRequest.status == "paid")) or 0
                share = max(ZERO, (total - Decimal(paid)) / total) if total else None
            shares[item.lease_contract_id] = share
        share = shares[item.lease_contract_id]
        if share is not None:
            result[item.id] = round_money(item.upgraded_price * share)
    return result
