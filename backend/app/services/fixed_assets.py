"""Основные средства (docs/TZ.md, раздел 14, «Детали 4.4»).

Остатки по каждому ОС берутся из журнала (аналитика «Основное средство» на 01, 02, 08) через
select_lines — так же, как в отчётах, поэтому карточка и ОСВ показывают одни и те же цифры.

commission   — ввод в эксплуатацию: Дт 01 — Кт 08 на стоимость (до даты остатков — без операции)
preview      — расчёт амортизации за месяц: стоимость / срок, не больше остаточной стоимости
depreciate   — начисление за месяц: по операции на юрлицо, Дт счёт затрат (ЦФО, статья) — Кт 02
"""

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import (
    Account,
    Article,
    DepreciationRun,
    FixedAsset,
    LegalEntity,
    Operation,
    Organization,
    Posting,
    User,
)
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import journal
from app.services.journal import JournalError
from app.services.ledger import select_lines

ZERO = Decimal("0.00")
DEPRECIATION_ARTICLE = "Амортизация основных средств"
MONTHS_GENITIVE = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
                   "октябрь", "ноябрь", "декабрь"]
MONTHS_PREPOSITIONAL = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
                        "октябре", "ноябре", "декабре"]


class AssetError(ValueError):
    """Действие с основным средством невозможно; текст — для пользователя."""


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _account_ids(db: Session) -> dict[str, int]:
    return {a.code: a.id for a in db.scalars(select(Account).where(Account.code.in_(("01", "02", "08"))))}


@dataclass
class Balance:
    cost: Decimal = ZERO  # сальдо 01 — стоимость в эксплуатации
    depreciation: Decimal = ZERO  # сальдо 02 (кредитовое) — накопленная амортизация
    investment: Decimal = ZERO  # сальдо 08 — вложения, ещё не введённые в эксплуатацию

    @property
    def residual(self) -> Decimal:
        return self.cost - self.depreciation


def balances(db: Session, organization_id: int, on_date: dt.date | None = None,
             asset_ids: list[int] | None = None) -> dict[int, Balance]:
    """Сальдо по каждому ОС на конец дня on_date (проведённые операции)."""
    ids = _account_ids(db)
    result: dict[int, Balance] = {}
    for code, field, sign in (("01", "cost", 1), ("02", "depreciation", -1), ("08", "investment", 1)):
        lines = select_lines(organization_id, account_id=ids[code], date_to=on_date).subquery()
        signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
        query = select(lines.c.fixed_asset, func.sum(signed)).where(lines.c.fixed_asset.is_not(None))
        if asset_ids is not None:
            query = query.where(lines.c.fixed_asset.in_(asset_ids))
        for asset_id, total in db.execute(query.group_by(lines.c.fixed_asset)):
            setattr(result.setdefault(asset_id, Balance()), field, sign * total + ZERO)  # + ZERO: −0 → 0
    return result


def monthly_amount(asset: FixedAsset) -> Decimal:
    """Линейный способ: стоимость / срок полезного использования в месяцах."""
    return round_money(asset.cost / asset.useful_life_months)


def _cost_article(db: Session, asset: FixedAsset) -> Article:
    article = db.get(Article, asset.pl_item_id) if asset.pl_item_id else None
    if asset.cfo_id is None or article is None or not article.is_pl or article.debit_account_id is None:
        raise AssetError(f"«{asset.name}»: не заполнены ЦФО или статья амортизации (статья ОПиУ с шаблоном проводки)")
    return article


def _posted_operation(db: Session, organization_id: int, user: User, body: OperationIn) -> Operation:
    op = Operation(organization_id=organization_id, created_by=user.id, status="draft")
    try:
        journal.save_draft(db, op, body, user)
        op.number = journal.next_number(db, organization_id)
        db.add(op)
        db.flush()
        journal._event(db, op, user, "created")
        journal.post(db, op, user)
    except JournalError as exc:
        raise AssetError(f"Проводки не прошли проверку журнала: {exc}") from None
    return op


def commission(db: Session, asset: FixedAsset, on_date: dt.date, user: User) -> Operation | None:
    """Ввести в эксплуатацию. До даты начальных остатков операции нет: стоимость и накопленную
    амортизацию такого ОС вводят на вкладке «Начальные остатки» (Дт 01 — Кт 00, Дт 00 — Кт 02)."""
    if asset.commissioned_on is not None:
        raise AssetError("Основное средство уже введено в эксплуатацию")
    if asset.cost <= 0:
        raise AssetError("Укажите стоимость основного средства")
    _cost_article(db, asset)
    organization = db.get(Organization, asset.organization_id)
    opening = organization.opening_balance_date
    op = None
    if opening is None or on_date > opening:
        investment = balances(db, asset.organization_id, on_date, [asset.id]).get(asset.id, Balance()).investment
        if investment < asset.cost:
            raise AssetError(
                f"На счёте 08 по «{asset.name}» на {on_date:%d.%m.%Y} — {investment:.2f} ₽, а стоимость — "
                f"{asset.cost:.2f} ₽. Сначала проведите поступление (Дт 08 — Кт 60, аналитика «Основное средство») "
                "или поправьте стоимость")
        ids = _account_ids(db)
        an = AnalyticsIn(fixed_asset=asset.id)
        body = OperationIn(legal_entity_id=asset.legal_entity_id, op_date=on_date,
                           description=f"Ввод в эксплуатацию: {asset.name}",
                           postings=[PostingIn(debit_account_id=ids["01"], credit_account_id=ids["08"],
                                               amount=asset.cost, debit=an, credit=an)])
        op = _posted_operation(db, asset.organization_id, user, body)
    asset.commissioned_on, asset.commission_operation_id = on_date, op.id if op else None
    db.flush()
    return op


def uncommission(db: Session, asset: FixedAsset) -> None:
    """Отменить ввод в эксплуатацию — пока по ОС нет начисленной амортизации."""
    if asset.commissioned_on is None:
        raise AssetError("Основное средство не введено в эксплуатацию")
    if asset.disposed_on is not None:
        raise AssetError("Основное средство выбыло — сначала отмените выбытие")
    ids = _account_ids(db)
    lines = select_lines(asset.organization_id, statuses=("draft", "posted"), account_id=ids["02"],
                         analytics={"fixed_asset": asset.id}, opening=False).subquery()
    if db.scalar(select(func.count()).select_from(lines)):
        raise AssetError("По основному средству начислена амортизация — сначала отмените начисления")
    if asset.commission_operation_id:
        op = db.get(Operation, asset.commission_operation_id)
        if op is not None:
            db.delete(op)
    asset.commissioned_on, asset.commission_operation_id = None, None
    db.flush()


@dataclass
class DepreciationLine:
    asset: FixedAsset
    amount: Decimal
    residual_before: Decimal


def _eligible(db: Session, organization_id: int, legal_entity_id: int, year: int, month: int) -> list[FixedAsset]:
    """ОС, по которым амортизация в этом месяце начисляется: введены до начала месяца, не в архиве,
    не выбыли до конца месяца (в месяце выбытия амортизация не начисляется)."""
    start, end = month_bounds(year, month)
    return list(db.scalars(select(FixedAsset).where(
        FixedAsset.organization_id == organization_id, FixedAsset.legal_entity_id == legal_entity_id,
        FixedAsset.is_active.is_(True), FixedAsset.commissioned_on.is_not(None), FixedAsset.commissioned_on < start,
        or_(FixedAsset.disposed_on.is_(None), FixedAsset.disposed_on > end),
    ).order_by(FixedAsset.name, FixedAsset.id)))


def preview(db: Session, organization_id: int, legal_entity_id: int, year: int, month: int) -> list[DepreciationLine]:
    """Сколько начислить за месяц по каждому ОС юрлица: стоимость / срок, но не больше остатка 01 − 02;
    в последнем месяце срока — весь остаток."""
    assets = _eligible(db, organization_id, legal_entity_id, year, month)
    if not assets:
        return []
    _, end = month_bounds(year, month)
    found = balances(db, organization_id, end, [a.id for a in assets])
    result = []
    for asset in assets:
        residual = found.get(asset.id, Balance()).residual
        # месяц срока: 1 — следующий за месяцем ввода; в последнем месяце срока — весь остаток (копейки)
        month_no = year * 12 + month - (asset.commissioned_on.year * 12 + asset.commissioned_on.month)
        amount = residual if month_no >= asset.useful_life_months else min(monthly_amount(asset), residual)
        if amount > 0:
            result.append(DepreciationLine(asset, amount, residual))
    return result


def depreciate(db: Session, organization_id: int, legal_entity_id: int, year: int, month: int,
               user: User) -> DepreciationRun | None:
    """Начислить амортизацию юрлица за месяц; None — начислять нечего."""
    if db.scalar(select(DepreciationRun.id).where(
            DepreciationRun.legal_entity_id == legal_entity_id, DepreciationRun.year == year,
            DepreciationRun.month == month)):
        raise AssetError("Амортизация за этот месяц уже начислена")
    entity = db.get(LegalEntity, legal_entity_id)
    later = db.scalar(select(DepreciationRun).where(
        DepreciationRun.legal_entity_id == legal_entity_id,
        DepreciationRun.year * 12 + DepreciationRun.month > year * 12 + month).limit(1))
    if later:
        raise AssetError(f"{entity.short_name}: амортизация уже начислена за {MONTHS_GENITIVE[later.month - 1]} "
                         f"{later.year} — начислять нужно по порядку месяцев")
    py, pm = _prev_month(year, month)
    opening = db.get(Organization, organization_id).opening_balance_date
    prev_start, prev_end = month_bounds(py, pm)
    prev_needed = (opening is None or prev_end > opening) and any(
        a.commissioned_on < prev_start for a in _eligible(db, organization_id, legal_entity_id, year, month))
    if prev_needed and not db.scalar(select(DepreciationRun.id).where(
            DepreciationRun.legal_entity_id == legal_entity_id, DepreciationRun.year == py,
            DepreciationRun.month == pm)):
        raise AssetError(f"{entity.short_name}: сначала начислите амортизацию за {MONTHS_GENITIVE[pm - 1]} {py}")
    lines = preview(db, organization_id, legal_entity_id, year, month)
    if not lines:
        return None
    credit = _account_ids(db)["02"]
    postings = []
    for line in lines:
        article = _cost_article(db, line.asset)
        postings.append(PostingIn(debit_account_id=article.debit_account_id, credit_account_id=credit,
                                  amount=line.amount,
                                  debit=AnalyticsIn(cfo=line.asset.cfo_id, pl_item=article.id),
                                  credit=AnalyticsIn(fixed_asset=line.asset.id)))
    _, end = month_bounds(year, month)
    body = OperationIn(legal_entity_id=legal_entity_id, op_date=end,
                       description=f"Амортизация основных средств за {MONTHS_GENITIVE[month - 1]} {year}",
                       postings=postings)
    op = _posted_operation(db, organization_id, user, body)
    run = DepreciationRun(organization_id=organization_id, legal_entity_id=legal_entity_id, year=year, month=month,
                          operation_id=op.id, created_by=user.id)
    db.add(run)
    db.flush()
    return run


def depreciate_all(db: Session, organization_id: int, year: int, month: int, user: User) -> list[DepreciationRun]:
    """Начислить за месяц по всем юрлицам организации, где есть что начислять."""
    entities = db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)
                          .order_by(LegalEntity.short_name))
    done = db.scalars(select(DepreciationRun.legal_entity_id).where(
        DepreciationRun.organization_id == organization_id, DepreciationRun.year == year,
        DepreciationRun.month == month)).all()
    runs = []
    for entity in entities:
        if entity.id in done or not preview(db, organization_id, entity.id, year, month):
            continue
        run = depreciate(db, organization_id, entity.id, year, month, user)
        if run is not None:
            runs.append(run)
    if not runs:
        raise AssetError(f"Начислять нечего: в {MONTHS_PREPOSITIONAL[month - 1]} {year} нет основных средств "
                         "с остаточной стоимостью без начисленной амортизации")
    return runs


def undo_depreciation(db: Session, run: DepreciationRun) -> None:
    """Отменить начисление — только последнего месяца юрлица: от него зависят следующие."""
    later = db.scalar(select(DepreciationRun.id).where(
        DepreciationRun.legal_entity_id == run.legal_entity_id,
        DepreciationRun.year * 12 + DepreciationRun.month > run.year * 12 + run.month).limit(1))
    if later:
        raise AssetError("Сначала отмените начисления следующих месяцев")
    if run.operation_id:
        op = db.get(Operation, run.operation_id)
        if op is not None:
            db.delete(op)
    db.delete(run)
    db.flush()


def run_total(db: Session, run: DepreciationRun) -> Decimal:
    if run.operation_id is None:
        return ZERO
    return db.scalar(select(func.coalesce(func.sum(Posting.amount_rub), 0)).where(
        Posting.operation_id == run.operation_id)) or ZERO


# --- Выбытие: продажа и списание ---

SALE_ARTICLE = "Доход от продажи основных средств"
WRITEOFF_ARTICLE = "Списание основных средств"
DISPOSAL_KINDS = {"sale": "Продажа", "writeoff": "Списание"}


def vat_included(price: Decimal, rate: int) -> Decimal:
    """НДС в цене: цена × ставка / (100 + ставка), до копеек."""
    return round_money(price * rate / (100 + rate)) if rate else ZERO


def dispose(db: Session, asset: FixedAsset, on_date: dt.date, kind: str, user: User, *,
            counterparty_id: int | None = None, contract_id: int | None = None,
            price: Decimal = ZERO, vat_rate: int = 0) -> Operation:
    """Продажа или списание: 02 → 01, остаточная стоимость → 91.02; при продаже — выручка на 91.01 и НДС."""
    if asset.commissioned_on is None:
        raise AssetError("Основное средство не введено в эксплуатацию")
    if asset.disposed_on is not None:
        raise AssetError("Основное средство уже выбыло")
    if on_date < asset.commissioned_on:
        raise AssetError("Дата выбытия раньше даты ввода в эксплуатацию")
    if kind == "sale" and (counterparty_id is None or price <= 0):
        raise AssetError("Для продажи укажите покупателя и цену")
    ids = _account_ids(db)
    later = select_lines(asset.organization_id, statuses=("draft", "posted"), account_id=ids["02"],
                         analytics={"fixed_asset": asset.id}, opening=False, date_from=on_date).subquery()
    if db.scalar(select(func.count()).select_from(later)):
        raise AssetError("После даты выбытия по ОС начислена амортизация — сначала отмените эти начисления")
    # амортизация по месяц перед выбытием должна быть начислена
    start = on_date.replace(day=1)
    prev_end = start - dt.timedelta(days=1)
    opening = db.get(Organization, asset.organization_id).opening_balance_date
    if asset.commissioned_on < prev_end.replace(day=1) and (opening is None or prev_end > opening) and not db.scalar(
            select(DepreciationRun.id).where(DepreciationRun.legal_entity_id == asset.legal_entity_id,
                                             DepreciationRun.year == prev_end.year,
                                             DepreciationRun.month == prev_end.month)):
        raise AssetError(f"Сначала начислите амортизацию за {MONTHS_GENITIVE[prev_end.month - 1]} {prev_end.year}")
    if asset.cfo_id is None:
        raise AssetError("В карточке не указан ЦФО — выбытие не на что отнести")
    articles = {a.name: a for a in db.scalars(select(Article).where(Article.name.in_((SALE_ARTICLE, WRITEOFF_ARTICLE))))}
    if len(articles) < 2:
        raise AssetError(f"Нет статей «{SALE_ARTICLE}» и «{WRITEOFF_ARTICLE}» — восстановите их в справочнике статей")

    b = balances(db, asset.organization_id, on_date, [asset.id]).get(asset.id, Balance())
    an = AnalyticsIn(fixed_asset=asset.id)
    codes = {a.code: a.id for a in db.scalars(select(Account).where(Account.code.in_(("62.01", "68.02", "91.01", "91.02"))))}
    postings = []
    if b.depreciation > 0:
        postings.append(PostingIn(debit_account_id=ids["02"], credit_account_id=ids["01"], amount=b.depreciation,
                                  debit=an, credit=an))
    if b.residual > 0:
        postings.append(PostingIn(debit_account_id=codes["91.02"], credit_account_id=ids["01"], amount=b.residual,
                                  debit=AnalyticsIn(cfo=asset.cfo_id, pl_item=articles[WRITEOFF_ARTICLE].id), credit=an))
    if kind == "sale":
        income = AnalyticsIn(cfo=asset.cfo_id, pl_item=articles[SALE_ARTICLE].id)
        postings.append(PostingIn(debit_account_id=codes["62.01"], credit_account_id=codes["91.01"], amount=price,
                                  debit=AnalyticsIn(counterparty=counterparty_id, contract=contract_id), credit=income))
        vat = vat_included(price, vat_rate)
        if vat > 0:
            postings.append(PostingIn(debit_account_id=codes["91.02"], credit_account_id=codes["68.02"], amount=vat,
                                      debit=income))
    if not postings:
        raise AssetError("Стоимость ОС на дату выбытия — ноль, проводить нечего")
    body = OperationIn(legal_entity_id=asset.legal_entity_id, op_date=on_date,
                       article_id=articles[SALE_ARTICLE if kind == "sale" else WRITEOFF_ARTICLE].id,
                       description=f"{DISPOSAL_KINDS[kind]} основного средства: {asset.name}", postings=postings)
    op = _posted_operation(db, asset.organization_id, user, body)
    asset.disposed_on, asset.disposal_kind, asset.disposal_operation_id = on_date, kind, op.id
    db.flush()
    return op


def undo_disposal(db: Session, asset: FixedAsset) -> None:
    if asset.disposed_on is None:
        raise AssetError("Основное средство не выбывало")
    if asset.disposal_operation_id:
        op = db.get(Operation, asset.disposal_operation_id)
        if op is not None:
            db.delete(op)
    asset.disposed_on, asset.disposal_kind, asset.disposal_operation_id = None, None, None
    db.flush()

