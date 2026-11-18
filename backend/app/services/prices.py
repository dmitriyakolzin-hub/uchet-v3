"""Внутренние цены по SKU и звенья цепочки — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.3»).

Цена на дату для ЦФО — самая «близкая»: своя цена ЦФО, затем его родителей, затем общая (без ЦФО); на каждом уровне —
последняя, начавшая действовать не позже даты. Цена может быть с НДС — тогда сумма без НДС считается по ставке.
Звено цепочки ЦФО — своё или ближайшего родителя.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import Cfo, Nomenclature
from app.models.cfo import CHAIN_LINKS
from app.models.price import PRICE_KINDS, VAT_RATES, NomenclaturePrice
from app.services.excel_import import (
    Column,
    RowError,
    RowProblem,
    _date,
    _decimal,
    _flag,
    _help_sheet,
    _read_rows,
    _sheet_with_header,
    _text,
    _to_bytes,
    cfo_lookup,
)

# Звено-продавец → вид цены, по которой оно передаёт следующему звену
TRANSFER_PRICE = {"production": "production", "logistics": "logistics", "trade": "discount"}


class PriceError(ValueError):
    pass


def chain_link(db: Session, cfo: Cfo | None) -> str | None:
    """Звено ЦФО: своё или ближайшего родителя."""
    if cfo is None:
        return None
    ids = [int(i) for i in str(cfo.path).split(".")]
    links = dict(db.execute(select(Cfo.id, Cfo.chain_link).where(Cfo.id.in_(ids))).all())
    for cfo_id in reversed(ids):
        if links.get(cfo_id):
            return links[cfo_id]
    return None


def without_vat(price: Decimal, vat_included: bool, vat_rate: int) -> Decimal:
    if not vat_included or not vat_rate:
        return price
    return round_money(price * 100 / (100 + vat_rate))


@dataclass
class Found:
    price: Decimal  # как введена
    net: Decimal  # без НДС
    vat_included: bool
    vat_rate: int
    valid_from: dt.date
    cfo_id: int | None  # чья цена: ЦФО или общая (None)
    price_id: int


def price_on(db: Session, organization_id: int, nomenclature_id: int, cfo_id: int | None, on_date: dt.date,
             kind: str) -> Found | None:
    """Цена вида kind на дату для ЦФО (или общая, если ЦФО не указан)."""
    if kind not in PRICE_KINDS:
        raise PriceError(f"Нет вида цены «{kind}»")
    levels: list[int | None] = []
    if cfo_id is not None:
        cfo = db.get(Cfo, cfo_id)
        if cfo is not None:
            levels = [int(i) for i in reversed(str(cfo.path).split("."))]
    column = getattr(NomenclaturePrice, kind)
    rows = list(db.scalars(select(NomenclaturePrice).where(
        NomenclaturePrice.organization_id == organization_id, NomenclaturePrice.nomenclature_id == nomenclature_id,
        NomenclaturePrice.valid_from <= on_date, column.is_not(None),
        or_(NomenclaturePrice.cfo_id.is_(None), NomenclaturePrice.cfo_id.in_(levels or [0])),
    ).order_by(NomenclaturePrice.valid_from.desc())))
    for level in [*levels, None]:
        row = next((r for r in rows if r.cfo_id == level), None)
        if row is not None:
            price = getattr(row, kind)
            return Found(price, without_vat(price, row.vat_included, row.vat_rate), row.vat_included, row.vat_rate,
                         row.valid_from, row.cfo_id, row.id)
    return None


def validate(db: Session, organization_id: int, *, nomenclature_id: int, cfo_id: int | None, vat_rate: int,
             prices: dict[str, Decimal | None]) -> None:
    item = db.get(Nomenclature, nomenclature_id)
    if item is None or item.organization_id != organization_id:
        raise PriceError("Номенклатура не найдена в этой организации")
    if cfo_id is not None:
        cfo = db.get(Cfo, cfo_id)
        if cfo is None or cfo.organization_id != organization_id:
            raise PriceError("ЦФО не найден в этой организации")
    if vat_rate not in VAT_RATES:
        raise PriceError("Ставка НДС: 0, 5, 7, 10, 20 или 22 %")
    if not any(v is not None for v in prices.values()):
        raise PriceError("Укажите хотя бы одну цену")
    if any(v is not None and v < 0 for v in prices.values()):
        raise PriceError("Цена не может быть отрицательной")


# --- Excel ---

PRICE_COLUMNS = [
    Column("Артикул", "article", False, 14, True),
    Column("Наименование", "name", False, 36, hint="Номенклатура ищется по артикулу, без него — по наименованию"),
    Column("ЦФО", "cfo", False, 26, True, "Пусто — цена для всей организации; код, путь или название филиала / магазина"),
    Column("Действует с", "valid_from", True, 12, hint="ДД.ММ.ГГГГ"),
    *[Column(title, kind, False, 14, hint="Число; пусто — нет такой цены") for kind, title in PRICE_KINDS.items()],
    Column("Цены с НДС", "vat_included", False, 11, hint="да / нет; пусто — без НДС"),
    Column("Ставка НДС, %", "vat_rate", False, 11, hint="0, 5, 7, 10, 20 или 22; пусто — 0"),
]


def prices_xlsx(db: Session, organization_id: int) -> bytes:
    """Шаблон: вся номенклатура организации и её общие цены на сегодня."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Цены"
    _sheet_with_header(ws, PRICE_COLUMNS)
    today = dt.date.today()
    items = db.scalars(select(Nomenclature).where(Nomenclature.organization_id == organization_id,
                                                  Nomenclature.is_active).order_by(Nomenclature.name))
    for r, item in enumerate(items, start=2):
        found = {k: price_on(db, organization_id, item.id, None, today, k) for k in PRICE_KINDS}
        first = next((f for f in found.values() if f), None)
        values = [item.article or None, item.name, None, today,
                  *[f.price if f else None for f in found.values()],
                  ("да" if first.vat_included else "нет") if first else None, first.vat_rate if first else None]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            if c == 4:
                cell.number_format = "DD.MM.YYYY"
            elif 5 <= c <= 8:
                cell.number_format = "#,##0.00"
    _help_sheet(wb, PRICE_COLUMNS, [
        "Внутренние цены: одна строка — цены товара с даты (для всей организации или для филиала / магазина).",
        "Цена производства — по ней производство передаёт логистике (СГП); цена логистики — логистика торговле;",
        "цена продажи — клиенту (план выручки); цена дисконта — торговля дисконту.",
        "Та же дата и тот же ЦФО — цены обновляются (пустая ячейка цену не стирает), иначе добавляются в историю.",
    ])
    return _to_bytes(wb)


@dataclass
class PricesResult:
    rows: int = 0
    created: int = 0
    updated: int = 0
    errors: list[RowError] = field(default_factory=list)


def import_prices(db: Session, organization_id: int, raw: bytes, user_id: int | None) -> PricesResult:
    rows = _read_rows(raw, PRICE_COLUMNS)
    result = PricesResult(rows=len(rows))
    items = list(db.scalars(select(Nomenclature).where(Nomenclature.organization_id == organization_id)))
    by_article = {i.article.strip().lower(): i for i in items if i.article}
    cfos = cfo_lookup(db, organization_id, active_only=False)
    for number, row in rows:
        try:
            with db.begin_nested():
                article, name = _text(row.get("article")).lower(), _text(row.get("name")).lower()
                item = by_article.get(article) if article else None
                if item is None and article:
                    raise RowProblem(f"Артикул «{_text(row.get('article'))}» не найден в номенклатуре")
                if item is None:
                    found = [i for i in items if i.name.strip().lower() == name]
                    if not name:
                        raise RowProblem("Укажите артикул или наименование")
                    if len(found) != 1:
                        raise RowProblem(f"«{_text(row.get('name'))}»: " + (
                            "не найдено в номенклатуре" if not found else "несколько с таким названием — укажите артикул"))
                    item = found[0]
                cfo_id = cfos.find(_text(row.get("cfo"))) if _text(row.get("cfo")) else None
                valid_from = _date(row.get("valid_from"))
                prices = {k: _decimal(row.get(k), PRICE_KINDS[k], 2) for k in PRICE_KINDS}
                rate_text = _text(row.get("vat_rate")).replace("%", "").strip()
                if rate_text and not rate_text.isdigit():
                    raise RowProblem("«Ставка НДС»: укажите число процентов")
                vat_rate = int(rate_text) if rate_text else 0
                vat_included = _flag(row.get("vat_included"), "Цены с НДС")
                record = db.scalar(select(NomenclaturePrice).where(
                    NomenclaturePrice.nomenclature_id == item.id, NomenclaturePrice.valid_from == valid_from,
                    NomenclaturePrice.cfo_id.is_(None) if cfo_id is None else NomenclaturePrice.cfo_id == cfo_id))
                merged = {k: v if v is not None else (getattr(record, k) if record else None) for k, v in prices.items()}
                try:
                    validate(db, organization_id, nomenclature_id=item.id, cfo_id=cfo_id, vat_rate=vat_rate,
                             prices=merged)
                except PriceError as exc:
                    raise RowProblem(str(exc)) from None
                if record is None:
                    record = NomenclaturePrice(organization_id=organization_id, nomenclature_id=item.id,
                                               cfo_id=cfo_id, valid_from=valid_from, created_by=user_id)
                    db.add(record)
                    result.created += 1
                else:
                    result.updated += 1
                for k, v in merged.items():
                    setattr(record, k, v)
                record.vat_included, record.vat_rate = vat_included, vat_rate
                db.flush()
        except RowProblem as exc:
            result.errors.append(RowError(number, str(exc)))
    return result


def link_title(link: str | None) -> str:
    return CHAIN_LINKS.get(link or "", "")
