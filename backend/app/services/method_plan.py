"""План по методам на месяц — «Управленческий», режим «План по методам» (docs/TZ.md, раздел 31, «Детали 23.2»;
раздел 32, «Детали 24.4»).

Все колонки 1–6 считаются у каждой статьи без подстатей одновременно — это независимые оценки, между собой они
не складываются (пусто — у метода нет данных):
- norm — нормативный: оклады сотрудников, работающих в месяце (у кого статья — статья затрат; статья только ДДС
  с методом «Нормативный» — все сотрудники), плюс суммы «Нормативный» из «Нормативов»; фильтры юрлица и ЦФО;
- avg — средний: факт статьи за N месяцев перед месяцем плана (не позже текущего месяца) / N;
- fact — фактический: регламентные операции, действующие в месяце, плюс сумма «Фактический» из «Нормативов»;
- calc — расчётный: процент из «Нормативов» × ИТОГ базового показателя (статьи или строки формы) в этом же плане;
- cfo / executor — снизу ЦФО / ЦФУ: плановые операции версии без ЦФУ / с ЦФУ.
Метод статьи из справочника — основная колонка (выделяется). «Сверху ИТОГ» выбирает финансовый директор: значение
одной из колонок 1–6 (до фиксации следует за колонкой) или своя сумма; пока не выбран — равен «Бюджету» (все плановые
операции версии за месяц). «Бюджет − ИТОГ» — отклонение. Суммы — в привычном знаке, как в управленческом отчёте.
"""

import datetime as dt
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Account, Article, Cfo, Employee, Operation, PlanItog, ReportFormRow, RoutineRule
from app.services import reports
from app.services.management_report import FormulaError, _sign, evaluate, parse_formula
from app.services.payroll import salary_on
from app.services.plan_methods import base_window
from app.services.plan_norms import effective_methods, norms_on, point_norm, trade_points
from app.services.reports import ReportFilter

ZERO = Decimal("0.00")
METHOD_KEYS = ("norm", "avg", "fact", "calc", "cfo", "executor")
AUTO_ITOG_METHODS = ("calc", "norm")  # ИТОГ сразу по своей колонке, пока не выбран другой (24.5)
COLUMNS = (*METHOD_KEYS, "itog", "budget", "diff")


class MethodPlanError(ValueError):
    pass


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Leaf:
    article: Article
    method: str  # основной метод статьи (из справочника)
    values: dict = field(default_factory=dict)  # метод → значение (None — у метода нет данных)
    notes: dict = field(default_factory=dict)  # метод → пояснение: «30,2 % от 2.1», «6 мес.», «нет норматива»
    # метод → {ЦФО (None — не определён, ЦФО отчёта): сумма} — подсказка и запись в бюджет при фиксации
    by_cfo: dict = field(default_factory=dict)
    cfo: Decimal = ZERO  # плановые операции без ЦФУ
    executor: Decimal = ZERO  # плановые операции с ЦФУ
    has_cfo: bool = False  # есть плановые операции без ЦФУ («снизу ЦФО»)
    has_executor: bool = False  # есть плановые операции с ЦФУ («снизу ЦФУ»)
    stored: object = None  # PlanItog — сохранённый «Сверху ИТОГ»

    @property
    def budget(self) -> Decimal:
        return self.cfo + self.executor

    @property
    def value(self) -> Decimal | None:  # значение основного метода
        return self.values.get(self.method)

    @property
    def note(self) -> str:
        return self.notes.get(self.method, "")

    @property
    def method_value(self) -> Decimal:
        return self.value if self.value is not None else ZERO

    @property
    def itog_auto(self) -> bool:
        """ИТОГ не выбран, а метод статьи «Расчётный» или «Нормативный» — ИТОГ сразу по своей колонке (24.5)."""
        return self.stored is None and self.method in AUTO_ITOG_METHODS

    @property
    def itog_source(self) -> str:
        """budget — не выбран (равен бюджету; после фиксации — зафиксированный бюджет), bottom — не выбран, заполнены
        обе «снизу» колонки — большая из них (24.7), norm…executor — колонка (у расчётных и нормативных — сразу,
        `itog_auto`), manual — своя сумма, fixed — запись до 24.4."""
        if self.stored is None:
            if self.itog_auto:
                return self.method
            return "bottom" if self.has_cfo and self.has_executor else "budget"
        return self.stored.source or ("manual" if self.stored.manual else "fixed")

    @property
    def itog_live(self) -> bool:
        """ИТОГ следует за колонкой метода (выбран или автоматический и ещё не зафиксирован)."""
        if self.stored is None:
            return self.itog_auto
        return self.stored.source in METHOD_KEYS and self.stored.fixed_at is None

    @property
    def itog(self) -> Decimal:
        if self.itog_live:
            return self.values.get(self.itog_source) or ZERO
        if self.stored is None:
            return max(self.cfo, self.executor) if self.itog_source == "bottom" else self.budget
        return self.stored.amount


@dataclass
class RowResult:
    row: ReportFormRow
    level: int
    parent: str | None
    values: dict[str, Decimal | None] = field(default_factory=dict)
    method: str | None = None  # основной метод строки-статьи (если у всех её статей один)
    note: str = ""
    notes: dict = field(default_factory=dict)  # пояснения по колонкам (строка — одна статья без подстатей)
    error: str = ""
    plan_kind: str | None = None  # pl / cf — для окна плановых операций (ввод «снизу», 23.3)
    itog_article_id: int | None = None  # ИТОГ строки можно править (строка — одна статья без подстатей)
    itog_manual: bool = False  # ИТОГ поставлен вручную
    itog_source: str | None = None  # откуда ИТОГ (строка — одна статья): budget / колонка / manual / fixed
    itog_fixed: bool = False  # ИТОГ зафиксирован
    itog_auto: bool = False  # ИТОГ не выбран, взят по методу статьи (расчётный, нормативный)
    breakdown: dict = field(default_factory=dict)  # значение метода по ЦФО (подсказка при наведении, 24.2)


def _month_bounds(month: dt.date) -> tuple[dt.date, dt.date]:
    start = month.replace(day=1)
    return start, (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)


def _branch(db: Session, cfo_id: int | None) -> set[int] | None:
    if cfo_id is None:
        return None
    root = db.get(Cfo, cfo_id)
    return set(db.scalars(select(Cfo.id).where(Cfo.path.op("<@")(root.path)))) if root else set()


def _sums(db: Session, f: ReportFilter, date_from: dt.date, date_to: dt.date) -> tuple[dict, dict]:
    """«Кредит − дебет» по статьям и ЦФО за период: ОПиУ (без ввода остатков и реформации) и деньги (без переводов).
    Ключ — статья, значение — {ЦФО строки (или None): сумма}."""
    def collect(lines, item) -> dict[int, dict]:
        signed = case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub)
        result: dict[int, dict] = {}
        for a, cfo, s in db.execute(select(item, lines.c.cfo, func.coalesce(func.sum(signed), 0))
                                    .where(item.is_not(None)).group_by(item, lines.c.cfo)).all():
            result.setdefault(a, {})[cfo] = Decimal(s)
        return result

    reformation = list(db.scalars(select(Account.id).where(Account.code == "84")))
    pl = f.lines(date_from=date_from, date_to=date_to, opening=False, exclude_corr_account_ids=reformation).subquery()
    money = reports.money_account_ids(db)
    cash = f.lines(date_from=date_from, date_to=date_to, opening=False, account_ids=money,
                   exclude_corr_account_ids=money).subquery()
    return collect(pl, pl.c.pl_item), collect(cash, cash.c.cash_flow_item)


def _plan_cells(db: Session, f: ReportFilter, version_id: int, start: dt.date, end: dt.date) -> tuple[dict, dict]:
    """Плановые операции версии за месяц: статья → [(сумма, есть ли), …] без ЦФУ и с ЦФУ, «кредит − дебет»;
    ОПиУ и ДДС отдельно."""
    plan = replace(f, with_drafts=False, plan_versions=((f"{start:%Y-%m}", version_id),))

    def collect(lines, item) -> dict[int, list]:
        signed = case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub)
        with_executor = Operation.plan_executor_id.is_not(None)
        result: dict[int, list] = {}
        for article_id, has_executor, amount in db.execute(
                select(item, with_executor, func.coalesce(func.sum(signed), 0))
                .join(Operation, Operation.id == lines.c.operation_id)
                .where(item.is_not(None)).group_by(item, with_executor)).all():
            result.setdefault(article_id, [(ZERO, False), (ZERO, False)])[1 if has_executor else 0] = \
                (Decimal(amount), True)
        return result

    money = reports.money_account_ids(db)
    pl = plan.lines(date_from=start, date_to=end).subquery()
    cash = plan.lines(date_from=start, date_to=end, account_ids=money, exclude_corr_account_ids=money).subquery()
    return collect(pl, pl.c.pl_item), collect(cash, cash.c.cash_flow_item)


def _add(bucket: dict, key, amount: Decimal) -> None:
    bucket[key] = bucket.get(key, ZERO) + amount


def _point_amounts(db: Session, f: ReportFilter, kind: str, branch: set[int] | None, start: dt.date) -> dict:
    """Суммы нормативов «на каждую точку» (24.6): статья → {точка: сумма}. Свой норматив точки (без отметки) —
    в обычном порядке, здесь не считается."""
    if f.executor_ids is not None:
        return {}
    norms = [n for n in norms_on(db, f.organization_id, kind, start)]
    by_article: dict[int, list] = {}
    for n in norms:
        by_article.setdefault(n.article_id, []).append(n)
    points = {p: path for p, path in trade_points(db, f.organization_id).items() if branch is None or p in branch}
    result: dict[int, dict] = {}
    for article_id, items in by_article.items():
        if not any(n.per_point for n in items):
            continue
        for point, path in points.items():
            norm = point_norm(items, path)
            if norm is not None and norm.per_point:
                _add(result.setdefault(article_id, {}), point, norm.amount)
    return result


def _norm_values(db: Session, f: ReportFilter, leaves: dict[int, Leaf], branch: set[int] | None,
                 start: dt.date, end: dt.date) -> None:
    query = select(Employee).where(Employee.organization_id == f.organization_id, Employee.is_active,
                                   or_(Employee.hired_on.is_(None), Employee.hired_on <= end),
                                   or_(Employee.fired_on.is_(None), Employee.fired_on >= start))
    if f.legal_entity_id is not None:
        query = query.where(Employee.legal_entity_id == f.legal_entity_id)
    employees = [] if f.executor_ids is not None else list(db.scalars(query))  # у сотрудника нет ЦФУ
    if branch is not None:
        employees = [e for e in employees if e.cfo_id in branch]
    salaries = {e.id: salary_on(db, e.id, end) or ZERO for e in employees}
    by_item: dict[int, list] = {}
    for e in employees:
        if e.pl_item_id is not None:
            by_item.setdefault(e.pl_item_id, []).append(e)
    # суммы «Нормативный» из «Нормативов» — к окладам (24.2); с фильтром ЦФО — только ЦФО ветки
    manual: dict[int, dict] = {}
    if f.executor_ids is None:
        for norm in norms_on(db, f.organization_id, "norm", start):
            if not norm.per_point and (branch is None or norm.cfo_id in branch):
                _add(manual.setdefault(norm.article_id, {}), norm.cfo_id, norm.amount)
    for article_id, bucket in _point_amounts(db, f, "norm", branch, start).items():  # на каждую точку (24.6)
        for point, amount in bucket.items():
            _add(manual.setdefault(article_id, {}), point, amount)
    for leaf in leaves.values():
        own = leaf.method == "norm"
        if leaf.article.is_pl:
            chosen = by_item.get(leaf.article.id, [])
            parts = [f"оклады: {len(chosen)} сотр."] if chosen or own else []
        elif own:  # статья только ДДС с нормативным методом — все оклады
            chosen = employees
            parts = [f"оклады всех сотрудников: {len(chosen)}"]
        else:
            chosen, parts = [], []
        if not chosen and not manual.get(leaf.article.id) and not own:
            continue
        bucket: dict = {}
        for e in chosen:
            _add(bucket, e.cfo_id, salaries[e.id])
        for cfo, amount in manual.get(leaf.article.id, {}).items():
            _add(bucket, cfo, amount)
        if manual.get(leaf.article.id):
            parts.append("норматив")
        leaf.by_cfo["norm"] = bucket
        leaf.notes["norm"] = " + ".join(parts)
        leaf.values["norm"] = sum(bucket.values(), ZERO)


def _avg_values(db: Session, f: ReportFilter, leaves: dict[int, Leaf], month: dt.date, today: dt.date) -> None:
    by_months: dict[int, list[Leaf]] = {}
    for leaf in leaves.values():
        by_months.setdefault(leaf.article.plan_avg_months or 6, []).append(leaf)
    fact = replace(f, with_drafts=False, plan_versions=None)
    for months, group in by_months.items():
        date_from, date_to = base_window(month.year, month.month, today, months)
        pl, cf = _sums(db, fact, date_from, date_to)
        for leaf in group:
            source = (pl if leaf.article.is_pl else cf).get(leaf.article.id)
            if not source and leaf.method != "avg":
                continue
            sign = _sign(leaf.article)
            leaf.by_cfo["avg"] = {cfo: _round(sign * amount / months) for cfo, amount in (source or {}).items()}
            leaf.values["avg"] = sum(leaf.by_cfo["avg"].values(), ZERO)
            leaf.notes["avg"] = f"{months} мес.: {date_from:%m.%Y}–{date_to:%m.%Y}" + ("" if source else ", факта нет")


def _fact_values(db: Session, f: ReportFilter, leaves: dict[int, Leaf], branch: set[int] | None,
                 start: dt.date) -> None:
    amounts: dict[int, Decimal] = {}
    by_cfo: dict[int, dict] = {}
    query = select(RoutineRule).where(RoutineRule.organization_id == f.organization_id, RoutineRule.is_active,
                                      RoutineRule.valid_from <= start,
                                      or_(RoutineRule.valid_to.is_(None), RoutineRule.valid_to >= start))
    if f.legal_entity_id is not None:
        query = query.where(RoutineRule.legal_entity_id == f.legal_entity_id)
    for rule in db.scalars(query):
        for posting in (rule.template or {}).get("postings", []):
            for side in ("debit", "credit"):
                analytics = posting.get(side) or {}
                for kind in ("pl_item", "cash_flow_item"):
                    article_id = analytics.get(kind)
                    if article_id not in leaves or (kind == "pl_item") != leaves[article_id].article.is_pl:
                        continue
                    if branch is not None and analytics.get("cfo") not in branch:
                        continue
                    if f.executor_ids is not None and analytics.get("executor") not in f.executor_ids:
                        continue
                    amount = Decimal(str(posting.get("amount") or 0))
                    amounts[article_id] = amounts.get(article_id, ZERO) + amount
                    _add(by_cfo.setdefault(article_id, {}), analytics.get("cfo"), amount)
    manual: dict[int, Decimal] = {}
    if f.executor_ids is None:
        for norm in norms_on(db, f.organization_id, "fact", start):
            # без фильтра ЦФО — все суммы; с фильтром — суммы ЦФО ветки (сумма «на все ЦФО» не делится)
            if not norm.per_point and norm.article_id in leaves and (branch is None or norm.cfo_id in branch):
                manual[norm.article_id] = manual.get(norm.article_id, ZERO) + norm.amount
                _add(by_cfo.setdefault(norm.article_id, {}), norm.cfo_id, norm.amount)
    for article_id, bucket in _point_amounts(db, f, "fact", branch, start).items():  # на каждую точку (24.6)
        if article_id in leaves:
            for point, amount in bucket.items():
                manual[article_id] = manual.get(article_id, ZERO) + amount
                _add(by_cfo.setdefault(article_id, {}), point, amount)
    for article_id, leaf in leaves.items():
        if article_id not in by_cfo and leaf.method != "fact":
            continue
        regular, own = amounts.get(article_id, ZERO), manual.get(article_id, ZERO)
        leaf.values["fact"] = regular + own
        leaf.by_cfo["fact"] = by_cfo.get(article_id, {})
        parts = []
        if regular:
            parts.append("регламентные")
        if own:
            parts.append("вручную")
        leaf.notes["fact"] = " + ".join(parts) or "нет регламентных операций и суммы"


def itog_scope_ok(f: ReportFilter) -> bool:
    """«Сверху ИТОГ» хранится по ЦФО отчёта; с фильтром юрлица или ЦФУ он не показывается и не меняется."""
    return f.legal_entity_id is None and f.executor_ids is None


@dataclass
class Computed:
    f: ReportFilter
    form: list
    articles: dict
    leaves: dict
    subtree: object
    # статья → наличие плановых операций (без ЦФУ, с ЦФУ) — для правил фиксации (24.4)
    has_cells: dict = field(default_factory=dict)


def compute(db: Session, f: ReportFilter, version_id: int, month: dt.date, today: dt.date | None = None) -> Computed:
    """Значения всех методов статей без подстатей за месяц (с сохранённым «Сверху ИТОГ»)."""
    today = today or dt.date.today()
    start, end = _month_bounds(month)
    f = replace(f, date_from=start, date_to=end)
    form = list(db.scalars(select(ReportFormRow).where(ReportFormRow.organization_id == f.organization_id)
                           .order_by(ReportFormRow.position)))
    items = list(db.scalars(select(Article).where(Article.organization_id == f.organization_id)))
    articles = {a.id: a for a in items}
    children: dict[int, list[int]] = {}
    for a in items:
        if a.parent_id is not None:
            children.setdefault(a.parent_id, []).append(a.id)
    methods = effective_methods(items)
    leaves = {a.id: Leaf(a, methods[a.id]) for a in items if a.id not in children}
    branch = _branch(db, f.cfo_id)

    def subtree(article_id: int) -> list[int]:
        result, stack = [], [article_id]
        while stack:
            current = stack.pop()
            if current in leaves:
                result.append(current)
            stack.extend(children.get(current, []))
        return result

    # снизу ЦФО / ЦФУ и бюджет — плановые операции версии
    cells_pl, cells_cf = _plan_cells(db, f, version_id, start, end)
    has_cells: dict[int, tuple[bool, bool]] = {}
    for leaf in leaves.values():
        cells = (cells_pl if leaf.article.is_pl else cells_cf).get(leaf.article.id)
        if cells:
            sign = _sign(leaf.article)
            leaf.cfo, leaf.executor = sign * cells[0][0], sign * cells[1][0]
            has_cells[leaf.article.id] = (cells[0][1], cells[1][1])
            leaf.has_cfo, leaf.has_executor = cells[0][1], cells[1][1]
        present = has_cells.get(leaf.article.id, (False, False))
        for key, amount, exists in (("cfo", leaf.cfo, present[0]), ("executor", leaf.executor, present[1])):
            if exists or leaf.method == key:
                leaf.values[key] = amount
    _norm_values(db, f, leaves, branch, start, end)
    _avg_values(db, f, leaves, start, today)
    _fact_values(db, f, leaves, branch, start)
    if itog_scope_ok(f):  # сохранённый «Сверху ИТОГ» — до расчётного: база берётся по ИТОГ
        for stored in db.scalars(select(PlanItog).where(
                PlanItog.version_id == version_id, PlanItog.month == start.month,
                PlanItog.cfo_id.is_(None) if f.cfo_id is None else PlanItog.cfo_id == f.cfo_id)):
            if stored.article_id in leaves:
                leaves[stored.article_id].stored = stored

    # расчётный — от ИТОГ базового показателя этого же плана (зависимости — рекурсией, с проверкой цикла)
    calc_norms: dict[int, object] = {}
    all_calc = norms_on(db, f.organization_id, "calc", start)
    for norm in all_calc:
        if norm.per_point:
            continue
        current = calc_norms.get(norm.article_id)
        if norm.cfo_id == f.cfo_id or (current is None and norm.cfo_id is None):
            calc_norms[norm.article_id] = norm
    # «на каждую точку» (24.6): процент от базы каждой точки торговли ветки отчёта; свой норматив точки — вместо общего
    point_calc: dict[int, dict[int, object]] = {}  # статья → {точка: норматив}
    if f.executor_ids is None:
        calc_by_article: dict[int, list] = {}
        for norm in all_calc:
            calc_by_article.setdefault(norm.article_id, []).append(norm)
        points = {p: path for p, path in trade_points(db, f.organization_id).items() if branch is None or p in branch}
        for article_id, items in calc_by_article.items():
            if article_id not in leaves or not any(n.per_point for n in items):
                continue
            chosen = {p: n for p, path in points.items() if (n := point_norm(items, path)) is not None}
            if any(n.per_point for n in chosen.values()):
                point_calc[article_id] = chosen
                calc_norms.setdefault(article_id, next(iter(chosen.values())))
    plan_pl = plan_cf = None  # план версии по ЦФО — база точки

    def point_budget(article_id: int, point: int) -> Decimal:
        nonlocal plan_pl, plan_cf
        if plan_pl is None:
            plan_pl, plan_cf = _sums(db, replace(f, with_drafts=False, plan_versions=((f"{start:%Y-%m}", version_id),)),
                                     start, end)
        article = articles[article_id]
        source = plan_pl if article.is_pl else plan_cf
        return _sign(article) * source.get(article_id, {}).get(point, ZERO)

    point_done: dict[tuple[int, int], Decimal] = {}

    def point_article(article_id: int, point: int, trail: tuple) -> Decimal:
        return sum((point_leaf(x, point, trail) for x in subtree(article_id)), ZERO)

    def point_leaf(article_id: int, point: int, trail: tuple) -> Decimal:
        """База точки: расчётная по точкам статья — её значение у точки, иначе — бюджет точки."""
        if article_id in point_calc:
            return point_value(article_id, point, trail)
        return point_budget(article_id, point)

    def point_row(code: str, point: int, trail: tuple) -> Decimal:
        row = by_code.get(code)
        if row is None:
            raise MethodPlanError(f"нет строки с кодом {code}")
        if ("row", code, point) in trail:
            raise MethodPlanError("база ссылается сама на себя")
        trail = (*trail, ("row", code, point))
        sign = -1 if row.invert else 1
        if row.kind == "article" and row.article_id:
            return sign * point_article(row.article_id, point, trail)
        if row.kind == "indicator" and row.formula:
            return sign * evaluate(parse_formula(row.formula), lambda c: point_row(c, point, trail))
        return ZERO

    def point_value(article_id: int, point: int, trail: tuple) -> Decimal:
        key = (article_id, point)
        if key in point_done:
            return point_done[key]
        if ("art", article_id, point) in trail:
            raise MethodPlanError("база ссылается сама на себя")
        norm = point_calc[article_id].get(point)
        if norm is None:
            point_done[key] = ZERO
            return ZERO
        trail = (*trail, ("art", article_id, point))
        if norm.base_article_id is not None:
            base = point_article(norm.base_article_id, point, trail) if norm.base_article_id in articles else ZERO
        else:
            base = point_row(norm.base_form_code, point, trail)
        point_done[key] = _round(base * norm.percent / 100)
        return point_done[key]
    by_code = {r.code: r for r in form if r.code}
    done: set[int] = set()

    def article_itog(article_id: int, trail: tuple) -> Decimal:
        """База расчётного: у расчётной статьи — её колонка 4, иначе — ИТОГ (колонка 7) (24.5)."""
        return sum((base_of(x, trail) for x in subtree(article_id)), ZERO)

    def base_of(article_id: int, trail: tuple) -> Decimal:
        if article_id in calc_norms:
            calc_value(article_id, trail)
            return leaves[article_id].values.get("calc") or ZERO
        return leaf_itog(article_id, trail)

    def row_itog(code: str, trail: tuple) -> Decimal:
        row = by_code.get(code)
        if row is None:
            raise MethodPlanError(f"нет строки с кодом {code}")
        if ("row", code) in trail:
            raise MethodPlanError("база ссылается сама на себя")
        trail = (*trail, ("row", code))
        sign = -1 if row.invert else 1
        if row.kind == "article" and row.article_id:
            return sign * article_itog(row.article_id, trail)
        if row.kind == "indicator" and row.formula:
            return sign * evaluate(parse_formula(row.formula), lambda c: row_itog(c, trail))
        return ZERO

    def leaf_itog(article_id: int, trail: tuple) -> Decimal:
        leaf = leaves[article_id]
        if leaf.itog_live and leaf.itog_source == "calc":
            calc_value(article_id, trail)
        return leaf.itog

    def calc_value(article_id: int, trail: tuple) -> None:
        if article_id in done:
            return
        leaf = leaves[article_id]
        if ("art", article_id) in trail:
            raise MethodPlanError("база ссылается сама на себя")
        if article_id in point_calc:  # по точкам торговли (24.6)
            values = {p: point_value(article_id, p, trail) for p in point_calc[article_id]}
            done.add(article_id)
            total = sum(values.values(), ZERO)
            leaf.values["calc"], leaf.by_cfo["calc"] = total, {p: v for p, v in values.items() if v}
            percents = {format(n.percent.normalize(), "f").replace(".", ",") for n in point_calc[article_id].values()}
            leaf.notes["calc"] = (f"{' / '.join(sorted(percents))} % по точкам торговли: {len(values)}"
                                  + ("" if total else " — база точек пустая: нет плана по базе у точек"))
            return
        norm = calc_norms.get(article_id)
        if norm is None:
            done.add(article_id)
            if leaf.method == "calc":
                leaf.values["calc"], leaf.notes["calc"] = ZERO, "нет норматива"
            return
        trail = (*trail, ("art", article_id))
        if norm.base_article_id is not None:
            base = article_itog(norm.base_article_id, trail)
            title = articles[norm.base_article_id].code or articles[norm.base_article_id].name \
                if norm.base_article_id in articles else "?"
        else:
            base = row_itog(norm.base_form_code, trail)
            title = norm.base_form_code
        done.add(article_id)
        value = _round(base * norm.percent / 100)
        leaf.values["calc"], leaf.by_cfo["calc"] = value, {None: value}
        base_text = f"{base:,.2f}".replace(",", " ").replace(".", ",")
        note = f"{format(norm.percent.normalize(), 'f').replace('.', ',')} % от {title} (база {base_text})"
        if not base:
            note += " — база пустая: нет плана по базовому показателю в этом месяце и версии"
        leaf.notes["calc"] = note

    for article_id, leaf in leaves.items():
        if article_id in calc_norms or leaf.method == "calc":
            try:
                calc_value(article_id, ())
            except (MethodPlanError, FormulaError) as exc:
                done.add(article_id)
                leaf.values["calc"], leaf.notes["calc"] = ZERO, f"ошибка: {exc}"
    return Computed(f, form, articles, leaves, subtree, has_cells)


def build(db: Session, f: ReportFilter, version_id: int, month: dt.date, today: dt.date | None = None
          ) -> list[RowResult]:
    """Строки управленческой формы с колонками методов, ИТОГ, бюджет, бюджет − ИТОГ за месяц."""
    c = compute(db, f, version_id, month, today)
    form, articles, leaves, subtree = c.form, c.articles, c.leaves, c.subtree

    # строки формы
    results: list[RowResult] = []
    by_article: dict[int, RowResult] = {}
    by_row_code: dict[str, RowResult] = {}
    for row in form:
        parent, level = None, 0
        if row.kind == "article" and row.article_id in articles:
            up = articles[row.article_id].parent_id
            while up is not None and up in articles:
                if up in by_article:
                    parent, level = f"row:{by_article[up].row.id}", by_article[up].level + 1
                    break
                up = articles[up].parent_id
        result = RowResult(row, level, parent)
        if row.kind == "article":
            if row.article_id not in articles:
                result.error = "статья не найдена в справочнике"
            else:
                sign = -1 if row.invert else 1
                group = [leaves[x] for x in subtree(row.article_id)]
                values: dict[str, Decimal | None] = {k: None for k in COLUMNS}
                for leaf in group:  # каждая колонка — сумма своей колонки подстатей
                    for key in METHOD_KEYS:
                        if leaf.values.get(key) is not None:
                            values[key] = (values[key] or ZERO) + sign * leaf.values[key]
                values["itog"] = sign * sum((x.itog for x in group), ZERO)
                values["budget"] = sign * sum((x.budget for x in group), ZERO)
                result.values = values
                result.plan_kind = "pl" if articles[row.article_id].is_pl else "cf"
                kinds = {x.method for x in group}
                result.method = kinds.pop() if len(kinds) == 1 else None
                if len(group) == 1:
                    result.note, result.notes = group[0].note, dict(group[0].notes)
                    result.itog_source, result.itog_auto = group[0].itog_source, group[0].itog_auto
                    result.itog_fixed = group[0].stored is not None and group[0].stored.fixed_at is not None
                    if group[0].article.id == row.article_id:
                        result.itog_article_id = row.article_id
                result.itog_manual = any(x.itog_source == "manual" for x in group)
                for leaf in group:
                    for method in ("norm", "avg", "fact", "calc"):
                        for cfo, amount in leaf.by_cfo.get(method, {}).items():
                            _add(result.breakdown, (method, cfo), sign * amount)
                by_article[row.article_id] = result
        results.append(result)
        if row.code:
            by_row_code[row.code] = result

    def indicator(result: RowResult, key: str, trail: tuple) -> Decimal | None:
        row = result.row
        if row.kind != "indicator":
            return result.values.get(key)
        if row.code in trail:
            raise FormulaError("формула ссылается сама на себя")
        if not row.formula:
            return None
        tokens = parse_formula(row.formula)
        for t in tokens:
            if t not in "+-()" and t not in by_row_code:
                raise FormulaError(f"нет строки с кодом {t}")
        found = False

        def value(code: str) -> Decimal:
            nonlocal found
            v = indicator(by_row_code[code], key, (*trail, row.code))
            found = found or v is not None
            return v or ZERO

        sign = -1 if row.invert else 1
        total = sign * evaluate(tokens, value)
        return total if found or key in ("itog", "budget") else None

    for result in results:
        if result.row.kind == "indicator" and result.row.formula:
            try:
                result.values = {k: None for k in COLUMNS}
                for key in (*METHOD_KEYS, "itog", "budget"):
                    result.values[key] = indicator(result, key, ())
            except FormulaError as exc:
                result.values, result.error = {}, str(exc)
        if result.values.get("itog") is not None and result.values.get("budget") is not None:
            result.values["diff"] = result.values["budget"] - result.values["itog"]
    return results
