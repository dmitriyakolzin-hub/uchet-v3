"""Управленческий отчёт по форме организации (docs/TZ.md, раздел 19, «Детали 9.4»).

Строка-статья — сумма по статье с подстатьями (ОПиУ — по начислению, только ДДС — по деньгам) в привычном знаке:
доходы, поступления, расходы и выплаты — положительные. Показатель — формула по кодам строк («3.2 + 1.3»).
Отбор проводок — тот же `ReportFilter.lines` (select_lines), что у ОПиУ и ДДС.
"""

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Account, Article, ReportFormRow
from app.services import reports
from app.services.articles import subtree_ids
from app.services.reports import ZERO, ReportFilter

INCOME_SECTIONS = ("revenue", "other_income")
TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)*)\.?|([()+\-]))")


class FormulaError(ValueError):
    pass


def parse_formula(text: str) -> list[str]:
    """Формула → токены: коды строк, «+», «-», скобки. Проверяет, что формула правильная."""
    text = (text or "").replace("−", "-").strip()
    tokens, pos = [], 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"непонятный символ в формуле: «{text[pos:pos + 10]}»")
        tokens.append(m.group(1) or m.group(2))
        pos = m.end()
    # проверка порядка: значение, затем знак, затем значение…
    depth, expect_value = 0, True
    for t in tokens:
        if t == "(":
            if not expect_value:
                raise FormulaError("перед скобкой нужен знак «+» или «−»")
            depth += 1
        elif t == ")":
            if expect_value or depth == 0:
                raise FormulaError("лишняя закрывающая скобка")
            depth -= 1
        elif t in "+-":
            if expect_value and t == "+":
                raise FormulaError("два знака подряд")
            if not expect_value:
                expect_value = True
                continue
        else:
            if not expect_value:
                raise FormulaError("между кодами нужен знак «+» или «−»")
            expect_value = False
            continue
    if depth or (tokens and expect_value):
        raise FormulaError("формула не закончена")
    return tokens


def evaluate(tokens: list[str], value_of) -> Decimal:
    """Сумма с учётом знаков и скобок; value_of(код) — значение строки."""
    stack: list[tuple[Decimal, int]] = []
    total, sign = ZERO, 1
    for t in tokens:
        if t == "+":
            continue
        elif t == "-":
            sign = -sign  # «a − −b» и «−(…)» тоже считаются
        elif t == "(":
            stack.append((total, sign))
            total, sign = ZERO, 1
        elif t == ")":
            inner = total
            total, sign = stack.pop()
            total += sign * inner
            sign = 1
        else:
            total += sign * value_of(t)
            sign = 1
    return total


@dataclass
class FormRowResult:
    row: ReportFormRow
    level: int
    parent: str | None
    fact: dict[str, Decimal]
    plan: dict[str, Decimal]
    drill: dict | None
    error: str = ""


def _sign(article: Article) -> int:
    """Множитель к «кредит − дебет», чтобы сумма была в привычном знаке: доходы, расходы, поступления, выплаты — плюсом.
    Статья ОПиУ: доход — кредит, расход — дебет; статья только ДДС: поступление — дебет денег, выплата — кредит."""
    if article.is_pl:
        return 1 if article.pl_section in INCOME_SECTIONS else -1
    return -1 if article.cash_flow_direction == "inflow" else 1


def _sums(db: Session, f: ReportFilter, columns) -> tuple[dict, dict]:
    """Суммы по статьям и месяцам: ОПиУ (кредит − дебет по «Статье ОПиУ») и деньги (кредит − дебет по счетам денег)."""
    def collect(lines, item) -> dict[int, dict[str, Decimal]]:
        # месяц и знак — во вложенном запросе, иначе PostgreSQL не узнаёт выражение в GROUP BY
        marked = select(func.to_char(lines.c.op_date, "YYYY-MM").label("month"), item.label("article_id"),
                        case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub).label("signed"),
                        ).where(item.is_not(None)).subquery()
        result: dict[int, dict[str, Decimal]] = {}
        for month, article_id, amount in db.execute(
                select(marked.c.month, marked.c.article_id, func.coalesce(func.sum(marked.c.signed), 0))
                .group_by(marked.c.month, marked.c.article_id)).all():
            values = result.setdefault(article_id, {c.key: ZERO for c in columns})
            values[month] = values.get(month, ZERO) + Decimal(amount)
            values["total"] += Decimal(amount)
        return result

    reformation = list(db.scalars(select(Account.id).where(Account.code == "84")))
    pl_lines = f.lines(date_from=f.date_from, date_to=f.date_to, opening=False,
                       exclude_corr_account_ids=reformation).subquery()
    money = reports.money_account_ids(db)
    cash_lines = f.lines(date_from=f.date_from, date_to=f.date_to, opening=False, account_ids=money,
                         exclude_corr_account_ids=money).subquery()
    return collect(pl_lines, pl_lines.c.pl_item), collect(cash_lines, cash_lines.c.cash_flow_item)


def build(db: Session, f: ReportFilter, plan_pl: ReportFilter | None, plan_cf: ReportFilter | None
          ) -> tuple[list, list[FormRowResult]]:
    """Строки формы с фактом (и планом, если заданы фильтры плана) по месяцам."""
    form = list(db.scalars(select(ReportFormRow).where(ReportFormRow.organization_id == f.organization_id)
                           .order_by(ReportFormRow.position)))
    columns = reports.month_columns(f.date_from, f.date_to)
    keys = [c.key for c in columns]
    zero = lambda: {k: ZERO for k in keys}  # noqa: E731
    fact_pl, fact_cf = _sums(db, f, columns)
    plan_sums = (_sums(db, plan_pl, columns)[0] if plan_pl else {}, _sums(db, plan_cf, columns)[1] if plan_cf else {})
    articles = {a.id: a for a in db.scalars(select(Article).where(Article.organization_id == f.organization_id))}
    money = reports.money_account_ids(db)
    reformation = list(db.scalars(select(Account.id).where(Account.code == "84")))

    def article_values(article: Article, pl: dict, cf: dict, invert: bool) -> dict[str, Decimal]:
        source = pl if article.is_pl else cf
        sign = -_sign(article) if invert else _sign(article)
        values = zero()
        for article_id in subtree_ids(db, article.id):
            for k, v in source.get(article_id, {}).items():
                values[k] += sign * v
        return values

    results: list[FormRowResult] = []
    by_code: dict[str, FormRowResult] = {}
    by_article: dict[int, FormRowResult] = {}  # строки-статьи, уже прошедшие (родитель в файле — выше подстатьи)
    for row in form:
        parent, level = None, 0
        if row.kind == "article" and row.article_id in articles:
            up = articles[row.article_id].parent_id
            while up is not None and up in articles:
                if up in by_article:  # ближайшая статья-предок, которая есть в форме
                    parent, level = f"row:{by_article[up].row.id}", by_article[up].level + 1
                    break
                up = articles[up].parent_id
        result = FormRowResult(row, level, parent, zero(), zero(), None)
        if row.kind == "article":
            article = articles.get(row.article_id)
            if article is None:
                result.error = "статья не найдена в справочнике"
            else:
                result.fact = article_values(article, fact_pl, fact_cf, row.invert)
                result.plan = article_values(article, *plan_sums, row.invert)
                if article.is_pl:  # те же условия, что у суммы: ОПиУ без реформации, деньги без переводов
                    result.drill = {"period": "turnover", "a_tree": [f"pl_item:{article.id}"], "opening": False,
                                    "exclude_corr_ids": reformation}
                else:
                    result.drill = {"period": "turnover", "a_tree": [f"cash_flow_item:{article.id}"], "opening": False,
                                    "account_id": money, "exclude_corr_ids": money}
        results.append(result)
        if row.code:
            by_code[row.code] = result
        if row.kind == "article" and row.article_id:
            by_article[row.article_id] = result

    # показатели: формулы по кодам (показатель может ссылаться на показатель ниже по форме)
    done: set[str] = set()

    def compute(result: FormRowResult, trail: tuple[str, ...]) -> None:
        row = result.row
        if row.code in done or row.kind != "indicator" or not row.formula:
            return
        if row.code in trail:
            raise FormulaError("формула ссылается сама на себя: " + " → ".join((*trail, row.code)))
        tokens = parse_formula(row.formula)
        for t in tokens:
            if t not in "+-()" and t not in by_code:
                raise FormulaError(f"нет строки с кодом {t}")
            if t in by_code:
                compute(by_code[t], (*trail, row.code))
        sign = -1 if row.invert else 1
        for part in ("fact", "plan"):
            values = getattr(result, part)
            for k in keys:
                values[k] = sign * evaluate(tokens, lambda code: getattr(by_code[code], part)[k])
        done.add(row.code)

    for result in results:
        try:
            compute(result, ())
        except FormulaError as exc:
            result.error = str(exc)
            if result.row.code:
                done.add(result.row.code)
    return columns, results


def plan_filters(db: Session, f: ReportFilter, version_id: int | None = None) -> tuple[ReportFilter, ReportFilter]:
    """Фильтры плана: ОПиУ и ДДС берутся из своих утверждённых версий (как в план/факте) или из выбранной."""
    months = [c.key for c in reports.month_columns(f.date_from, f.date_to)[:-1]]
    result = []
    for kind in ("pl", "cf"):
        versions = (reports.version_plan_months(db, version_id, months) if version_id
                    else reports.actual_plan_versions(db, f.organization_id, months, kind))
        result.append(replace(f, with_drafts=False, plan_versions=tuple(sorted(versions.items()))))
    return result[0], result[1]
