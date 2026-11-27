"""«Сверху ИТОГ» и фиксация плана месяца (docs/TZ.md, раздел 31, «Детали 23.4»).

- правка ИТОГ: финансовый директор ставит сумму статьи вручную (manual) или возвращает её к методу;
- «Зафиксировать план»: суммы методов 1–4 (нормативный, средний, фактический, расчётный) записываются в версию
  бюджета на свои ЦФО (прежние плановые операции этих статей за месяц в ветке без ЦФУ заменяются), ИТОГ каждой
  статьи сохраняется как утверждённая цифра (цель); вниз ИТОГ не делится (решение владельца 4 б).
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, BudgetVersion, Cfo, Operation, PlanItog, PlanItogEvent, User
from app.services import plan
from app.services.articles import is_group
from app.services.method_plan import ZERO, compute, itog_scope_ok
from app.services.plan import PlanError
from app.services.reports import ReportFilter

WRITTEN_METHODS = ("norm", "avg", "fact", "calc")


def _stored(db: Session, version_id: int, cfo_id: int | None, article_id: int, month: int) -> PlanItog | None:
    return db.scalar(select(PlanItog).where(
        PlanItog.version_id == version_id, PlanItog.article_id == article_id, PlanItog.month == month,
        PlanItog.cfo_id.is_(None) if cfo_id is None else PlanItog.cfo_id == cfo_id))


def set_itog(db: Session, version: BudgetVersion, organization_id: int, cfo_id: int | None, article_id: int,
             month: dt.date, amount: Decimal | None, user: User) -> None:
    """ИТОГ статьи вручную; None — вернуть к методу (сохранённая сумма удаляется)."""
    article = db.get(Article, article_id)
    if article is None or article.organization_id != organization_id:
        raise PlanError("Статья не найдена в этой организации")
    if is_group(db, article.id):
        raise PlanError("ИТОГ задаётся для статьи без подстатей; у группы он складывается из подстатей")
    stored = _stored(db, version.id, cfo_id, article_id, month.month)
    old = stored.amount if stored is not None else None
    if amount is None:
        if stored is not None:
            db.delete(stored)
            db.add(PlanItogEvent(version_id=version.id, cfo_id=cfo_id, article_id=article_id, month=month.month,
                                 action="reset", old_amount=old, user_id=user.id, details="по методу"))
        return
    if stored is None:
        stored = PlanItog(version_id=version.id, cfo_id=cfo_id, article_id=article_id, month=month.month,
                          amount=amount, manual=True, updated_by=user.id)
        db.add(stored)
    else:
        stored.amount, stored.manual, stored.updated_by = amount, True, user.id
    db.add(PlanItogEvent(version_id=version.id, cfo_id=cfo_id, article_id=article_id, month=month.month,
                         action="edit", old_amount=old, new_amount=amount, user_id=user.id))


def _fallback_cfo(db: Session, organization_id: int, cfo_id: int | None) -> int | None:
    if cfo_id is not None:
        return cfo_id
    roots = list(db.scalars(select(Cfo.id).where(Cfo.organization_id == organization_id, Cfo.parent_id.is_(None),
                                                 Cfo.is_active)))
    return roots[0] if len(roots) == 1 else None


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
        if leaf.method not in WRITTEN_METHODS or not leaf.article.is_active:
            continue
        kind = "pl" if leaf.article.is_pl else "cf"
        targets: dict[int | None, Decimal] = {}
        for cfo, amount in leaf.by_cfo.items():
            key = cfo if cfo is not None else (fallback if kind == "pl" or f.cfo_id is not None else None)
            if key is None and kind == "pl":
                if amount:
                    raise PlanError(f"«{leaf.article.name}»: не определён ЦФО — выберите ЦФО в фильтре отчёта")
                continue
            targets[key] = targets.get(key, ZERO) + amount
        query = select(Operation).where(
            Operation.budget_version_id == version.id, Operation.plan_kind == kind,
            Operation.article_id == leaf.article.id, Operation.op_date == start,
            Operation.plan_executor_id.is_(None), Operation.plan_project_id.is_(None), Operation.plan_auto.is_(False))
        if branch is not None:
            query = query.where(Operation.plan_cfo_id.in_(branch))
        existing: dict[int | None, Decimal] = {}
        for op in db.scalars(query).all():
            existing[op.plan_cfo_id] = sum((p.amount or ZERO for p in op.postings), ZERO)
            if op.plan_cfo_id not in targets or targets[op.plan_cfo_id] <= 0:
                plan.set_cells(db, version, kind, op.plan_cfo_id, [plan.Cell(leaf.article.id, start.month, None)],
                               user, guard=guard)
                cleared += 1
        for cfo, amount in targets.items():
            if amount <= 0:
                skipped += 1 if amount < 0 else 0
                continue
            if existing.get(cfo) == amount:  # уже записано — не трогаем
                continue
            written += plan.set_cells(db, version, kind, cfo, [plan.Cell(leaf.article.id, start.month, amount)],
                                      user, guard=guard)
    now = dt.datetime.now(dt.UTC)
    itogs = 0
    for leaf in c.leaves.values():
        stored = leaf.stored
        if stored is None and not leaf.method_value:
            continue
        if stored is None:
            stored = PlanItog(version_id=version.id, cfo_id=f.cfo_id, article_id=leaf.article.id, month=start.month,
                              amount=leaf.method_value, manual=False, updated_by=user.id)
            db.add(stored)
        elif not stored.manual:
            stored.amount = leaf.method_value
        stored.fixed_by, stored.fixed_at = user.id, now
        itogs += 1
    db.add(PlanItogEvent(version_id=version.id, cfo_id=f.cfo_id, month=start.month, action="fix", user_id=user.id,
                         details=f"статей с ИТОГ: {itogs}; в бюджет записано ячеек: {written}, очищено: {cleared}"
                                 + (f", отрицательных сумм пропущено: {skipped}" if skipped else "")))
    db.flush()
    return {"written": written, "cleared": cleared, "skipped": skipped, "itogs": itogs}
