"""Массовое изменение операций журнала (docs/TZ.md, раздел 20, «Детали 10.1»).

Замены применяются к каждой операции в своей точке сохранения: черновик просто меняется, проведённая операция
после изменения проверяется как при проведении — не прошла проверку, остаётся как была (номер и причина в итогах).
Предпросмотр (dry_run) делает то же самое и откатывает.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.analytics import ANALYTIC_TITLES, OPTIONAL_ANALYTICS, AnalyticKind
from app.models import Account, AccountAnalytic, Article, Currency, Operation, OperationEvent, User
from app.models.journal import ANALYTIC_COLUMNS, analytic_column
from app.services import journal
from app.services.articles import is_group
from app.services.journal import JournalError
from app.services.reports import analytic_names

SIDES = {"debit": "дебет", "credit": "кредит"}


@dataclass
class Change:
    """field: article, description, op_date, account или вид аналитики (counterparty, contract, …).
    side — для проводок: debit, credit или both. old — менять только там, где сейчас это значение (None — везде;
    для счёта обязателен). mode — для описания: replace, append, substitute (old → new в тексте)."""

    field: str
    new: int | str | dt.date | None
    side: str = "both"
    old: int | str | None = None
    mode: str = "replace"


@dataclass
class OpResult:
    id: int
    number: int | None
    ok: bool
    changed: bool = False
    error: str = ""
    details: list[str] = field(default_factory=list)


def _sides(change: Change) -> tuple[str, ...]:
    return ("debit", "credit") if change.side == "both" else (change.side,)


class _Names:
    """Подписи значений для истории: «ООО А → ООО Б»."""

    def __init__(self, db: Session):
        self.db = db

    def __call__(self, kind: str, value) -> str:
        if value is None:
            return "—"
        if kind == "account":
            account = self.db.get(Account, value)
            return account.code if account else str(value)
        if kind == "article":
            article = self.db.get(Article, value)
            return article.name if article else str(value)
        return analytic_names(self.db, kind, [value]).get(value, str(value))


def _analytic_kinds(db: Session, account_id: int | None) -> set[str]:
    if account_id is None:
        return set()
    account = db.get(Account, account_id)
    kinds = {a.kind for a in db.scalars(select(AccountAnalytic).where(AccountAnalytic.account_id == account.id))}
    # необязательная аналитика (ЦФУ) ставится там, где ведётся основная (ЦФО)
    kinds |= {extra for extra, base in OPTIONAL_ANALYTICS.items() if base in kinds}
    return kinds


def validate(db: Session, organization_id: int, changes: list[Change]) -> None:
    """Новые значения — своей организации и подходящие (статья — без подстатей, счёт — без субсчетов)."""
    for c in changes:
        if c.field in ("description", "op_date"):
            continue
        if c.new is None:
            raise JournalError("Укажите новое значение")
        if c.field == "article":
            article = db.get(Article, c.new)
            if article is None or article.organization_id != organization_id:
                raise JournalError("Статья не найдена в этой организации")
            if is_group(db, article.id):
                raise JournalError(f"«{article.name}» — группа статей, выберите подстатью")
        elif c.field == "account":
            if c.old is None:
                raise JournalError("Для замены счёта укажите, какой счёт менять")
            account = db.get(Account, c.new)
            if account is None or db.scalar(select(Account.id).where(Account.parent_id == account.id).limit(1)):
                raise JournalError("Счёт не найден или у него есть субсчета — выберите субсчёт")
        elif c.field in ANALYTIC_COLUMNS:
            journal._check_ref(db, organization_id, c.field, c.new, ANALYTIC_TITLES[AnalyticKind(c.field)])
        else:
            raise JournalError(f"Неизвестное поле «{c.field}»")


def _apply(db: Session, op: Operation, changes: list[Change], names: _Names) -> list[str]:
    """Изменить операцию; вернуть описание изменений (пусто — ничего не поменялось)."""
    details: list[str] = []
    for c in changes:
        if c.field == "article":
            if op.article_id != c.new:
                details.append(f"Статья операции: {names('article', op.article_id)} → {names('article', c.new)}")
                op.article_id = c.new
        elif c.field == "description":
            text = op.description or ""
            if c.mode == "append":
                new = f"{text} {c.new}".strip() if c.new else text
            elif c.mode == "substitute":
                new = text.replace(str(c.old or ""), str(c.new or "")) if c.old else text
            else:
                new = str(c.new or "")
            new = new[:2000]
            if new != text:
                details.append(f"Описание: «{text}» → «{new}»")
                op.description = new
        elif c.field == "op_date":
            if c.new != op.op_date:
                if op.is_opening:
                    raise JournalError("Дату ввода начальных остатков менять нельзя")
                details.append(f"Дата: {op.op_date:%d.%m.%Y} → {c.new:%d.%m.%Y}")
                op.op_date = c.new
                for p in op.postings:  # сумма в рублях — по курсу новой даты (кроме введённой вручную)
                    if not p.amount_rub_manual:
                        p.amount_rub = journal._amount_rub(db, db.get(Currency, p.currency_id), p.amount, c.new)
        elif c.field == "account":
            for p in op.postings:
                for side in _sides(c):
                    column = f"{side}_account_id"
                    if getattr(p, column) == c.old:
                        setattr(p, column, c.new)
                        details.append(f"Проводка {p.line_no}, счёт {SIDES[side]}а: "
                                       f"{names('account', c.old)} → {names('account', c.new)}")
        else:
            for p in op.postings:
                for side in _sides(c):
                    column = analytic_column(side, c.field)
                    current = getattr(p, column)
                    if c.old is not None:
                        if current != c.old:
                            continue
                    elif current is None and c.field not in _analytic_kinds(
                            db, p.debit_account_id if side == "debit" else p.credit_account_id):
                        continue  # у счёта нет такой аналитики — значение не ставим
                    if current != c.new:
                        setattr(p, column, c.new)
                        title = ANALYTIC_TITLES[AnalyticKind(c.field)]
                        details.append(f"Проводка {p.line_no}, {title} ({SIDES[side]}): "
                                       f"{names(c.field, current)} → {names(c.field, c.new)}")
    return details


def run(db: Session, user: User, organization_id: int, ops: list[Operation], changes: list[Change], *,
        can_post: bool, can_change_draft, branch_check, dry_run: bool) -> list[OpResult]:
    """Применить замены к операциям. can_change_draft(op) и branch_check(op) бросают JournalError при запрете."""
    validate(db, organization_id, changes)
    names = _Names(db)
    results = []
    for op in ops:
        result = OpResult(op.id, op.number, ok=True)
        savepoint = db.begin_nested()
        try:
            if op.status == "cancelled":
                raise JournalError("Аннулированную операцию не меняем — сначала восстановите её")
            if op.status == "posted" and not can_post:
                raise JournalError("Проведённую операцию меняет тот, у кого есть право проводить")
            if op.status == "draft":
                can_change_draft(op)
            branch_check(op)
            details = _apply(db, op, changes, names)
            branch_check(op)
            if details:
                if op.status == "posted":
                    errors = journal.posting_errors(db, op)
                    if errors:
                        raise JournalError("после изменения не проходит проверку: " + "; ".join(errors))
                op.updated_by, op.updated_at = user.id, journal._now()
                db.add(OperationEvent(operation_id=op.id, user_id=user.id, action="bulk_edited",
                                      details="; ".join(details)))
                db.flush()  # здесь же срабатывает запрет изменений в закрытом периоде
            result.changed, result.details = bool(details), details
        except JournalError as exc:
            result.ok, result.error = False, str(exc)
            savepoint.rollback()
            db.expire(op)
            results.append(result)
            continue
        if dry_run:
            savepoint.rollback()
            db.expire(op)
        else:
            savepoint.commit()
        results.append(result)
    return results
