"""Методы планирования (docs/TZ.md, раздел 22): сверху вниз (12.3) и ретроспективный (12.4).

Ретроспективный: пустая ячейка плана = среднее за N (1, 3, 6, 12) месяцев перед её месяцем; прошедшие месяцы
базы — факт, текущий и будущие месяцы этого бюджета — план; месяцы без факта и вне бюджета в среднее не входят;
заполненные ячейки не меняются.

Сверху вниз: цель вышестоящего ЦФО по статье ОПиУ на месяц делится между самыми нижними ЦФО его ветки
пропорционально их факту по этой статье за 6 месяцев перед месяцем плана (месяц плана в будущем — 6 месяцев
до текущего); факта нет — поровну. Доли — до копейки, остаток — самой крупной доле. Нижние суммы потом
правятся вручную; цель хранится для сверки «цель / по низу».
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, null, select
from sqlalchemy.orm import Session

from app.models import Account, Article, Budget, BudgetVersion, Cfo, Operation, PlanTarget, Posting, User
from app.services import plan
from app.services.ledger import select_lines

BASE_MONTHS = 6
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _shift(day: dt.date, months: int) -> dt.date:
    index = day.year * 12 + day.month - 1 + months
    return dt.date(index // 12, index % 12 + 1, 1)


def base_window(year: int, month: int, today: dt.date, months: int = BASE_MONTHS) -> tuple[dt.date, dt.date]:
    """Месяцы базы перед месяцем плана (не позже текущего месяца): первый день и последний день."""
    end = min(dt.date(year, month, 1), today.replace(day=1))
    return _shift(end, -months), end - dt.timedelta(days=1)


def leaves(db: Session, cfo: Cfo) -> list[Cfo]:
    """Самые нижние действующие ЦФО ветки (сам ЦФО, если подчинённых нет)."""
    branch = list(db.scalars(select(Cfo).where(Cfo.path.op("<@")(cfo.path), Cfo.is_active).order_by(Cfo.path)))
    parents = {c.parent_id for c in branch}
    return [c for c in branch if c.id not in parents]


def fact_by_cfo(db: Session, organization_id: int, article_id: int, cfo_ids: list[int],
                date_from: dt.date, date_to: dt.date) -> dict[int, Decimal]:
    """Факт по статье ОПиУ за период по ЦФО — по модулю (доход и расход — положительные), как в ОПиУ:
    проведённые операции без ввода остатков и без реформации баланса (корр. 84)."""
    reformation = list(db.scalars(select(Account.id).where(Account.code == "84")))
    lines = select_lines(organization_id, date_from=date_from, date_to=date_to, opening=False,
                         analytics={"pl_item": article_id}, analytics_in={"cfo": cfo_ids},
                         exclude_corr_account_ids=reformation).subquery()
    signed = case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    rows = db.execute(select(lines.c.cfo, func.coalesce(func.sum(signed), 0)).group_by(lines.c.cfo)).all()
    return {cfo_id: abs(Decimal(amount)) for cfo_id, amount in rows}


def split(amount: Decimal, weights: dict[int, Decimal]) -> dict[int, Decimal]:
    """Сумма по долям до копейки; остаток от округления — самой крупной доле. Весов нет — поровну."""
    if not weights:
        return {}
    total = sum(weights.values(), ZERO)
    if total <= 0:
        weights, total = {k: Decimal(1) for k in weights}, Decimal(len(weights))
    parts = {k: (amount * w / total).quantize(CENT, rounding=ROUND_HALF_UP) for k, w in weights.items()}
    biggest = max(weights, key=lambda k: weights[k])
    parts[biggest] += amount - sum(parts.values(), ZERO)
    return parts


@dataclass
class MonthResult:
    month: int
    target: Decimal | None
    base_from: dt.date
    base_to: dt.date
    by_fact: bool  # False — факта за базу нет, поделено поровну
    parts: dict[int, Decimal] = field(default_factory=dict)


def distribute(db: Session, version: BudgetVersion, cfo: Cfo, article: Article, targets: dict[int, Decimal | None],
               user: User, today: dt.date | None = None) -> list[MonthResult]:
    """Записать цели ЦФО по месяцам и разложить их на нижние ЦФО (их план по статье заменяется долями).
    Пустая цель или 0 — цель снимается, план нижних ЦФО не меняется."""
    budget = db.get(Budget, version.budget_id)
    today = today or dt.date.today()
    if article.id not in {a.id for a, _ in plan.plan_articles(db, budget.organization_id, "pl")}:
        raise plan.PlanError("Статья не подходит для плана ОПиУ")
    bottom = leaves(db, cfo)
    if not bottom:
        raise plan.PlanError("У ЦФО нет действующих подчинённых")
    results = []
    for month, amount in sorted(targets.items()):
        if month not in budget.months:
            raise plan.PlanError(f"Месяц {month} не входит в период бюджета «{budget.title}»")
        amount = amount.quantize(CENT, rounding=ROUND_HALF_UP) if amount else None
        if amount is not None and amount < 0:
            raise plan.PlanError("Цель не может быть отрицательной")
        row = db.scalar(select(PlanTarget).where(PlanTarget.version_id == version.id, PlanTarget.cfo_id == cfo.id,
                                                 PlanTarget.article_id == article.id, PlanTarget.month == month))
        base_from, base_to = base_window(budget.year, month, today)
        result = MonthResult(month, amount, base_from, base_to, by_fact=False)
        results.append(result)
        if amount is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            row = PlanTarget(version_id=version.id, cfo_id=cfo.id, article_id=article.id, month=month,
                             created_by=user.id)
            db.add(row)
        row.amount = amount
        fact = fact_by_cfo(db, budget.organization_id, article.id, [c.id for c in bottom], base_from, base_to)
        weights = {c.id: fact.get(c.id, ZERO) for c in bottom}
        result.by_fact = sum(weights.values(), ZERO) > 0
        result.parts = split(amount, weights)
        for cfo_id, part in result.parts.items():
            plan.set_cells(db, version, "pl", cfo_id, [plan.Cell(article.id, month, part)], user)
    db.flush()
    return results


# --- Ретроспективный метод (12.4) ---

RETRO_BASES = (1, 3, 6, 12)


@dataclass
class RetroResult:
    filled: int = 0  # заполнено пустых ячеек
    kept: int = 0  # ячеек с планом не тронуто
    cfos: int = 0


def _month_index(day: dt.date) -> int:
    return day.year * 12 + day.month - 1


def _monthly_fact(db: Session, organization_id: int, kind: str, cfo_ids: list[int] | None,
                  date_from: dt.date, date_to: dt.date) -> dict[tuple, Decimal]:
    """Факт по месяцам: (индекс месяца, ЦФО, статья) → сумма по модулю. ОПиУ — по «Статье ОПиУ», без ввода
    остатков и реформации; ДДС — движение денег по «Статье ДДС» без переводов между своими счетами (ЦФО — None)."""
    from app.services import reports  # здесь, чтобы не было круговой зависимости при загрузке

    if date_from > date_to:
        return {}
    if kind == "pl":
        reformation = list(db.scalars(select(Account.id).where(Account.code == "84")))
        lines = select_lines(organization_id, date_from=date_from, date_to=date_to, opening=False,
                             analytics_in={"cfo": cfo_ids}, exclude_corr_account_ids=reformation).subquery()
        item, cfo = lines.c.pl_item, lines.c.cfo
    else:
        money = reports.money_account_ids(db)
        lines = select_lines(organization_id, date_from=date_from, date_to=date_to, opening=False,
                             account_ids=money, exclude_corr_account_ids=money).subquery()
        item, cfo = lines.c.cash_flow_item, None
    signed = case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    marked = select(func.to_char(lines.c.op_date, "YYYY-MM").label("month"), item.label("article_id"),
                    (cfo if cfo is not None else null()).label("cfo_id"),
                    signed.label("signed")).where(item.is_not(None)).subquery()
    result: dict[tuple, Decimal] = {}
    for month, cfo_id, article_id, amount in db.execute(
            select(marked.c.month, marked.c.cfo_id, marked.c.article_id, func.coalesce(func.sum(marked.c.signed), 0))
            .group_by(marked.c.month, marked.c.cfo_id, marked.c.article_id)).all():
        year, mon = (int(x) for x in month.split("-"))
        result[(year * 12 + mon - 1, cfo_id, article_id)] = abs(Decimal(amount))
    return result


def retrospective(db: Session, version: BudgetVersion, kind: str, cfo: Cfo | None, with_branch: bool, base: int,
                  user: User, today: dt.date | None = None) -> RetroResult:
    """Заполнить пустые ячейки плана средним за base месяцев перед каждым месяцем плана. Прошедшие месяцы базы —
    факт, текущий и будущие — план (уже введённый или только что рассчитанный). Заполненные ячейки не меняются."""
    if base not in RETRO_BASES:
        raise plan.PlanError("База — 1, 3, 6 или 12 месяцев")
    if kind not in ("pl", "cf"):
        raise plan.PlanError("По среднему заполняется план ОПиУ или ДДС")
    if kind == "pl" and cfo is None:
        raise plan.PlanError("План ОПиУ заполняется по ЦФО — выберите ЦФО")
    budget = db.get(Budget, version.budget_id)
    today = today or dt.date.today()
    current = _month_index(today)
    if kind == "pl":
        cfos = (list(db.scalars(select(Cfo).where(Cfo.path.op("<@")(cfo.path), Cfo.is_active).order_by(Cfo.path)))
                if with_branch else [cfo])
        cfo_ids: list[int | None] = [c.id for c in cfos]
    else:
        cfo_ids = [None]
    articles = [a.id for a, _ in plan.plan_articles(db, budget.organization_id, kind)]
    months = sorted(budget.months)
    budget_months = {_month_index(dt.date(budget.year, m, 1)) for m in months}
    first = _month_index(dt.date(budget.year, months[0], 1)) - base
    fact = _monthly_fact(db, budget.organization_id, kind, [c for c in cfo_ids if c is not None] or None,
                         dt.date(first // 12, first % 12 + 1, 1),
                         min(dt.date(budget.year, months[-1], 1), today.replace(day=1)) - dt.timedelta(days=1))
    # план версии: (индекс месяца, ЦФО, статья) → сумма (строки с ЦФУ — тоже: ячейка занята)
    existing: dict[tuple, Decimal] = {}
    query = (select(Operation.op_date, Operation.plan_cfo_id, Operation.article_id, func.sum(Posting.amount))
             .join(Posting, Posting.operation_id == Operation.id)
             .where(Operation.budget_version_id == version.id, Operation.plan_kind == kind,
                    Operation.plan_project_id.is_(None))
             .group_by(Operation.op_date, Operation.plan_cfo_id, Operation.article_id))
    for day, cfo_id, article_id, amount in db.execute(query).all():
        existing[(_month_index(day), cfo_id, article_id)] = Decimal(amount)

    result = RetroResult(cfos=len(cfo_ids))
    for cfo_id in cfo_ids:
        planned = {k: v for k, v in existing.items() if k[1] == cfo_id}
        cells = []
        for article_id in articles:
            for month in months:
                index = _month_index(dt.date(budget.year, month, 1))
                key = (index, cfo_id, article_id)
                if key in planned:
                    result.kept += 1
                    continue
                # месяцы базы: прошедшие — факт; месяцы этого бюджета — план; остальные (не наступили и не в
                # бюджете) в среднее не входят
                values = [fact.get((m, cfo_id, article_id), ZERO) if m < current
                          else planned.get((m, cfo_id, article_id), ZERO)
                          for m in range(index - base, index) if m < current or m in budget_months]
                if not values:
                    continue
                average = (sum(values, ZERO) / len(values)).quantize(CENT, rounding=ROUND_HALF_UP)
                if average > 0:
                    planned[key] = average  # следующие месяцы берут его в базу
                    cells.append(plan.Cell(article_id, month, average))
        if cells:
            plan.set_cells(db, version, kind, cfo_id, cells, user)
            result.filled += len(cells)
    db.flush()
    return result


def targets(db: Session, version: BudgetVersion, cfo: Cfo) -> list[dict]:
    """Цели ЦФО и «по низу» — план ветки ЦФО по той же статье и месяцу (вместе со своим планом ЦФО)."""
    budget = db.get(Budget, version.budget_id)
    branch = select(Cfo.id).where(Cfo.path.op("<@")(cfo.path))
    rows = db.scalars(select(PlanTarget).where(PlanTarget.version_id == version.id, PlanTarget.cfo_id == cfo.id)
                      .order_by(PlanTarget.article_id, PlanTarget.month)).all()
    result = []
    for t in rows:
        bottom = db.scalar(
            select(func.coalesce(func.sum(Posting.amount), 0)).join(Operation, Operation.id == Posting.operation_id)
            .where(Operation.budget_version_id == version.id, Operation.plan_kind == "pl",
                   Operation.article_id == t.article_id, Operation.op_date == dt.date(budget.year, t.month, 1),
                   Operation.plan_cfo_id.in_(branch)))
        bottom = Decimal(bottom) + ZERO
        result.append({"article_id": t.article_id, "month": t.month, "target": t.amount, "bottom": bottom,
                       "deviation": bottom - t.amount})
    return result
