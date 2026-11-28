"""«Сверху ИТОГ» и фиксация плана месяца (docs/TZ.md, раздел 31, «Детали 23.4»; раздел 32, «Детали 24.4»).

- ИТОГ выбирает финансовый директор: значение одной из колонок 1–6 (до фиксации ИТОГ следует за колонкой) или своя
  сумма; пока не выбран — ИТОГ равен «Бюджету» (решение владельца 1 в);
- «Зафиксировать план» по каждой статье (решения 2 а и 3):
  - ИТОГ из «Снизу ЦФО» или «Снизу ЦФУ» — в бюджете остаётся только эта колонка, плановые операции другой «снизу»
    колонки по статье за месяц удаляются;
  - иначе, если заполнены обе «снизу» колонки, остаётся большая из них;
  - если обе «снизу» пусты, а ИТОГ взят из колонки 1–4 — её суммы пишутся в бюджет на свои ЦФО (как в 23.4);
    своя сумма при пустых «снизу» — только цель;
  - ИТОГ каждой статьи сохраняется как утверждённая цифра (цель); вниз ИТОГ не делится.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, BudgetVersion, Cfo, Operation, PlanItog, PlanItogEvent, User
from app.services import plan
from app.services.articles import is_group
from app.services.method_plan import METHOD_KEYS, ZERO, compute, itog_scope_ok
from app.services.plan import PlanError
from app.services.reports import ReportFilter

WRITTEN_METHODS = ("norm", "avg", "fact", "calc")
SOURCES = (*METHOD_KEYS, "manual")
SOURCE_TITLES = {"norm": "1. Нормативный", "avg": "2. Средний", "fact": "3. Фактический", "calc": "4. Расчётный",
                 "cfo": "5. Снизу ЦФО", "executor": "6. Снизу ЦФУ", "manual": "своя сумма"}


def _stored(db: Session, version_id: int, cfo_id: int | None, article_id: int, month: int) -> PlanItog | None:
    return db.scalar(select(PlanItog).where(
        PlanItog.version_id == version_id, PlanItog.article_id == article_id, PlanItog.month == month,
        PlanItog.cfo_id.is_(None) if cfo_id is None else PlanItog.cfo_id == cfo_id))


def _filter(organization_id: int, month: dt.date, cfo_id: int | None) -> ReportFilter:
    first = month.replace(day=1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return ReportFilter(organization_id=organization_id, date_from=first, date_to=last, cfo_id=cfo_id)


def _write(db: Session, version: BudgetVersion, cfo_id: int | None, article_id: int, month: int,
           amount: Decimal, source: str, user: User) -> None:
    stored = _stored(db, version.id, cfo_id, article_id, month)
    old = stored.amount if stored is not None else None
    if stored is None:
        stored = PlanItog(version_id=version.id, cfo_id=cfo_id, article_id=article_id, month=month)
        db.add(stored)
    stored.amount, stored.source, stored.manual, stored.updated_by = amount, source, source == "manual", user.id
    stored.fixed_by = stored.fixed_at = None  # выбор после фиксации — снова до фиксации
    db.add(PlanItogEvent(version_id=version.id, cfo_id=cfo_id, article_id=article_id, month=month,
                         action="edit", old_amount=old, new_amount=amount, user_id=user.id,
                         details=f"из колонки «{SOURCE_TITLES[source]}»" if source != "manual" else "вручную"))


def set_itog(db: Session, version: BudgetVersion, organization_id: int, cfo_id: int | None, article_id: int,
             month: dt.date, amount: Decimal | None, user: User, source: str | None = None) -> None:
    """ИТОГ статьи: source — колонка метода (сумма берётся из неё) или manual (своя сумма amount);
    без source и amount — сбросить выбор (ИТОГ снова равен бюджету)."""
    article = db.get(Article, article_id)
    if article is None or article.organization_id != organization_id:
        raise PlanError("Статья не найдена в этой организации")
    if is_group(db, article.id):
        raise PlanError("ИТОГ задаётся для статьи без подстатей; у группы он складывается из подстатей")
    if source is None and amount is not None:
        source = "manual"
    if source is None:
        stored = _stored(db, version.id, cfo_id, article_id, month.month)
        if stored is not None:
            db.add(PlanItogEvent(version_id=version.id, cfo_id=cfo_id, article_id=article_id, month=month.month,
                                 action="reset", old_amount=stored.amount, user_id=user.id, details="равен бюджету"))
            db.delete(stored)
        return
    if source not in SOURCES:
        raise PlanError("Неизвестная колонка для ИТОГ")
    if source == "manual":
        if amount is None:
            raise PlanError("Введите сумму")
    else:
        leaf = compute(db, _filter(organization_id, month, cfo_id), version.id, month).leaves[article_id]
        if leaf.values.get(source) is None:
            raise PlanError(f"В колонке «{SOURCE_TITLES[source]}» у статьи нет значения")
        amount = leaf.values[source]
    _write(db, version, cfo_id, article_id, month.month, amount, source, user)


def own_methods(db: Session, version: BudgetVersion, f: ReportFilter, month: dt.date, user: User) -> int:
    """ИТОГ = основная колонка статьи (метод из справочника) у статей, где ИТОГ ещё не выбран; возвращает число статей."""
    if not itog_scope_ok(f):
        raise PlanError("ИТОГ задаётся без фильтра юрлица и ЦФУ (по всей организации или ветке ЦФО)")
    count = 0
    for _ in range(10):  # расчётные зависят от ИТОГ базы — повторяем, пока что-то добавляется
        added = 0
        for leaf in compute(db, f, version.id, month).leaves.values():
            if leaf.itog_source != "budget" or not leaf.article.is_active or leaf.method not in METHOD_KEYS:
                continue
            if leaf.value is None or (not leaf.value and not leaf.budget):
                continue
            _write(db, version, f.cfo_id, leaf.article.id, month.month, leaf.value, leaf.method, user)
            added += 1
        db.flush()
        count += added
        if not added:
            break
    return count


def _fallback_cfo(db: Session, organization_id: int, cfo_id: int | None) -> int | None:
    if cfo_id is not None:
        return cfo_id
    roots = list(db.scalars(select(Cfo.id).where(Cfo.organization_id == organization_id, Cfo.parent_id.is_(None),
                                                 Cfo.is_active)))
    return roots[0] if len(roots) == 1 else None


def _cell_query(version: BudgetVersion, kind: str, article_id: int, start: dt.date, branch: set[int] | None,
                with_executor: bool):
    query = select(Operation).where(
        Operation.budget_version_id == version.id, Operation.plan_kind == kind, Operation.article_id == article_id,
        Operation.op_date == start, Operation.plan_project_id.is_(None), Operation.plan_auto.is_(False),
        Operation.plan_executor_id.is_not(None) if with_executor else Operation.plan_executor_id.is_(None))
    if branch is not None:
        query = query.where(Operation.plan_cfo_id.in_(branch))
    return query


def _clear(db: Session, version: BudgetVersion, kind: str, article_id: int, start: dt.date, branch: set[int] | None,
           with_executor: bool, user: User, guard) -> int:
    """Удалить плановые операции статьи за месяц: колонки «снизу ЦФУ» (with_executor) или «снизу ЦФО»."""
    count = 0
    for op in db.scalars(_cell_query(version, kind, article_id, start, branch, with_executor)).all():
        plan.set_cells(db, version, kind, op.plan_cfo_id, [plan.Cell(article_id, start.month, None)], user,
                       executor_id=op.plan_executor_id, guard=guard)
        count += 1
    return count


def fix(db: Session, version: BudgetVersion, f: ReportFilter, month: dt.date, user: User, guard) -> dict:
    """Зафиксировать план месяца по ЦФО отчёта; возвращает {written, cleared, skipped, itogs}."""
    if not itog_scope_ok(f):
        raise PlanError("Фиксация — без фильтра юрлица и ЦФУ (по всей организации или ветке ЦФО)")
    start = month.replace(day=1)
    c = compute(db, f, version.id, start)
    fallback = _fallback_cfo(db, f.organization_id, f.cfo_id)
    branch = None
    if f.cfo_id is not None:
        root = db.get(Cfo, f.cfo_id)
        branch = set(db.scalars(select(Cfo.id).where(Cfo.path.op("<@")(root.path))))
    written = cleared = skipped = 0
    for leaf in c.leaves.values():
        if not leaf.article.is_active:
            continue
        kind = "pl" if leaf.article.is_pl else "cf"
        source = leaf.itog_source
        has_cfo, has_executor = c.has_cells.get(leaf.article.id, (False, False))
        keep = None  # какая «снизу» колонка остаётся в бюджете
        if source in ("cfo", "executor"):
            keep = source
        elif has_cfo and has_executor:
            keep = "executor" if leaf.executor > leaf.cfo else "cfo"
        if keep == "cfo" and has_executor:
            cleared += _clear(db, version, kind, leaf.article.id, start, branch, True, user, guard)
        elif keep == "executor" and has_cfo:
            cleared += _clear(db, version, kind, leaf.article.id, start, branch, False, user, guard)
        if has_cfo or has_executor or source not in WRITTEN_METHODS:
            continue
        # «снизу» пусто, ИТОГ — из колонки 1–4: её суммы — в бюджет на свои ЦФО
        targets: dict[int | None, Decimal] = {}
        for cfo, amount in leaf.by_cfo.get(source, {}).items():
            key = cfo if cfo is not None else (fallback if kind == "pl" or f.cfo_id is not None else None)
            if key is None and kind == "pl":
                if amount:
                    raise PlanError(f"«{leaf.article.name}»: не определён ЦФО — выберите ЦФО в фильтре отчёта")
                continue
            targets[key] = targets.get(key, ZERO) + amount
        for cfo, amount in targets.items():
            if amount <= 0:
                skipped += 1 if amount < 0 else 0
                continue
            written += plan.set_cells(db, version, kind, cfo, [plan.Cell(leaf.article.id, start.month, amount)],
                                      user, guard=guard)
    db.flush()
    # ИТОГ — по бюджету после записи (ИТОГ «по бюджету» — уже новый бюджет)
    c = compute(db, f, version.id, start)
    now = dt.datetime.now(dt.UTC)
    itogs = 0
    for leaf in c.leaves.values():
        stored = leaf.stored
        amount = leaf.budget if leaf.itog_source == "budget" else leaf.itog
        if stored is None and not amount:
            continue
        if stored is None:
            stored = PlanItog(version_id=version.id, cfo_id=f.cfo_id, article_id=leaf.article.id, month=start.month,
                              source="budget", manual=False, updated_by=user.id)
            db.add(stored)
        stored.amount = amount
        stored.fixed_by, stored.fixed_at = user.id, now
        itogs += 1
    db.add(PlanItogEvent(version_id=version.id, cfo_id=f.cfo_id, month=start.month, action="fix", user_id=user.id,
                         details=f"статей с ИТОГ: {itogs}; в бюджет записано ячеек: {written}, удалено: {cleared}"
                                 + (f", отрицательных сумм пропущено: {skipped}" if skipped else "")))
    db.flush()
    return {"written": written, "cleared": cleared, "skipped": skipped, "itogs": itogs}
