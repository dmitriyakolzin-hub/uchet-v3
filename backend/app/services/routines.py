"""Регламентные операции (docs/TZ.md, раздел 14, «Детали 4.5»): правило = образец операции + число месяца.

from_operation — правило из готовой операции журнала (её шапка без даты и проводки)
due          — правила, которые должны выполниться в месяце
execute      — выполнить за месяц: по операции на правило, повторно не создаётся
"""

import calendar
import datetime as dt
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Operation, RoutineRule, RoutineRun, User
from app.models.journal import ANALYTIC_COLUMNS, analytic_column
from app.schemas.journal import OperationIn
from app.services import journal
from app.services.journal import JournalError


class RoutineError(ValueError):
    """Действие с регламентной операцией невозможно; текст — для пользователя."""


def month_start(year: int, month: int) -> dt.date:
    return dt.date(year, month, 1)


def run_date(rule: RoutineRule, year: int, month: int) -> dt.date:
    """Дата операции в месяце: число из правила, в коротком месяце — последний день."""
    return dt.date(year, month, min(rule.day, calendar.monthrange(year, month)[1]))


def template_of(op: Operation) -> dict:
    """Образец из операции: статья, описание, проводки с аналитикой (суммы в рублях — по курсу на дату)."""
    postings = []
    for p in op.postings:
        posting = {"debit_account_id": p.debit_account_id, "credit_account_id": p.credit_account_id,
                   "amount": str(p.amount) if p.amount is not None else None, "currency_id": p.currency_id,
                   "quantity": str(p.quantity) if p.quantity is not None else None}
        for side in ("debit", "credit"):
            posting[side] = {kind: getattr(p, analytic_column(side, kind)) for kind in ANALYTIC_COLUMNS
                             if getattr(p, analytic_column(side, kind)) is not None}
        postings.append(posting)
    return {"article_id": op.article_id, "description": op.description, "postings": postings}


def from_operation(db: Session, op: Operation, *, name: str, day: int, valid_from: dt.date, post: bool) -> RoutineRule:
    if op.budget_version_id is not None or op.is_opening:
        raise RoutineError("Регламентной можно сделать только обычную операцию журнала")
    if not op.postings:
        raise RoutineError("В операции нет проводок")
    rule = RoutineRule(organization_id=op.organization_id, name=name, legal_entity_id=op.legal_entity_id, day=day,
                       valid_from=valid_from.replace(day=1), post=post, template=template_of(op))
    db.add(rule)
    db.flush()
    return rule


def due(db: Session, organization_id: int, year: int, month: int) -> list[RoutineRule]:
    """Действующие правила, срок которых включает месяц."""
    start = month_start(year, month)
    return list(db.scalars(select(RoutineRule).where(
        RoutineRule.organization_id == organization_id, RoutineRule.is_active.is_(True),
        RoutineRule.valid_from <= start, or_(RoutineRule.valid_to.is_(None), RoutineRule.valid_to >= start),
    ).order_by(RoutineRule.day, RoutineRule.name)))


def run_of(db: Session, rule_id: int, year: int, month: int) -> RoutineRun | None:
    return db.scalar(select(RoutineRun).where(
        RoutineRun.rule_id == rule_id, RoutineRun.year == year, RoutineRun.month == month))


@dataclass
class ExecResult:
    rule: RoutineRule
    operation: Operation | None
    error: str | None = None


def execute(db: Session, organization_id: int, year: int, month: int, user: User) -> list[ExecResult]:
    """Выполнить невыполненные правила месяца. Ошибка одного правила не мешает остальным;
    операция, которую не удалось провести, остаётся черновиком (ошибка — в результате)."""
    results = []
    for rule in due(db, organization_id, year, month):
        if run_of(db, rule.id, year, month) is not None:
            continue
        try:
            body = OperationIn(legal_entity_id=rule.legal_entity_id, op_date=run_date(rule, year, month), **rule.template)
        except ValidationError:
            results.append(ExecResult(rule, None, "Образец операции повреждён — создайте правило заново"))
            continue
        op = None
        try:
            with db.begin_nested():
                op = Operation(organization_id=organization_id, created_by=user.id, status="draft")
                journal.save_draft(db, op, body, user)
                op.number = journal.next_number(db, organization_id)
                db.add(op)
                db.flush()
                journal._event(db, op, user, "created")
                db.add(RoutineRun(rule_id=rule.id, year=year, month=month, operation_id=op.id, created_by=user.id))
                db.flush()
        except JournalError as exc:
            results.append(ExecResult(rule, None, str(exc)))
            continue
        error = None
        if rule.post:
            try:
                with db.begin_nested():
                    journal.post(db, op, user)
                    db.flush()
            except JournalError as exc:
                error = f"Не проведена, осталась черновиком: {exc}"
        results.append(ExecResult(rule, op, error))
    return results
