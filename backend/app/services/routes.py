"""Маршрут товара до точки продаж — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.5»).

purchased — покупной, со склада: логистика → точка; product — выпускает производство (producer_cfo_id, ЦФО
со звеном «Производство»), в т. ч. полуфабрикат: производство → логистика → точка; assembled — доготавливается
на точке из составляющих (полуфабрикатов, покупных) по норме на 1 единицу.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Cfo, Nomenclature, NomenclatureComponent
from app.services import prices
from app.services.excel_import import (
    Column,
    RowError,
    RowProblem,
    _decimal,
    _help_sheet,
    _read_rows,
    _sheet_with_header,
    _text,
    _to_bytes,
    cfo_lookup,
)

ROUTES = {"purchased": "Покупной (со склада)", "product": "Продукция производства", "assembled": "Доготовка на точке"}
ROUTE_WORDS = {"покупной": "purchased", "со склада": "purchased", "товар": "purchased", "продукция": "product",
               "производство": "product", "полуфабрикат": "product", "продукция производства": "product",
               "доготовка": "assembled", "доготовка на точке": "assembled", "доготавливается": "assembled"}


class RouteError(ValueError):
    pass


def check_item(db: Session, item: Nomenclature) -> None:
    """Проверка карточки: у продукции — производство (ЦФО со звеном «Производство»); у остальных — без него."""
    if item.route != "product":
        item.producer_cfo_id = None
        return
    if item.producer_cfo_id is None:
        raise RouteError("У продукции укажите производство (ЦФО, которое её выпускает)")
    cfo = db.get(Cfo, item.producer_cfo_id)
    if cfo is None or cfo.organization_id != item.organization_id:
        raise RouteError("Производство: ЦФО не найден в этой организации")
    if prices.chain_link(db, cfo) != "production":
        raise RouteError(f"ЦФО «{cfo.name}» — не производство: укажите у него (или выше) звено «Производство»")


@dataclass
class ComponentIn:
    component_id: int
    quantity: Decimal


def set_components(db: Session, item: Nomenclature, components: list[ComponentIn]) -> None:
    """Заменить состав доготавливаемого товара."""
    if components and item.route != "assembled":
        raise RouteError("Состав указывается только у товара с маршрутом «Доготовка на точке»")
    seen = set()
    for c in components:
        part = db.get(Nomenclature, c.component_id)
        if part is None or part.organization_id != item.organization_id:
            raise RouteError("Составляющая не найдена в этой организации")
        if part.id == item.id:
            raise RouteError("Товар не может входить в свой состав")
        if part.route not in ("purchased", "product"):
            raise RouteError(f"«{part.name}»: у составляющей маршрут — покупной или продукция производства")
        if part.id in seen:
            raise RouteError(f"«{part.name}» указана дважды")
        if c.quantity <= 0:
            raise RouteError(f"«{part.name}»: норма должна быть больше нуля")
        seen.add(part.id)
    db.execute(delete(NomenclatureComponent).where(NomenclatureComponent.nomenclature_id == item.id))
    for c in components:
        db.add(NomenclatureComponent(nomenclature_id=item.id, component_id=c.component_id, quantity=c.quantity))
    db.flush()


def components_of(db: Session, item_id: int) -> list[NomenclatureComponent]:
    return list(db.scalars(select(NomenclatureComponent).where(NomenclatureComponent.nomenclature_id == item_id)
                           .order_by(NomenclatureComponent.id)))


# --- Excel ---

ROUTE_COLUMNS = [
    Column("Артикул", "article", False, 14, True),
    Column("Наименование", "name", False, 36, hint="Товар ищется по артикулу, без него — по наименованию"),
    Column("Маршрут", "route", False, 22, hint="покупной / продукция / доготовка; пусто — не менять"),
    Column("Производство (ЦФО)", "producer", False, 26, True, "Для продукции: код, путь или название ЦФО производства"),
    Column("Составляющая (артикул или наименование)", "component", False, 30, True,
           "Для доготовки: полуфабрикат или покупной товар; на каждую составляющую — своя строка с тем же товаром"),
    Column("Норма на 1 шт.", "norm", False, 12, hint="Сколько составляющей идёт на 1 единицу товара"),
]


def routes_xlsx(db: Session, organization_id: int) -> bytes:
    """Номенклатура с маршрутами и составом — в формате загрузки."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Маршруты"
    _sheet_with_header(ws, ROUTE_COLUMNS)
    cfos = {c.id: c for c in db.scalars(select(Cfo).where(Cfo.organization_id == organization_id))}
    names = {c.id: c.name for c in cfos.values()}
    label = {cid: " / ".join(names[int(i)] for i in str(c.path).split(".")) for cid, c in cfos.items()}
    items = list(db.scalars(select(Nomenclature).where(Nomenclature.organization_id == organization_id,
                                                       Nomenclature.is_active).order_by(Nomenclature.name)))
    by_id = {i.id: i for i in items}
    r = 2
    for item in items:
        parts = components_of(db, item.id) if item.route == "assembled" else []
        for part in parts or [None]:
            component = by_id.get(part.component_id) if part else None
            values = [item.article or None, item.name, ROUTES.get(item.route or "", None),
                      label.get(item.producer_cfo_id), (component.article or component.name) if component else None,
                      part.quantity if part else None]
            for c, value in enumerate(values, start=1):
                ws.cell(row=r, column=c, value=value)
            r += 1
    _help_sheet(wb, ROUTE_COLUMNS, [
        "Маршрут товара до точки продаж: покупной (со склада через логистику), продукция производства (в т. ч.",
        "полуфабрикат) или доготовка на точке из составляющих по норме на 1 единицу.",
        "Состав доготавливаемого товара заменяется составляющими из файла.",
    ])
    return _to_bytes(wb)


@dataclass
class RoutesResult:
    rows: int = 0
    updated: int = 0
    errors: list[RowError] = field(default_factory=list)


def import_routes(db: Session, organization_id: int, raw: bytes) -> RoutesResult:
    rows = _read_rows(raw, ROUTE_COLUMNS)
    result = RoutesResult(rows=len(rows))
    items = list(db.scalars(select(Nomenclature).where(Nomenclature.organization_id == organization_id)))
    by_article = {i.article.strip().lower(): i for i in items if i.article}
    cfos = cfo_lookup(db, organization_id, active_only=False)

    def find(article_or_name: str, title: str) -> Nomenclature:
        low = article_or_name.strip().lower()
        if low in by_article:
            return by_article[low]
        found = [i for i in items if i.name.strip().lower() == low]
        if len(found) != 1:
            raise RowProblem(f"{title} «{article_or_name}»: " + (
                "не найдено в номенклатуре" if not found else "несколько с таким названием — укажите артикул"))
        return found[0]

    plan: dict[int, dict] = {}  # товар → маршрут, производство, состав, строки
    for number, row in rows:
        try:
            key = _text(row.get("article")) or _text(row.get("name"))
            if not key:
                raise RowProblem("Укажите артикул или наименование")
            item = find(key, "Товар")
            entry = plan.setdefault(item.id, {"item": item, "rows": [], "route": None, "producer": None,
                                              "components": []})
            entry["rows"].append(number)
            route_text = _text(row.get("route")).lower()
            if route_text:
                route = ROUTE_WORDS.get(route_text) or next((k for k, v in ROUTES.items() if v.lower() == route_text), None)
                if route is None:
                    raise RowProblem(f"«Маршрут»: «{_text(row.get('route'))}» — укажите покупной, продукция или доготовка")
                entry["route"] = route
            if _text(row.get("producer")):
                entry["producer"] = cfos.find(_text(row.get("producer")))
            if _text(row.get("component")):
                part = find(_text(row.get("component")), "Составляющая")
                norm = _decimal(row.get("norm"), "Норма на 1 шт.", 4)
                if norm is None:
                    raise RowProblem("Укажите норму составляющей на 1 шт.")
                entry["components"].append(ComponentIn(part.id, norm))
        except RowProblem as exc:
            result.errors.append(RowError(number, str(exc)))
    failed = {e.row for e in result.errors}
    for entry in plan.values():
        if failed & set(entry["rows"]):
            continue
        item = entry["item"]
        try:
            with db.begin_nested():
                if entry["route"]:
                    item.route = entry["route"]
                if entry["producer"]:
                    item.producer_cfo_id = entry["producer"]
                check_item(db, item)
                if entry["components"]:
                    set_components(db, item, entry["components"])
                db.flush()
                result.updated += 1
        except RouteError as exc:
            result.errors += [RowError(n, str(exc)) for n in entry["rows"]]
    result.errors.sort(key=lambda e: e.row)
    return result
