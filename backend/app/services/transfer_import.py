"""Загрузка внутренних передач из Excel (docs/TZ.md, раздел 29, «Детали 20.4»).

Строка — товар с количеством (или сумма вручную); строки с одинаковыми датой, «откуда» и «куда» — один документ.
Документ с ошибкой в любой строке не загружается (причина — у каждой его строки), остальные проводятся.
"""

from dataclasses import dataclass, field

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Nomenclature, User
from app.services import internal_turnover as transfers
from app.services.excel_import import (
    Column,
    RowError,
    RowProblem,
    _date,
    _decimal,
    _help_sheet,
    _read_rows,
    _sheet_with_header,
    _text,
    _to_bytes,
    cfo_lookup,
)

TRANSFER_COLUMNS = [
    Column("Дата", "date", True, 12, hint="ДД.ММ.ГГГГ"),
    Column("Откуда (ЦФО)", "from", True, 26, True, "Кто передаёт: код, путь «Компания / … / Магазин» или название"),
    Column("Куда (ЦФО)", "to", True, 26, True, "Кто получает"),
    Column("Артикул", "article", False, 14, True),
    Column("Наименование", "name", False, 32, hint="Товар ищется по артикулу, без него — по наименованию"),
    Column("Количество", "quantity", False, 12, hint="Сумма = количество × внутренняя цена без НДС"),
    Column("Сумма вручную", "amount", False, 14, hint="Только если цены в справочнике нет"),
    Column("Описание", "description", False, 30),
]


def template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Передачи"
    _sheet_with_header(ws, TRANSFER_COLUMNS)
    _help_sheet(wb, TRANSFER_COLUMNS, [
        "Внутренние передачи между направлениями: одна строка — товар с количеством.",
        "Строки с одинаковыми датой, «Откуда» и «Куда» становятся одним документом передачи.",
        "Цена — по звену того, кто передаёт (производство → цена производства, логистика → логистики, "
        "торговля → дисконта), для ЦФО того, кто получает.",
    ])
    return _to_bytes(wb)


@dataclass
class TransfersResult:
    rows: int = 0
    created: list[int] = field(default_factory=list)  # номера документов
    errors: list[RowError] = field(default_factory=list)


def import_transfers(db: Session, organization_id: int, user: User, raw: bytes) -> TransfersResult:
    rows = _read_rows(raw, TRANSFER_COLUMNS)
    result = TransfersResult(rows=len(rows))
    items = list(db.scalars(select(Nomenclature).where(Nomenclature.organization_id == organization_id)))
    by_article = {i.article.strip().lower(): i for i in items if i.article}
    cfos = cfo_lookup(db, organization_id, active_only=False)
    groups: dict[tuple, dict] = {}
    for number, row in rows:
        try:
            on_date = _date(row.get("date"))
            if not _text(row.get("from")) or not _text(row.get("to")):
                raise RowProblem("Укажите «Откуда» и «Куда»")
            from_id, to_id = cfos.find(_text(row.get("from"))), cfos.find(_text(row.get("to")))
            article, name = _text(row.get("article")).lower(), _text(row.get("name")).lower()
            item = by_article.get(article) if article else None
            if article and item is None:
                raise RowProblem(f"Артикул «{_text(row.get('article'))}» не найден в номенклатуре")
            quantity = _decimal(row.get("quantity"), "Количество", 3)
            amount = _decimal(row.get("amount"), "Сумма вручную", 2)
            if item is None and name:
                found = [i for i in items if i.name.strip().lower() == name]
                if len(found) == 1:
                    item = found[0]
                elif amount is None:  # с суммой вручную название может быть любым («Тара»)
                    raise RowProblem(f"«{_text(row.get('name'))}»: " + (
                        "не найдено в номенклатуре" if not found else "несколько с таким названием — укажите артикул"))
            if amount is None and (item is None or quantity is None):
                raise RowProblem("Укажите товар и количество или сумму вручную")
        except RowProblem as exc:
            result.errors.append(RowError(number, str(exc)))
            continue
        group = groups.setdefault((on_date, from_id, to_id), {"rows": [], "lines": [], "description": ""})
        group["rows"].append(number)
        group["lines"].append(transfers.LineIn(nomenclature_id=item.id if item else None, quantity=quantity,
                                               amount=amount, title=_text(row.get("name"))[:255]))
        group["description"] = group["description"] or _text(row.get("description"))[:500]
    for (on_date, from_id, to_id), group in groups.items():
        try:
            with db.begin_nested():
                doc = transfers.create_document(db, organization_id, user, on_date=on_date, from_cfo_id=from_id,
                                                to_cfo_id=to_id, lines=group["lines"],
                                                description=group["description"], source="excel")
                result.created.append(doc.number)
        except transfers.InternalError as exc:
            result.errors += [RowError(n, f"передача не загружена: {exc}") for n in group["rows"]]
    result.errors.sort(key=lambda e: e.row)
    return result
