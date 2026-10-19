"""«Деньги»: остатки и движение по расчётным счетам и кассе (docs/TZ.md, раздел 10).

Считается только через единый отбор проводок select_lines() (правило 6), только проведённые.
Расчётные счета — счета 51, 52, 55 с аналитикой «Расчётный счёт»; касса — счёт 50 по ЦФО.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import Account, BankAccount, Cfo, Currency, Operation, Posting
from app.services.ledger import select_lines

BANK_ROOTS = ("51", "52", "55")
CASH_ROOT = "50"
ZERO = Decimal("0.00")


@dataclass
class Balance:
    opening: Decimal = ZERO
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO

    @property
    def closing(self) -> Decimal:
        return self.opening + self.inflow - self.outflow


def _account_ids(db: Session, codes: tuple[str, ...]) -> list[int]:
    return list(db.scalars(select(Account.id).where(Account.code.in_(codes))))


def _balances(db: Session, organization_id: int, roots: tuple[str, ...], key: str,
              date_from: dt.date, date_to: dt.date) -> dict[int | None, Balance]:
    """Остаток на начало, поступления и списания по каждому значению аналитики key."""
    lines = select_lines(organization_id, account_ids=_account_ids(db, roots), date_to=date_to).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    in_period = lines.c.op_date >= date_from
    query = select(
        getattr(lines.c, key),
        func.coalesce(func.sum(case((~in_period, signed), else_=0)), 0),
        func.coalesce(func.sum(case((in_period & (lines.c.side == "debit"), lines.c.amount_rub), else_=0)), 0),
        func.coalesce(func.sum(case((in_period & (lines.c.side == "credit"), lines.c.amount_rub), else_=0)), 0),
    ).group_by(getattr(lines.c, key))
    return {
        ref_id: Balance(round_money(Decimal(opening)), round_money(Decimal(inflow)), round_money(Decimal(outflow)))
        for ref_id, opening, inflow, outflow in db.execute(query)
    }


def money_overview(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date) -> list[dict]:
    """Строки «Деньги»: все активные расчётные счета (и счета с движением) + касса по ЦФО с движением."""
    rows = []
    bank = _balances(db, organization_id, BANK_ROOTS, "bank_account", date_from, date_to)
    accounts = db.execute(
        select(BankAccount, Currency.code)
        .join(Currency, Currency.id == BankAccount.currency_id)
        .where(BankAccount.organization_id == organization_id)
        .order_by(BankAccount.name)
    )
    for acc, currency in accounts:
        balance = bank.get(acc.id)
        if balance is None and not acc.is_active:
            continue
        balance = balance or Balance()
        rows.append({
            "kind": "bank", "bank_account_id": acc.id, "cfo_id": None, "name": acc.name,
            "number": acc.number, "legal_entity_id": acc.legal_entity_id, "currency": currency,
            "opening": balance.opening, "inflow": balance.inflow, "outflow": balance.outflow,
            "closing": balance.closing,
        })

    cash = _balances(db, organization_id, (CASH_ROOT,), "cfo", date_from, date_to)
    # кассы, добавленные на «Деньгах», видны и без движения
    for cfo_id in db.scalars(select(Cfo.id).where(Cfo.organization_id == organization_id, Cfo.has_cash, Cfo.is_active)):
        cash.setdefault(cfo_id, Balance())
    names = dict(db.execute(select(Cfo.id, Cfo.name).where(Cfo.id.in_([k for k in cash if k]))).all())
    for cfo_id, balance in sorted(cash.items(), key=lambda kv: names.get(kv[0], "")):
        rows.append({
            "kind": "cash", "bank_account_id": None, "cfo_id": cfo_id,
            "name": f"Касса — {names.get(cfo_id, 'без ЦФО')}", "number": None, "legal_entity_id": None,
            "currency": "RUB", "opening": balance.opening, "inflow": balance.inflow,
            "outflow": balance.outflow, "closing": balance.closing,
        })
    return rows


@dataclass
class Movements:
    opening: Decimal = ZERO
    rows: list[dict] = field(default_factory=list)

    @property
    def inflow(self) -> Decimal:
        return sum((r["inflow"] or ZERO for r in self.rows), ZERO)

    @property
    def outflow(self) -> Decimal:
        return sum((r["outflow"] or ZERO for r in self.rows), ZERO)

    @property
    def closing(self) -> Decimal:
        return self.opening + self.inflow - self.outflow


def money_movements(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date, *,
                    bank_account_id: int | None = None, cfo_id: int | None = None) -> Movements:
    """Движение по одному расчётному счёту или кассе ЦФО с остатком после каждой строки."""
    if bank_account_id is not None:
        roots, analytics = BANK_ROOTS, {"bank_account": bank_account_id}
    else:
        roots, analytics = (CASH_ROOT,), {"cfo": cfo_id}
    lines = select_lines(
        organization_id, account_ids=_account_ids(db, roots), date_to=date_to, analytics=analytics
    ).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    opening = db.scalar(select(func.coalesce(func.sum(signed), 0)).where(lines.c.op_date < date_from))
    result = Movements(opening=round_money(Decimal(opening)))

    # контрагент — с корреспондирующей стороны проводки
    counterparty = case(
        (lines.c.side == "debit", Posting.ct_counterparty_id), else_=Posting.dt_counterparty_id
    )
    query = (
        select(lines, Operation.number, Operation.description, counterparty.label("counterparty_id"))
        .join(Operation, Operation.id == lines.c.operation_id)
        .join(Posting, Posting.id == lines.c.posting_id)
        .where(lines.c.op_date >= date_from)
        .order_by(lines.c.op_date, Operation.number, Posting.line_no, lines.c.side)
    )
    balance = result.opening
    for row in db.execute(query).mappings():
        inflow = row["amount_rub"] if row["side"] == "debit" else None
        outflow = row["amount_rub"] if row["side"] == "credit" else None
        balance += (inflow or ZERO) - (outflow or ZERO)
        result.rows.append({
            "operation_id": row["operation_id"], "number": row["number"], "op_date": row["op_date"],
            "description": row["description"], "counterparty_id": row["counterparty_id"],
            "cash_flow_item": row["cash_flow_item"], "inflow": inflow, "outflow": outflow,
            "balance": balance,
        })
    return result


def opening_balance_check(db: Session, organization_id: int) -> Decimal:
    """Сальдо счёта 00 по проведённым операциям: должно быть 0, когда остатки введены полностью."""
    lines = select_lines(organization_id, account_ids=_account_ids(db, ("00",))).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    return round_money(Decimal(db.scalar(select(func.coalesce(func.sum(signed), 0)))))


def bank_balance(db: Session, organization_id: int, bank_account_id: int, on_date: dt.date) -> Decimal:
    """Остаток на расчётном счёте на конец дня on_date по проведённым операциям."""
    lines = select_lines(
        organization_id, account_ids=_account_ids(db, BANK_ROOTS), date_to=on_date,
        analytics={"bank_account": bank_account_id},
    ).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    return round_money(Decimal(db.scalar(select(func.coalesce(func.sum(signed), 0)))))
