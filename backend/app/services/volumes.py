"""План объёмов и расчёт плана ОПиУ по цепочке — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.6»).

Строка плана: точка (ЦФО торговли или дисконта) + товар (с маршрутом) + вид: продажа клиентам / на дисконт (какой
дисконт) / утиль; по месяцам — количество. «Рассчитать план» протягивает цепочку и пишет ячейки ОПиУ «из объёмов»:
- продажа — выручка точки (количество × цена продажи, статья выручки — одна на весь план);
- на дисконт — внутренняя выручка точки и внутренняя закупка дисконта (цена дисконта);
- всё, что точка торговли получает (продажа + на дисконт + утиль), — внутренняя закупка у логистики и внутренняя
  выручка логистики (цена логистики); дальше по маршруту: продукция — закупка логистики у своего производства
  и выручка производства (цена производства); доготовка — так по каждой составляющей (количество × норма).
Дисконт получает товар от торговли (строки «на дисконт»), поэтому его продажа и утиль цепочку не тянут.
Цены — без НДС, на 1-е число месяца, для ЦФО получателя. Нет цены — план не считается (список недостающих).
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import Article, Budget, BudgetVersion, Cfo, Nomenclature, Operation, PlanVolume, User
from app.models.budget import VOLUME_KINDS
from app.models.price import PRICE_KINDS
from app.services import plan, prices, routes
from app.services.articles import system_article
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
from app.services.plan import MONTH_TITLES, PlanError

ZERO = Decimal("0.00")
KIND_WORDS = {"продажа": "sale", "на дисконт": "discount", "дисконт": "discount", "утиль": "waste",
              "списание": "waste"}


@dataclass
class VolumeIn:
    cfo_id: int
    nomenclature_id: int
    kind: str
    discount_cfo_id: int | None
    month: int
    quantity: Decimal | None  # пусто или 0 — удалить


def _links(db: Session, organization_id: int) -> dict[int, str | None]:
    cfos = {c.id: c for c in db.scalars(select(Cfo).where(Cfo.organization_id == organization_id))}
    result = {}
    for cfo_id, cfo in cfos.items():
        ids = [int(i) for i in str(cfo.path).split(".")]
        result[cfo_id] = next((cfos[i].chain_link for i in reversed(ids) if i in cfos and cfos[i].chain_link), None)
    return result


def validate(db: Session, organization_id: int, months: list[int], rows: list[VolumeIn],
             branch: set[int] | None = None) -> None:
    links = _links(db, organization_id)
    for r in rows:
        cfo = db.get(Cfo, r.cfo_id)
        if cfo is None or cfo.organization_id != organization_id:
            raise PlanError("Точка (ЦФО) не найдена в этой организации")
        if links.get(r.cfo_id) not in ("trade", "discount"):
            raise PlanError(f"«{cfo.name}» — не точка продаж: нужно звено «Торговля» или «Дисконт»")
        if branch is not None and r.cfo_id not in branch:
            raise PlanError(f"«{cfo.name}» вне вашей ветки")
        item = db.get(Nomenclature, r.nomenclature_id)
        if item is None or item.organization_id != organization_id:
            raise PlanError("Товар не найден в этой организации")
        if not item.route:
            raise PlanError(f"«{item.name}»: в карточке не указан маршрут до точки продаж")
        if r.kind not in VOLUME_KINDS:
            raise PlanError("Вид строки: продажа, на дисконт или утиль")
        if r.kind == "discount":
            target = db.get(Cfo, r.discount_cfo_id) if r.discount_cfo_id else None
            if target is None or target.organization_id != organization_id or links.get(target.id) != "discount":
                raise PlanError(f"«{item.name}» на дисконт: выберите ЦФО со звеном «Дисконт»")
            if links.get(r.cfo_id) != "trade":
                raise PlanError("На дисконт передаёт точка торговли")
        elif r.discount_cfo_id is not None:
            raise PlanError("Дисконт указывается только у строки «на дисконт»")
        if r.month not in months:
            raise PlanError(f"Месяц {r.month} не входит в период бюджета")
        if r.quantity is not None and r.quantity < 0:
            raise PlanError("Количество не может быть отрицательным")


def set_volumes(db: Session, version: BudgetVersion, rows: list[VolumeIn], branch: set[int] | None = None) -> int:
    budget = db.get(Budget, version.budget_id)
    if version.status == "archived":
        raise PlanError("Версия в архиве — менять нельзя")
    validate(db, budget.organization_id, budget.months, rows, branch)
    changed = 0
    for r in rows:
        cell = db.scalar(select(PlanVolume).where(
            PlanVolume.budget_version_id == version.id, PlanVolume.cfo_id == r.cfo_id,
            PlanVolume.nomenclature_id == r.nomenclature_id, PlanVolume.kind == r.kind, PlanVolume.month == r.month,
            PlanVolume.discount_cfo_id.is_(None) if r.discount_cfo_id is None
            else PlanVolume.discount_cfo_id == r.discount_cfo_id))
        if not r.quantity:
            if cell is not None:
                db.delete(cell)
                changed += 1
            continue
        if cell is None:
            cell = PlanVolume(budget_version_id=version.id, cfo_id=r.cfo_id, nomenclature_id=r.nomenclature_id,
                              kind=r.kind, discount_cfo_id=r.discount_cfo_id, month=r.month, quantity=r.quantity)
            db.add(cell)
        else:
            cell.quantity = r.quantity
        changed += 1
    db.flush()
    return changed


def grid(db: Session, version: BudgetVersion, branch: set[int] | None = None) -> dict:
    budget = db.get(Budget, version.budget_id)
    query = select(PlanVolume).where(PlanVolume.budget_version_id == version.id)
    if branch is not None:
        query = query.where(PlanVolume.cfo_id.in_(branch))
    rows: dict[tuple, dict] = {}
    labels = plan.cfo_labels(db, budget.organization_id)
    names = {n.id: n for n in db.scalars(select(Nomenclature).where(Nomenclature.organization_id == budget.organization_id))}
    for v in db.scalars(query):
        key = (v.cfo_id, v.nomenclature_id, v.kind, v.discount_cfo_id)
        row = rows.setdefault(key, {
            "cfo_id": v.cfo_id, "cfo_name": labels.get(v.cfo_id, ""), "nomenclature_id": v.nomenclature_id,
            "name": names[v.nomenclature_id].name, "article": names[v.nomenclature_id].article, "kind": v.kind,
            "kind_title": VOLUME_KINDS[v.kind], "discount_cfo_id": v.discount_cfo_id,
            "discount_name": labels.get(v.discount_cfo_id, "") if v.discount_cfo_id else "", "values": {}})
        row["values"][plan.month_key(budget.year, v.month)] = v.quantity
    order = sorted(rows.values(), key=lambda r: (r["cfo_name"].lower(), r["name"].lower(),
                                                 list(VOLUME_KINDS).index(r["kind"]), r["discount_name"]))
    return {"months": plan._months(budget), "rows": order}


# --- Расчёт ---


@dataclass
class CalcResult:
    cells: int = 0
    total_revenue: Decimal = ZERO
    missing: list[str] = field(default_factory=list)


def calculate(db: Session, version: BudgetVersion, user: User, revenue_article_id: int, guard=None) -> CalcResult:
    """Пересчитать ячейки «из объёмов» версии. Нет цен — ничего не меняет, возвращает список недостающих."""
    budget = db.get(Budget, version.budget_id)
    org = budget.organization_id
    revenue = db.get(Article, revenue_article_id)
    allowed = {a.id for a, _ in plan.plan_articles(db, org, "pl")}
    if revenue is None or revenue.organization_id != org or revenue.pl_section != "revenue" or revenue.id not in allowed:
        raise PlanError("Статья выручки: статья раздела «Выручка» без подстатей, с шаблоном проводки")
    internal_rev = system_article(db, org, "internal_revenue")
    internal_buy = system_article(db, org, "internal_purchase")
    if internal_rev is None or internal_buy is None:
        raise PlanError("Нет системных статей внутренних оборотов")
    cost = system_article(db, org, "cost_of_sales")  # себестоимость по техкарте и закупка (шаг 20.7)
    if cost is None or cost.id not in allowed:
        raise PlanError("Нет системной статьи «Себестоимость по техкарте» с шаблоном проводки")
    links = _links(db, org)
    own_logistics = [c.id for c in db.scalars(select(Cfo).where(Cfo.organization_id == org, Cfo.is_active,
                                                                Cfo.chain_link == "logistics"))]
    items = {n.id: n for n in db.scalars(select(Nomenclature).where(Nomenclature.organization_id == org))}
    cfo_names = {c.id: c.name for c in db.scalars(select(Cfo).where(Cfo.organization_id == org))}
    volumes = list(db.scalars(select(PlanVolume).where(PlanVolume.budget_version_id == version.id)))
    result = CalcResult()
    sums: dict[tuple[int, int, int], Decimal] = {}  # (ЦФО, статья, месяц) → сумма
    missing: set[str] = set()

    def add(cfo_id: int, article_id: int, month: int, amount: Decimal) -> None:
        sums[(cfo_id, article_id, month)] = sums.get((cfo_id, article_id, month), ZERO) + amount

    def price(item: Nomenclature, kind: str, receiver: int, month: int) -> Decimal | None:
        day = dt.date(budget.year, month, 1)
        found = prices.price_on(db, org, item.id, receiver, day, kind)
        if found is None:
            missing.add(f"«{item.name}» — {PRICE_KINDS[kind].lower()} для «{cfo_names.get(receiver, '?')}» "
                        f"на {day:%m.%Y}")
            return None
        return found.net

    def transfer(seller: int, buyer: int, month: int, amount: Decimal) -> None:
        add(seller, internal_rev.id, month, amount)
        add(buyer, internal_buy.id, month, amount)

    def supply(item: Nomenclature, quantity: Decimal, point: int, month: int, logistics: int) -> None:
        """Товар приходит на точку торговли: логистика → точка, для продукции — производство → логистика."""
        if item.route == "assembled":
            parts = routes.components_of(db, item.id)
            if not parts:
                missing.add(f"«{item.name}» — доготовка без состава")
            for part in parts:
                supply(items[part.component_id], quantity * part.quantity, point, month, logistics)
            return
        p = price(item, "logistics", point, month)
        if p is not None:
            transfer(logistics, point, month, round_money(quantity * p))
        if item.route == "purchased":  # логистика закупает у поставщика — себестоимость по цене закупки
            p = price(item, "purchase", logistics, month)
            if p is not None:
                add(logistics, cost.id, month, round_money(quantity * p))
        if item.route == "product":
            if item.producer_cfo_id is None:
                missing.add(f"«{item.name}» — не указано производство")
                return
            p = price(item, "production", logistics, month)
            if p is not None:
                transfer(item.producer_cfo_id, logistics, month, round_money(quantity * p))
            # себестоимость производства — материалы по техкарте × цена закупки (признаётся при передаче на СГП)
            for r in routes.recipe_of(db, item.id):
                p = price(items[r.material_id], "purchase", item.producer_cfo_id, month)
                if p is not None:
                    add(item.producer_cfo_id, cost.id, month, round_money(quantity * r.quantity * p))

    needs_logistics = any(links.get(v.cfo_id) == "trade" for v in volumes)
    if needs_logistics and len(own_logistics) != 1:
        raise PlanError("Логистика: нужно ровно одно ЦФО со звеном «Логистика» (сейчас — "
                        f"{len(own_logistics)})")
    logistics = own_logistics[0] if own_logistics else None
    for v in volumes:
        item = items[v.nomenclature_id]
        if v.kind == "sale":
            p = price(item, "sale", v.cfo_id, v.month)
            if p is not None:
                amount = round_money(v.quantity * p)
                add(v.cfo_id, revenue.id, v.month, amount)
                result.total_revenue += amount
        elif v.kind == "discount":
            p = price(item, "discount", v.discount_cfo_id, v.month)
            if p is not None:
                transfer(v.cfo_id, v.discount_cfo_id, v.month, round_money(v.quantity * p))
        if links.get(v.cfo_id) == "trade":
            supply(item, v.quantity, v.cfo_id, v.month, logistics)
    if missing:
        result.missing = sorted(missing)
        return result
    # прежние ячейки «из объёмов», которых больше нет, — очищаются
    for op in db.scalars(select(Operation).where(Operation.budget_version_id == version.id, Operation.plan_auto,
                                                 Operation.plan_kind == "pl")):
        key = (op.plan_cfo_id, op.article_id, op.op_date.month)
        sums.setdefault(key, ZERO)
    by_cfo: dict[int, list[plan.Cell]] = {}
    for (cfo_id, article_id, month), amount in sums.items():
        by_cfo.setdefault(cfo_id, []).append(plan.Cell(article_id, month, amount or None))
    for cfo_id, cells in by_cfo.items():
        result.cells += plan.set_cells(db, version, "pl", cfo_id, cells, user, guard=guard, auto=True)
    return result


def clear(db: Session, version: BudgetVersion) -> None:
    db.execute(delete(PlanVolume).where(PlanVolume.budget_version_id == version.id))


# --- Excel ---


def _columns(budget: Budget) -> list[Column]:
    return [Column("Точка (ЦФО)", "cfo", True, 30, True, "Код, путь или название ЦФО торговли или дисконта"),
            Column("Артикул", "article", False, 14, True),
            Column("Товар", "name", False, 30, hint="Товар ищется по артикулу, без него — по наименованию"),
            Column("Вид", "kind", False, 14, hint="продажа / на дисконт / утиль; пусто — продажа"),
            Column("Дисконт (ЦФО)", "discount", False, 24, True, "Для «на дисконт»: какой дисконт получает"),
            *[Column(MONTH_TITLES[m - 1], f"m{m}", False, 12, hint="Количество") for m in budget.months]]


def volumes_xlsx(db: Session, version: BudgetVersion, branch: set[int] | None = None) -> bytes:
    budget = db.get(Budget, version.budget_id)
    columns = _columns(budget)
    wb = Workbook()
    ws = wb.active
    ws.title = "План объёмов"
    _sheet_with_header(ws, columns)
    data = grid(db, version, branch)
    for r, row in enumerate(data["rows"], start=2):
        values = [row["cfo_name"], row["article"] or None, row["name"], row["kind_title"].lower(),
                  row["discount_name"] or None,
                  *(row["values"].get(plan.month_key(budget.year, m)) for m in budget.months)]
        for c, value in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=value)
    _help_sheet(wb, columns, [
        "План объёмов: строка — точка, товар и вид (продажа клиентам, на дисконт, утиль); по месяцам — количество.",
        "Пустая ячейка месяца количество не меняет, 0 — удаляет. После загрузки нажмите «Рассчитать план».",
    ])
    return _to_bytes(wb)


@dataclass
class VolumesImportResult:
    rows: int = 0
    changed: int = 0
    errors: list[RowError] = field(default_factory=list)


def import_volumes(db: Session, version: BudgetVersion, raw: bytes, branch: set[int] | None = None) -> VolumesImportResult:
    budget = db.get(Budget, version.budget_id)
    rows = _read_rows(raw, _columns(budget))
    result = VolumesImportResult(rows=len(rows))
    cfos = cfo_lookup(db, budget.organization_id, active_only=False)
    items = list(db.scalars(select(Nomenclature).where(Nomenclature.organization_id == budget.organization_id)))
    by_article = {i.article.strip().lower(): i for i in items if i.article}
    for number, row in rows:
        try:
            with db.begin_nested():
                cfo_id = cfos.find(_text(row.get("cfo")))
                article, name = _text(row.get("article")).lower(), _text(row.get("name")).lower()
                item = by_article.get(article) if article else None
                if item is None:
                    found = [i for i in items if name and i.name.strip().lower() == name]
                    if len(found) != 1:
                        raise RowProblem("Товар не найден (укажите артикул или точное наименование)" if not found
                                         else "Несколько товаров с таким наименованием — укажите артикул")
                    item = found[0]
                kind_text = _text(row.get("kind")).lower()
                kind = KIND_WORDS.get(kind_text, "sale" if not kind_text else None)
                if kind is None:
                    raise RowProblem(f"«Вид»: «{_text(row.get('kind'))}» — продажа, на дисконт или утиль")
                discount = cfos.find(_text(row.get("discount"))) if _text(row.get("discount")) else None
                cells = []
                for m in budget.months:
                    value = row.get(f"m{m}")
                    if value is None or _text(value) == "":
                        continue
                    quantity = Decimal(0) if _text(value) in ("0", "0,0", "0.0") else _decimal(value, MONTH_TITLES[m - 1], 3)
                    cells.append(VolumeIn(cfo_id, item.id, kind, discount, m, quantity))
                try:
                    result.changed += set_volumes(db, version, cells, branch)
                except PlanError as exc:
                    raise RowProblem(str(exc)) from None
        except RowProblem as exc:
            result.errors.append(RowError(number, str(exc)))
    return result
