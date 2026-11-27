"""План по методам на месяц — «Управленческий», режим «План по методам» (docs/TZ.md, раздел 31, «Детали 23.2»).

У каждой статьи без подстатей считается значение её метода (решение владельца 1 а — только своя колонка):
- norm — нормативный: оклады сотрудников, работающих в месяце (статья ОПиУ — у кого она статья затрат; статья только
  ДДС — все сотрудники), с фильтром юрлица и ЦФО;
- avg — средний: факт статьи за N месяцев перед месяцем плана (не позже текущего месяца) / N;
- fact — фактический: регламентные операции, действующие в месяце, плюс сумма «Фактический» из «Нормативов»;
- calc — расчётный: процент из «Нормативов» × ИТОГ базового показателя (статьи или строки формы) в этом же плане;
- cfo / executor — снизу ЦФО / ЦФУ: плановые операции версии без ЦФУ / с ЦФУ;
- top — сверху ИТОГ: только колонка ИТОГ.
ИТОГ по умолчанию равен колонке метода (фиксация — шаг 23.4). «Бюджет» — все плановые операции версии за месяц
(по ЦФО), «Бюджет − ИТОГ» — отклонение. Суммы — в привычном знаке, как в управленческом отчёте.
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
from app.services.plan_norms import effective_methods, norms_on
from app.services.reports import ReportFilter

ZERO = Decimal("0.00")
METHOD_KEYS = ("norm", "avg", "fact", "calc", "cfo", "executor")
COLUMNS = (*METHOD_KEYS, "itog", "budget", "diff")


class MethodPlanError(ValueError):
    pass


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Leaf:
    article: Article
    method: str
    value: Decimal | None = None  # значение своего метода
    note: str = ""  # пояснение: «30,2 % от 2.1», «6 мес.», «нет норматива»
    cfo: Decimal = ZERO  # плановые операции без ЦФУ
    executor: Decimal = ZERO  # плановые операции с ЦФУ
    # значение метода по ЦФО (для записи в бюджет при фиксации, 23.4): None — ЦФО не определён (ЦФО отчёта)
    by_cfo: dict = field(default_factory=dict)
    stored: object = None  # PlanItog — сохранённый «Сверху ИТОГ»

    @property
    def budget(self) -> Decimal:
        return self.cfo + self.executor

    @property
    def method_value(self) -> Decimal:
        return self.value if self.value is not None else ZERO

    @property
    def itog(self) -> Decimal:
        return self.stored.amount if self.stored is not None else self.method_value


@dataclass
class RowResult:
    row: ReportFormRow
    level: int
    parent: str | None
    values: dict[str, Decimal | None] = field(default_factory=dict)
    method: str | None = None  # метод строки-статьи (если у всех её статей один)
    note: str = ""
    error: str = ""
    plan_kind: str | None = None  # pl / cf — для окна плановых операций (ввод «снизу», 23.3)
    itog_article_id: int | None = None  # ИТОГ строки можно править (строка — одна статья без подстатей)
    itog_manual: bool = False  # ИТОГ поставлен вручную


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
    """Плановые операции версии за месяц: статья → (без ЦФУ, с ЦФУ), «кредит − дебет»; ОПиУ и ДДС отдельно."""
    plan = replace(f, with_drafts=False, plan_versions=((f"{start:%Y-%m}", version_id),))

    def collect(lines, item) -> dict[int, list[Decimal]]:
        signed = case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub)
        with_executor = Operation.plan_executor_id.is_not(None)
        result: dict[int, list[Decimal]] = {}
        for article_id, has_executor, amount in db.execute(
                select(item, with_executor, func.coalesce(func.sum(signed), 0))
                .join(Operation, Operation.id == lines.c.operation_id)
                .where(item.is_not(None)).group_by(item, with_executor)).all():
            result.setdefault(article_id, [ZERO, ZERO])[1 if has_executor else 0] += Decimal(amount)
        return result

    money = reports.money_account_ids(db)
    pl = plan.lines(date_from=start, date_to=end).subquery()
    cash = plan.lines(date_from=start, date_to=end, account_ids=money, exclude_corr_account_ids=money).subquery()
    return collect(pl, pl.c.pl_item), collect(cash, cash.c.cash_flow_item)


def _norm_values(db: Session, f: ReportFilter, leaves: dict[int, Leaf], branch: set[int] | None,
                 start: dt.date, end: dt.date) -> None:
    targets = [x for x in leaves.values() if x.method == "norm"]
    if not targets:
        return
    query = select(Employee).where(Employee.organization_id == f.organization_id, Employee.is_active,
                                   or_(Employee.hired_on.is_(None), Employee.hired_on <= end),
                                   or_(Employee.fired_on.is_(None), Employee.fired_on >= start))
    if f.legal_entity_id is not None:
        query = query.where(Employee.legal_entity_id == f.legal_entity_id)
    employees = [] if f.executor_ids is not None else list(db.scalars(query))  # у сотрудника нет ЦФУ
    if branch is not None:
        employees = [e for e in employees if e.cfo_id in branch]
    salaries = {e.id: salary_on(db, e.id, end) or ZERO for e in employees}
    for leaf in targets:
        if leaf.article.is_pl:
            chosen = [e for e in employees if e.pl_item_id == leaf.article.id]
            leaf.note = f"оклады: {len(chosen)} сотр."
        else:
            chosen = employees
            leaf.note = f"оклады всех сотрудников: {len(chosen)}"
        for e in chosen:
            leaf.by_cfo[e.cfo_id] = leaf.by_cfo.get(e.cfo_id, ZERO) + salaries[e.id]
        leaf.value = sum((salaries[e.id] for e in chosen), ZERO)


def _avg_values(db: Session, f: ReportFilter, leaves: dict[int, Leaf], month: dt.date, today: dt.date) -> None:
    by_months: dict[int, list[Leaf]] = {}
    for leaf in leaves.values():
        if leaf.method == "avg":
            by_months.setdefault(leaf.article.plan_avg_months or 6, []).append(leaf)
    fact = replace(f, with_drafts=False, plan_versions=None)
    for months, group in by_months.items():
        date_from, date_to = base_window(month.year, month.month, today, months)
        pl, cf = _sums(db, fact, date_from, date_to)
        for leaf in group:
            source = pl if leaf.article.is_pl else cf
            sign = _sign(leaf.article)
            leaf.by_cfo = {cfo: _round(sign * amount / months)
                           for cfo, amount in source.get(leaf.article.id, {}).items()}
            leaf.value = sum(leaf.by_cfo.values(), ZERO)
            leaf.note = f"{months} мес.: {date_from:%m.%Y}–{date_to:%m.%Y}"


def _fact_values(db: Session, f: ReportFilter, leaves: dict[int, Leaf], branch: set[int] | None,
                 start: dt.date) -> None:
    targets = {x.article.id: x for x in leaves.values() if x.method == "fact"}
    if not targets:
        return
    amounts = {a: ZERO for a in targets}
    by_cfo: dict[int, dict] = {a: {} for a in targets}
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
                    if article_id not in targets or (kind == "pl_item") != targets[article_id].article.is_pl:
                        continue
                    if branch is not None and analytics.get("cfo") not in branch:
                        continue
                    if f.executor_ids is not None and analytics.get("executor") not in f.executor_ids:
                        continue
                    amount = Decimal(str(posting.get("amount") or 0))
                    amounts[article_id] += amount
                    cfo = analytics.get("cfo")
                    by_cfo[article_id][cfo] = by_cfo[article_id].get(cfo, ZERO) + amount
    manual = {a: ZERO for a in targets}
    if f.executor_ids is None:
        for norm in norms_on(db, f.organization_id, "fact", start):
            # без фильтра ЦФО — все суммы; с фильтром — суммы ЦФО ветки (сумма «на все ЦФО» не делится)
            if norm.article_id in targets and (branch is None or norm.cfo_id in branch):
                manual[norm.article_id] += norm.amount
                by_cfo[norm.article_id][norm.cfo_id] = by_cfo[norm.article_id].get(norm.cfo_id, ZERO) + norm.amount
    for article_id, leaf in targets.items():
        leaf.value = amounts[article_id] + manual[article_id]
        leaf.by_cfo = by_cfo[article_id]
        parts = []
        if amounts[article_id]:
            parts.append("регламентные")
        if manual[article_id]:
            parts.append("вручную")
        leaf.note = " + ".join(parts) or "нет регламентных операций и суммы"


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


def compute(db: Session, f: ReportFilter, version_id: int, month: dt.date, today: dt.date | None = None) -> Computed:
    """Значения методов статей без подстатей за месяц (с сохранённым «Сверху ИТОГ»)."""
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
    for leaf in leaves.values():
        cells = (cells_pl if leaf.article.is_pl else cells_cf).get(leaf.article.id)
        if cells:
            sign = _sign(leaf.article)
            leaf.cfo, leaf.executor = sign * cells[0], sign * cells[1]
        if leaf.method == "cfo":
            leaf.value = leaf.cfo
        elif leaf.method == "executor":
            leaf.value = leaf.executor
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
    for norm in norms_on(db, f.organization_id, "calc", start):
        current = calc_norms.get(norm.article_id)
        if norm.cfo_id == f.cfo_id or (current is None and norm.cfo_id is None):
            calc_norms[norm.article_id] = norm
    by_code = {r.code: r for r in form if r.code}

    def article_itog(article_id: int, trail: tuple) -> Decimal:
        return sum((leaf_itog(x, trail) for x in subtree(article_id)), ZERO)

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
        if leaf.method != "calc" or leaf.value is not None:
            return leaf.itog
        if ("art", article_id) in trail:
            raise MethodPlanError("база ссылается сама на себя")
        norm = calc_norms.get(article_id)
        if norm is None:
            leaf.value, leaf.note = ZERO, "нет норматива"
            return ZERO
        trail = (*trail, ("art", article_id))
        if norm.base_article_id is not None:
            base = article_itog(norm.base_article_id, trail)
            title = articles[norm.base_article_id].code or articles[norm.base_article_id].name \
                if norm.base_article_id in articles else "?"
        else:
            base = row_itog(norm.base_form_code, trail)
            title = norm.base_form_code
        leaf.value = _round(base * norm.percent / 100)
        leaf.by_cfo = {None: leaf.value}
        leaf.note = f"{format(norm.percent.normalize(), 'f').replace('.', ',')} % от {title}"
        return leaf.itog

    for article_id, leaf in leaves.items():
        if leaf.method == "calc" and leaf.value is None:
            try:
                leaf_itog(article_id, ())
            except (MethodPlanError, FormulaError) as exc:
                leaf.value, leaf.note = ZERO, f"ошибка: {exc}"
    return Computed(f, form, articles, leaves, subtree)


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
                for leaf in group:
                    if leaf.method in METHOD_KEYS:
                        values[leaf.method] = (values[leaf.method] or ZERO) + sign * leaf.method_value
                values["itog"] = sign * sum((x.itog for x in group), ZERO)
                values["budget"] = sign * sum((x.budget for x in group), ZERO)
                result.values = values
                result.plan_kind = "pl" if articles[row.article_id].is_pl else "cf"
                kinds = {x.method for x in group}
                result.method = kinds.pop() if len(kinds) == 1 else None
                if len(group) == 1:
                    result.note = group[0].note
                    if group[0].article.id == row.article_id:
                        result.itog_article_id = row.article_id
                result.itog_manual = any(x.stored is not None and x.stored.manual for x in group)
                by_article[row.article_id] = result
        results.append(result)
        if row.code:
            by_row_code[row.code] = result

    def indicator(result: RowResult, key: str, trail: tuple) -> Decimal:
        row = result.row
        if row.kind != "indicator":
            return result.values.get(key) or ZERO
        if row.code in trail:
            raise FormulaError("формула ссылается сама на себя")
        if not row.formula:
            return ZERO
        tokens = parse_formula(row.formula)
        for t in tokens:
            if t not in "+-()" and t not in by_row_code:
                raise FormulaError(f"нет строки с кодом {t}")
        sign = -1 if row.invert else 1
        return sign * evaluate(tokens, lambda code: indicator(by_row_code[code], key, (*trail, row.code)))

    for result in results:
        if result.row.kind == "indicator" and result.row.formula:
            try:
                result.values = {k: None for k in COLUMNS}
                for key in ("itog", "budget"):
                    result.values[key] = indicator(result, key, ())
            except FormulaError as exc:
                result.values, result.error = {}, str(exc)
        if result.values.get("itog") is not None and result.values.get("budget") is not None:
            result.values["diff"] = result.values["budget"] - result.values["itog"]
    return results
