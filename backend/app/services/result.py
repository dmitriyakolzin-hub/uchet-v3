"""Финансовый результат (docs/TZ.md, раздел 15, «Детали 5.1»).

close_month — по юрлицу операция последним днём месяца: 26, 44 → 90.02; субсчета 90, 91 → 99 (статья ОПиУ
              сохраняется, поэтому ОПиУ не меняется); в декабре ещё и реформация 99 → 84
pending     — юрлица, у которых на конец месяца 26, 44, 90, 91 (в декабре и 99) не закрыты
"""

import calendar
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Account, LegalEntity, Operation, ResultClosing, User
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import journal
from app.services.journal import JournalError
from app.services.ledger import select_lines

COST_ROOTS = ("26", "44")
RESULT_ROOTS = ("90", "91")
MONTHS_GENITIVE = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
                   "октябрь", "ноябрь", "декабрь"]


class ResultError(ValueError):
    """Финансовый результат определить нельзя; текст — для пользователя."""


def month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _ids(db: Session, codes) -> list[int]:
    return list(db.scalars(select(Account.id).where(Account.code.in_(codes))))


def _balances(db: Session, organization_id: int, legal_entity_id: int, roots, on_date: dt.date,
              key=("account_id", "cfo", "pl_item")) -> dict[tuple, Decimal]:
    """Сальдо (дебет − кредит) на конец дня по счетам roots с субсчетами, по группам key."""
    lines = select_lines(organization_id, account_ids=_ids(db, roots), date_to=on_date,
                         legal_entity_id=legal_entity_id).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    columns = [getattr(lines.c, k) for k in key]
    rows = db.execute(select(*columns, func.sum(signed)).group_by(*columns))
    return {tuple(r[:-1]): Decimal(r[-1]) for r in rows if r[-1]}


def _closing_postings(db: Session, organization_id: int, legal_entity_id: int, end: dt.date) -> list[PostingIn]:
    codes = {a.id: a.code for a in db.scalars(select(Account))}
    by_code = {v: k for k, v in codes.items()}
    sales_cost, result = by_code["90.02"], by_code["99"]
    postings = []

    def move(debit, credit, amount, debit_an, credit_an):
        postings.append(PostingIn(debit_account_id=debit, credit_account_id=credit, amount=amount,
                                  debit=debit_an, credit=credit_an))

    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    for key, value in _balances(db, organization_id, legal_entity_id, RESULT_ROOTS, end).items():
        totals[key] += value
    for (account_id, cfo, pl_item), value in sorted(_balances(db, organization_id, legal_entity_id, COST_ROOTS,
                                                              end).items(), key=lambda kv: str(kv[0])):
        an = AnalyticsIn(cfo=cfo, pl_item=pl_item)
        if value > 0:
            move(sales_cost, account_id, value, an, an)
        else:
            move(account_id, sales_cost, -value, an, an)
        totals[(sales_cost, cfo, pl_item)] += value
    for (account_id, cfo, pl_item), value in sorted(totals.items(), key=lambda kv: (codes[kv[0][0]], str(kv[0]))):
        if not value:
            continue
        if pl_item is None:
            raise ResultError(f"На счёте {codes[account_id]} есть суммы без статьи ОПиУ — их не на что отнести в 99")
        an, result_an = AnalyticsIn(cfo=cfo, pl_item=pl_item), AnalyticsIn(pl_item=pl_item)
        if value > 0:
            move(result, account_id, value, result_an, an)
        else:
            move(account_id, result, -value, an, result_an)
    return postings


def _reformation_postings(db: Session, organization_id: int, legal_entity_id: int, end: dt.date) -> list[PostingIn]:
    codes = {a.code: a.id for a in db.scalars(select(Account).where(Account.code.in_(("84", "99"))))}
    postings = []
    for (pl_item,), value in sorted(_balances(db, organization_id, legal_entity_id, ("99",), end,
                                              key=("pl_item",)).items(), key=lambda kv: str(kv[0])):
        if pl_item is None:
            raise ResultError("На счёте 99 есть суммы без статьи ОПиУ — реформацию провести нельзя")
        an = AnalyticsIn(pl_item=pl_item)
        if value < 0:  # прибыль
            postings.append(PostingIn(debit_account_id=codes["99"], credit_account_id=codes["84"], amount=-value, debit=an))
        else:
            postings.append(PostingIn(debit_account_id=codes["84"], credit_account_id=codes["99"], amount=value, credit=an))
    return postings


def undo(db: Session, organization_id: int, year: int, month: int, legal_entity_id: int | None = None) -> int:
    """Удалить операции финансового результата месяца (и реформации, если месяц — декабрь)."""
    query = select(ResultClosing).where(ResultClosing.organization_id == organization_id,
                                        ResultClosing.year == year, ResultClosing.month == month)
    if legal_entity_id is not None:
        query = query.where(ResultClosing.legal_entity_id == legal_entity_id)
    removed = 0
    for closing in db.scalars(query):
        op = db.get(Operation, closing.operation_id)
        db.delete(closing)
        if op is not None:
            db.delete(op)
        removed += 1
    db.flush()
    return removed


def close_month(db: Session, organization_id: int, year: int, month: int, user: User) -> list[ResultClosing]:
    """Определить финансовый результат месяца по всем юрлицам; прежние операции месяца пересоздаются."""
    end = month_end(year, month)
    created = []
    for entity in db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)
                             .order_by(LegalEntity.short_name)):
        undo(db, organization_id, year, month, entity.id)
        steps = [("month", _closing_postings, f"Закрытие 26, 44, 90, 91 на 99 за {MONTHS_GENITIVE[month - 1]} {year}")]
        if month == 12:
            steps.append(("year", _reformation_postings, f"Реформация баланса за {year} год: 99 → 84"))
        for kind, build, description in steps:
            postings = build(db, organization_id, entity.id, end)
            if not postings:
                continue
            body = OperationIn(legal_entity_id=entity.id, op_date=end, description=description, postings=postings)
            try:
                op = journal.create_posted(db, organization_id, body, user)
            except JournalError as exc:
                raise ResultError(f"{entity.short_name}: проводки не прошли проверку журнала: {exc}") from None
            closing = ResultClosing(organization_id=organization_id, legal_entity_id=entity.id, year=year,
                                    month=month, kind=kind, operation_id=op.id, created_by=user.id)
            db.add(closing)
            db.flush()
            created.append(closing)
    return created


def pending(db: Session, organization_id: int, year: int, month: int) -> list[str]:
    """Юрлица, у которых на конец месяца 26, 44, 90, 91 (в декабре — и 99) не равны нулю."""
    end = month_end(year, month)
    roots = COST_ROOTS + RESULT_ROOTS + (("99",) if month == 12 else ())
    names = []
    for entity in db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)
                             .order_by(LegalEntity.short_name)):
        lines = select_lines(organization_id, account_ids=_ids(db, roots), date_to=end,
                             legal_entity_id=entity.id).subquery()
        signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
        left = db.execute(select(lines.c.account_id).group_by(lines.c.account_id, lines.c.cfo, lines.c.pl_item)
                          .having(func.sum(signed) != 0).limit(1)).first()
        if left:
            names.append(entity.short_name)
    return names


def operations_of(db: Session, organization_id: int, year: int, month: int) -> list[tuple[ResultClosing, Operation]]:
    return list(db.execute(select(ResultClosing, Operation).join(Operation, Operation.id == ResultClosing.operation_id)
                           .where(ResultClosing.organization_id == organization_id, ResultClosing.year == year,
                                  ResultClosing.month == month)
                           .order_by(ResultClosing.legal_entity_id, ResultClosing.kind)).all())


