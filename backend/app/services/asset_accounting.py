"""Учёт имущества (docs/TZ.md, раздел 28, «Детали 19.4»): постановка на учёт бухгалтером, остаток лизинга, QR-этикетки.

Вид учёта:
- fixed_asset — основное средство: карточка ОС (стоимость = цена, срок, статья амортизации), поступление
  Дт 08 — Кт 60.01 по поставщику (если указан), по желанию — сразу ввод в эксплуатацию (Дт 01 — Кт 08);
- expense — малоценка сразу в расход: Дт 26 / 44 (ЦФО единицы, статья ОПиУ) — Кт 60.01 по поставщику;
- off_balance — за балансом (аренда, лизинг на балансе лизингодателя): только отметка, проводок нет;
- earlier — учтено ранее (имущество, уже числящееся в учёте): только отметка.
"""

import datetime as dt
from decimal import Decimal

import segno
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import (
    Account,
    Article,
    AssetItem,
    Contract,
    Counterparty,
    FixedAsset,
    Operation,
    PaymentRequest,
    User,
)
from app.models.asset import ACCOUNTING, AssetReceipt, AssetRequest
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import assets as asset_service
from app.services import fixed_assets, journal
from app.services.articles import is_group, system_article
from app.services.journal import JournalError

ZERO = Decimal("0.00")
EXPENSE_ACCOUNTS = ("26", "44")


class AccountingError(ValueError):
    pass


def _accounts(db: Session, *codes: str) -> dict[str, int]:
    return dict(db.execute(select(Account.code, Account.id).where(Account.code.in_(codes))).all())


def source_request(db: Session, item: AssetItem) -> AssetRequest | None:
    """Заявка на имущество, по которой единица принята (поставщик, договор, лизинг)."""
    if item.receipt_id is None:
        return None
    receipt = db.get(AssetReceipt, item.receipt_id)
    return db.get(AssetRequest, receipt.request_id) if receipt else None


def _posted(db: Session, organization_id: int, user: User, body: OperationIn) -> Operation:
    try:
        return journal.create_posted(db, organization_id, body, user)
    except JournalError as exc:
        raise AccountingError(f"Проводки не прошли проверку журнала: {exc}") from None


def account(db: Session, items: list[AssetItem], user: User, *, kind: str, on_date: dt.date,
            counterparty_id: int | None = None, contract_id: int | None = None, useful_life_months: int | None = None,
            depreciation_item_id: int | None = None, commission_on: dt.date | None = None,
            expense_account: str = "26", expense_item_id: int | None = None) -> None:
    if kind not in ACCOUNTING:
        raise AccountingError("Вид учёта: основное средство, малоценка в расход, за балансом или учтено ранее")
    if not items:
        raise AccountingError("Не выбраны единицы")
    org = items[0].organization_id
    for item in items:
        if item.accounting is not None:
            raise AccountingError(f"{item.number} уже учтена: {ACCOUNTING[item.accounting].lower()}")
        if item.status == "disposed":
            raise AccountingError(f"{item.number} выбыла")
        if kind in ("fixed_asset", "expense"):
            if item.legal_entity_id is None:
                raise AccountingError(f"{item.number}: не указано юрлицо")
            if not item.purchase_price:
                raise AccountingError(f"{item.number}: не указана цена приобретения")
    if counterparty_id is not None:
        cp = db.get(Counterparty, counterparty_id)
        if cp is None or cp.organization_id != org:
            raise AccountingError("Поставщик не найден в этой организации")
    if contract_id is not None:
        contract = db.get(Contract, contract_id)
        if contract is None or contract.organization_id != org or contract.counterparty_id != counterparty_id:
            raise AccountingError("Договор — с этим поставщиком")
    supplier = AnalyticsIn(counterparty=counterparty_id, contract=contract_id)

    if kind == "fixed_asset":
        if not useful_life_months or not 1 <= useful_life_months <= 1200:
            raise AccountingError("Укажите срок полезного использования, месяцев")
        depreciation = (db.get(Article, depreciation_item_id) if depreciation_item_id
                        else system_article(db, org, "depreciation"))
        if depreciation is None or depreciation.organization_id != org or not depreciation.is_pl \
                or depreciation.debit_account_id is None or is_group(db, depreciation.id):
            raise AccountingError("Статья амортизации — статья ОПиУ с шаблоном проводки, без подстатей")
        ids = _accounts(db, "08", "60.01")
        for item in items:
            src = source_request(db, item)  # инвестиционная заявка — карточка ОС в её инвестпроекте (21.3)
            fa = FixedAsset(organization_id=org, name=item.name, inventory_number=item.number,
                            legal_entity_id=item.legal_entity_id, cost=item.purchase_price,
                            useful_life_months=useful_life_months, cfo_id=item.cfo_id, pl_item_id=depreciation.id,
                            project_id=src.project_id if src is not None and src.cost_kind == "invest" else None)
            db.add(fa)
            db.flush()
            item.fixed_asset_id = fa.id
            if counterparty_id is not None:
                op = _posted(db, org, user, OperationIn(
                    legal_entity_id=item.legal_entity_id, op_date=on_date,
                    description=f"Поступление основного средства: {item.name} ({item.number})",
                    postings=[PostingIn(debit_account_id=ids["08"], credit_account_id=ids["60.01"],
                                        amount=item.purchase_price, debit=AnalyticsIn(fixed_asset=fa.id),
                                        credit=supplier)]))
                item.accounting_operation_id = op.id
            if commission_on is not None:
                try:
                    fixed_assets.commission(db, fa, commission_on, user)
                except fixed_assets.AssetError as exc:
                    raise AccountingError(f"{item.number}: {exc}") from None
                item.status = "in_use"
    elif kind == "expense":
        if counterparty_id is None:
            raise AccountingError("Укажите поставщика (Кт 60.01) или выберите «учтено ранее»")
        if expense_account not in EXPENSE_ACCOUNTS:
            raise AccountingError("Счёт затрат — 26 или 44")
        article = db.get(Article, expense_item_id) if expense_item_id else None
        if article is None or article.organization_id != org or not article.is_pl or is_group(db, article.id):
            raise AccountingError("Укажите статью ОПиУ расхода (без подстатей)")
        if any(i.cfo_id is None for i in items):
            raise AccountingError("У единиц должно быть место (ЦФО) — на него относится расход")
        ids = _accounts(db, expense_account, "60.01")
        by_entity: dict[int, list[AssetItem]] = {}
        for item in items:
            by_entity.setdefault(item.legal_entity_id, []).append(item)
        for entity_id, group in by_entity.items():
            op = _posted(db, org, user, OperationIn(
                legal_entity_id=entity_id, op_date=on_date, article_id=article.id,
                description="Малоценка в расход: " + ", ".join(f"{i.name} ({i.number})" for i in group)[:1900],
                postings=[PostingIn(debit_account_id=ids[expense_account], credit_account_id=ids["60.01"],
                                    amount=i.purchase_price, debit=AnalyticsIn(cfo=i.cfo_id, pl_item=article.id),
                                    credit=supplier) for i in group]))
            for item in group:
                item.accounting_operation_id = op.id
    for item in items:
        item.accounting, item.accounted_on = kind, on_date
        details = ACCOUNTING[kind] + f" с {on_date:%d.%m.%Y}"
        if item.accounting_operation_id:
            number = db.get(Operation, item.accounting_operation_id).number
            details += f", операция № {number}"
        asset_service.event(db, item, user, "accounted", details)
    db.flush()


def unaccount(db: Session, item: AssetItem, user: User) -> list[AssetItem]:
    """Снять с учёта (ошибочная постановка): удаляется операция поступления (у малоценки — вместе с другими
    единицами этой операции) и карточка ОС, если она не введена в эксплуатацию. Возвращает снятые единицы."""
    if item.accounting is None:
        raise AccountingError(f"{item.number} не учтена")
    if item.accounted_on is None:
        raise AccountingError(f"{item.number} учтена до модуля имущества — снимите в «Основных средствах»")
    if item.status == "disposed":
        raise AccountingError(f"{item.number} выбыла")
    group = [item]
    if item.accounting_operation_id is not None:
        group = list(db.scalars(select(AssetItem).where(
            AssetItem.accounting_operation_id == item.accounting_operation_id)))
    fas = []
    for i in group:
        if i.fixed_asset_id is not None:
            fa = db.get(FixedAsset, i.fixed_asset_id)
            if fa is not None and fa.commissioned_on is not None:
                raise AccountingError(f"{i.number}: основное средство введено в эксплуатацию — сначала отмените ввод")
            fas.append(fa)
    if item.accounting_operation_id is not None:
        op = db.get(Operation, item.accounting_operation_id)
        if op is not None:
            db.delete(op)
            db.flush()
    for i in group:
        i.accounting, i.accounted_on, i.accounting_operation_id, i.fixed_asset_id = None, None, None, None
        asset_service.event(db, i, user, "unaccounted")
    db.flush()
    for fa in fas:
        if fa is not None:
            db.delete(fa)
    db.flush()
    return group


def lease_remaining(db: Session, items: list[AssetItem]) -> dict[int, Decimal]:
    """Остаток лизинга на единицу. С договором лизинга (21.2) — цена с удорожанием × неоплаченная доля договора;
    по прежним заявкам — (платёж × срок − оплачено по платежам лизинга) / количество в заявке."""
    from app.services import leasing

    result: dict[int, Decimal] = leasing.remaining_by_item(db, items)
    requests: dict[int, AssetRequest | None] = {}
    for item in items:
        if item.ownership != "leasing" or item.receipt_id is None or item.id in result \
                or item.lease_contract_id is not None:
            continue
        if item.receipt_id not in requests:
            requests[item.receipt_id] = source_request(db, item)
        req = requests[item.receipt_id]
        if req is None or req.purchase_kind != "leasing" or not req.lease_payment or not req.lease_months:
            continue
        paid = ZERO
        if req.periodic_id is not None:
            paid = db.scalar(select(func.coalesce(func.sum(PaymentRequest.amount), 0)).where(
                PaymentRequest.periodic_id == req.periodic_id, PaymentRequest.status == "paid")) or ZERO
        total = req.lease_payment * req.lease_months
        result[item.id] = max(ZERO, round_money((total - Decimal(paid)) / req.quantity))
    return result


def qr_svg(text: str) -> str:
    """QR-код (уровень коррекции M) в виде SVG для этикетки."""
    return segno.make(text, error="m").svg_inline(scale=3, border=2)
