"""Выбытие имущества (docs/TZ.md, раздел 28, «Детали 19.6»): продажа, списание, гибель / утрата.

Предложение выбытия согласует финансовый директор; при согласовании единицы выбывают:
- основное средство (карточка ОС) — через `fixed_assets.dispose`: 02 → 01, остаток → 91.02, при продаже — выручка
  Дт 62.01 — Кт 91.01 и НДС; гибель / утрата — как списание;
- остальное (малоценка, за балансом, учтено ранее, не учтено): продажа — выручка Дт 62.01 — Кт 91.01 (+ НДС), списание
  и утрата — без проводок (стоимость уже в расходах или единица не на балансе).
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Account, AssetItem, Contract, Counterparty, Employee, FixedAsset, Operation, User
from app.models.asset import DISPOSAL_KINDS, AssetDisposal, AssetDisposalItem
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import asset_operation, fixed_assets, journal
from app.services import assets as asset_service
from app.services.articles import system_article
from app.services.journal import JournalError

ZERO = Decimal("0.00")
VAT_RATES = (0, 5, 7, 10, 20, 22)


class DisposalError(ValueError):
    pass


def pending_item_ids(db: Session, organization_id: int) -> set[int]:
    return set(db.scalars(select(AssetDisposalItem.item_id).join(
        AssetDisposal, AssetDisposal.id == AssetDisposalItem.disposal_id).where(
        AssetDisposal.organization_id == organization_id, AssetDisposal.status == "pending")))


def create(db: Session, organization_id: int, user: User, items: list[AssetItem], *, kind: str, on_date: dt.date,
           reason: str, counterparty_id: int | None, contract_id: int | None, vat_rate: int, employee_id: int | None,
           prices: dict[int, Decimal]) -> AssetDisposal:
    if kind not in DISPOSAL_KINDS:
        raise DisposalError("Вид выбытия: продажа, списание или гибель / утрата")
    if vat_rate not in VAT_RATES:
        raise DisposalError("Ставка НДС: 0, 5, 7, 10, 20 или 22 %")
    for model, value, title in ((Counterparty, counterparty_id, "Покупатель"), (Contract, contract_id, "Договор"),
                                (Employee, employee_id, "Виновное лицо")):
        if value is not None:
            record = db.get(model, value)
            if record is None or record.organization_id != organization_id:
                raise DisposalError(f"{title}: не найдено в этой организации")
    if contract_id is not None and db.get(Contract, contract_id).counterparty_id != counterparty_id:
        raise DisposalError("Договор — с этим покупателем")
    if kind == "sale":
        if counterparty_id is None:
            raise DisposalError("Для продажи укажите покупателя")
        missing = [i.number for i in items if not prices.get(i.id)]
        if missing:
            raise DisposalError("Для продажи укажите цену каждой единицы: " + ", ".join(missing))
    if not reason.strip() and kind != "sale":
        raise DisposalError("Укажите причину (основание) списания")
    busy = pending_item_ids(db, organization_id)
    moving = asset_operation.pending_item_ids(db, organization_id)
    for item in items:
        if item.status == "disposed":
            raise DisposalError(f"{item.number} уже выбыла")
        if item.id in busy:
            raise DisposalError(f"{item.number} уже в другом выбытии на согласовании")
        if item.id in moving:
            raise DisposalError(f"{item.number} перемещается — дождитесь подтверждения или отмените перемещение")
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    number = (db.scalar(select(func.max(AssetDisposal.number)).where(
        AssetDisposal.organization_id == organization_id)) or 0) + 1
    disposal = AssetDisposal(organization_id=organization_id, number=number, kind=kind, on_date=on_date,
                             reason=reason, counterparty_id=counterparty_id, contract_id=contract_id,
                             vat_rate=vat_rate, employee_id=employee_id, created_by=user.id)
    # согласование АХО (22.3): предложил АХО (или ФД) или в организации нет оператора АХО — уже согласовано
    if asset_service.is_aho(db, user, organization_id) or not asset_service.aho_required(db, organization_id):
        disposal.aho_approved_by, disposal.aho_approved_at = user.id, dt.datetime.now(dt.UTC)
    db.add(disposal)
    db.flush()
    for item in items:
        db.add(AssetDisposalItem(disposal_id=disposal.id, item_id=item.id,
                                 price=prices.get(item.id) if kind == "sale" else None))
        asset_service.event(db, item, user, "disposal_created",
                            f"{DISPOSAL_KINDS[kind]}: акт № {number} на согласовании ФД")
    db.flush()
    return disposal


def lines(db: Session, disposal: AssetDisposal) -> list[tuple[AssetDisposalItem, AssetItem]]:
    return list(db.execute(select(AssetDisposalItem, AssetItem).join(AssetItem, AssetItem.id == AssetDisposalItem.item_id)
                           .where(AssetDisposalItem.disposal_id == disposal.id).order_by(AssetItem.number)).all())


def _sale_operation(db: Session, disposal: AssetDisposal, item: AssetItem, price: Decimal, user: User) -> Operation:
    """Продажа единицы, которой нет в карточках ОС: выручка Дт 62.01 — Кт 91.01, НДС Дт 91.02 — Кт 68.02."""
    if item.legal_entity_id is None:
        raise DisposalError(f"{item.number}: не указано юрлицо")
    if item.cfo_id is None:
        raise DisposalError(f"{item.number}: не указано место (ЦФО) — на него относится доход")
    article = system_article(db, disposal.organization_id, "asset_sale")
    if article is None:
        raise DisposalError("Нет системной статьи «Доход от продажи основных средств»")
    codes = dict(db.execute(select(Account.code, Account.id).where(
        Account.code.in_(("62.01", "68.02", "91.01", "91.02")))).all())
    income = AnalyticsIn(cfo=item.cfo_id, pl_item=article.id)
    postings = [PostingIn(debit_account_id=codes["62.01"], credit_account_id=codes["91.01"], amount=price,
                          debit=AnalyticsIn(counterparty=disposal.counterparty_id, contract=disposal.contract_id),
                          credit=income)]
    vat = fixed_assets.vat_included(price, disposal.vat_rate)
    if vat > 0:
        postings.append(PostingIn(debit_account_id=codes["91.02"], credit_account_id=codes["68.02"], amount=vat,
                                  debit=income))
    try:
        return journal.create_posted(db, disposal.organization_id, OperationIn(
            legal_entity_id=item.legal_entity_id, op_date=disposal.on_date, article_id=article.id,
            description=f"Продажа имущества: {item.name} ({item.number}), акт № {disposal.number}",
            postings=postings), user)
    except JournalError as exc:
        raise DisposalError(f"{item.number}: проводки не прошли проверку журнала: {exc}") from None


def approve_aho(db: Session, disposal: AssetDisposal, user: User) -> None:
    """АХО согласовал выбытие (22.3) — дальше решение финансового директора."""
    if disposal.status != "pending":
        raise DisposalError("Выбытие уже не на согласовании")
    if disposal.aho_approved_at is not None:
        raise DisposalError("Выбытие уже согласовано АХО")
    disposal.aho_approved_by, disposal.aho_approved_at = user.id, dt.datetime.now(dt.UTC)
    for _, item in lines(db, disposal):
        asset_service.event(db, item, user, "disposal_approved", f"выбытие № {disposal.number}: согласовано АХО")
    db.flush()


def approve(db: Session, disposal: AssetDisposal, user: User) -> None:
    """Согласование ФД: единицы выбывают, проводки создаются. Сначала — согласование АХО (22.3)."""
    if disposal.status != "pending":
        raise DisposalError("Выбытие уже не на согласовании")
    if disposal.aho_approved_at is None:
        raise DisposalError("Выбытие ещё не согласовано АХО")
    fa_kind = "sale" if disposal.kind == "sale" else "writeoff"
    for link, item in lines(db, disposal):
        if item.status == "disposed":
            raise DisposalError(f"{item.number} уже выбыла")
        fa = db.get(FixedAsset, item.fixed_asset_id) if item.fixed_asset_id else None
        price = link.price or ZERO
        if fa is not None and fa.disposed_on is None:
            try:
                fixed_assets.dispose(db, fa, disposal.on_date, fa_kind, user, counterparty_id=disposal.counterparty_id,
                                     contract_id=disposal.contract_id, price=price, vat_rate=disposal.vat_rate)
            except fixed_assets.AssetError as exc:
                raise DisposalError(f"{item.number}: {exc}") from None
        elif disposal.kind == "sale" and price > 0:
            link.operation_id = _sale_operation(db, disposal, item, price, user).id
        link.prev_status = item.status
        item.status = "disposed"
        details = f"{DISPOSAL_KINDS[disposal.kind]} {disposal.on_date:%d.%m.%Y}, акт № {disposal.number}"
        if price:
            details += f", {price:,.2f} ₽".replace(",", " ").replace(".", ",")
        if disposal.reason:
            details += f": {disposal.reason}"
        asset_service.event(db, item, user, "disposed", details)
    disposal.status, disposal.decided_by, disposal.decided_at = "done", user.id, dt.datetime.now(dt.UTC)
    db.flush()


def close(db: Session, disposal: AssetDisposal, user: User, status: str, reason: str = "") -> None:
    """Отклонить (ФД) или отменить (автор): единицы остаются."""
    if disposal.status != "pending":
        raise DisposalError("Выбытие уже не на согласовании")
    disposal.status, disposal.reject_reason = status, reason
    disposal.decided_by, disposal.decided_at = user.id, dt.datetime.now(dt.UTC)
    for _, item in lines(db, disposal):
        asset_service.event(db, item, user, "disposal_" + status, f"акт № {disposal.number}"
                            + (f": {reason}" if reason else ""))
    db.flush()


def undo(db: Session, disposal: AssetDisposal, user: User) -> None:
    """Отменить проведённое выбытие (ошибка): проводки удаляются, единицы возвращаются в прежний статус."""
    if disposal.status != "done":
        raise DisposalError("Отменить можно только проведённое выбытие")
    for link, item in lines(db, disposal):
        fa = db.get(FixedAsset, item.fixed_asset_id) if item.fixed_asset_id else None
        if fa is not None and fa.disposed_on is not None:
            fixed_assets.undo_disposal(db, fa)
        if link.operation_id is not None:
            op = db.get(Operation, link.operation_id)
            if op is not None:
                db.delete(op)
            link.operation_id = None
        item.status = link.prev_status or "stock"
        asset_service.event(db, item, user, "disposal_undone", f"акт № {disposal.number}")
    disposal.status = "undone"
    db.flush()
