"""Отчёты (docs/TZ.md, раздел 12). Все цифры — из select_lines() (CLAUDE.md, правило 6):
расшифровка (lines) отбирает проводки той же функцией с теми же условиями, поэтому её итог
всегда равен цифре отчёта.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Article,
    BankAccount,
    Cfo,
    Contract,
    Counterparty,
    Employee,
    Nomenclature,
    Operation,
    Warehouse,
)
from app.models.journal import ANALYTIC_COLUMNS
from app.services.ledger import select_lines, subtree

ZERO = Decimal("0.00")
DRILL_LIMIT = 1000


@dataclass(frozen=True)
class ReportFilter:
    """Общие фильтры отчётов."""

    organization_id: int
    date_from: dt.date
    date_to: dt.date
    legal_entity_id: int | None = None
    cfo_id: int | None = None  # ЦФО вместе с веткой подчинённых
    with_drafts: bool = False

    @property
    def statuses(self) -> tuple[str, ...]:
        return ("posted", "draft") if self.with_drafts else ("posted",)

    def lines(self, *, date_from: dt.date | None = None, date_to: dt.date | None = None, **conditions):
        """select_lines с фильтрами отчёта; период задаёт вызывающий."""
        return select_lines(
            self.organization_id, statuses=self.statuses, date_from=date_from, date_to=date_to,
            legal_entity_id=self.legal_entity_id, cfo_branch=self.cfo_id, **conditions,
        )


def _split(net: Decimal) -> tuple[Decimal, Decimal]:
    """Свёрнутое сальдо: положительное — в дебет, отрицательное — в кредит."""
    return (net, ZERO) if net > 0 else (ZERO, -net)


@dataclass
class Balances:
    opening: Decimal = ZERO  # сальдо на начало: дебет минус кредит
    debit: Decimal = ZERO  # обороты за период
    credit: Decimal = ZERO

    @property
    def closing(self) -> Decimal:
        return self.opening + self.debit - self.credit

    def add(self, other: "Balances") -> None:
        self.opening += other.opening
        self.debit += other.debit
        self.credit += other.credit

    def is_empty(self) -> bool:
        return self.opening == 0 and self.debit == 0 and self.credit == 0

    def out(self) -> dict:
        od, ok = _split(self.opening)
        cd, ck = _split(self.closing)
        return {"opening_debit": od, "opening_credit": ok, "debit": self.debit, "credit": self.credit,
                "closing_debit": cd, "closing_credit": ck}


def _grouped_balances(db: Session, f: ReportFilter, group_column: str, **conditions) -> dict:
    """Сальдо на начало и обороты за период по значениям колонки строки проводки."""
    lines = f.lines(date_to=f.date_to, **conditions).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    before = lines.c.op_date < f.date_from
    key = getattr(lines.c, group_column)
    rows = db.execute(
        select(
            key,
            func.coalesce(func.sum(case((before, signed))), 0),
            func.coalesce(func.sum(case((~before & (lines.c.side == "debit"), lines.c.amount_rub))), 0),
            func.coalesce(func.sum(case((~before & (lines.c.side == "credit"), lines.c.amount_rub))), 0),
        ).group_by(key)
    )
    return {k: Balances(Decimal(o) + ZERO, Decimal(d) + ZERO, Decimal(c) + ZERO) for k, o, d, c in rows}


# --- ОСВ ---


@dataclass
class OsvRow:
    account_id: int
    code: str
    name: str
    parent_id: int | None
    balances: Balances
    has_analytics: bool


@dataclass
class OsvResult:
    rows: list[OsvRow]
    total: Balances = field(default_factory=Balances)
    total_out: dict = field(default_factory=dict)


def osv(db: Session, f: ReportFilter) -> OsvResult:
    by_account = _grouped_balances(db, f, "account_id")
    accounts = {a.id: a for a in db.scalars(select(Account))}
    totals: dict[int, Balances] = {}
    for account_id, bal in by_account.items():
        node = accounts.get(account_id)
        while node is not None:  # счёт и все его родители
            totals.setdefault(node.id, Balances()).add(bal)
            node = accounts.get(node.parent_id) if node.parent_id else None

    rows = [
        OsvRow(a.id, a.code, a.name, a.parent_id, totals[a.id], bool(a.analytics))
        for a in sorted(accounts.values(), key=lambda a: a.code)
        if a.id in totals and not totals[a.id].is_empty()
    ]
    # итог — по счетам верхнего уровня, сальдо каждого счёта свёрнуто
    total = {k: ZERO for k in Balances().out()}
    for row in rows:
        if row.parent_id is None:
            for k, v in row.balances.out().items():
                total[k] += v
    return OsvResult(rows=rows, total_out=total)


# --- Разрез по аналитике ---


def analytic_names(db: Session, kind: str, ids) -> dict[int, str]:
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    if kind == "counterparty":
        return dict(db.execute(select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(ids))).all())
    if kind == "contract":
        rows = db.execute(select(Contract.id, Contract.number, Contract.contract_date).where(Contract.id.in_(ids)))
        return {i: f"№ {n}" + (f" от {d:%d.%m.%Y}" if d else "") for i, n, d in rows}
    if kind == "bank_account":
        rows = db.execute(select(BankAccount.id, BankAccount.name, BankAccount.number).where(BankAccount.id.in_(ids)))
        return {i: f"{n} ··{num[-4:]}" for i, n, num in rows}
    model, column = {
        "employee": (Employee, Employee.full_name),
        "nomenclature": (Nomenclature, Nomenclature.name),
        "warehouse": (Warehouse, Warehouse.name),
        "cfo": (Cfo, Cfo.name),
        "cash_flow_item": (Article, Article.name),
        "pl_item": (Article, Article.name),
    }[kind]
    return dict(db.execute(select(model.id, column).where(model.id.in_(ids))).all())


@dataclass
class DetailRow:
    value: int | None
    name: str
    balances: Balances


def osv_details(db: Session, f: ReportFilter, account_id: int, kind: str) -> tuple[list[DetailRow], dict]:
    grouped = _grouped_balances(db, f, kind, account_id=account_id)
    names = analytic_names(db, kind, grouped)
    rows = [DetailRow(value, names.get(value, "— не указано —") if value is not None else "— не указано —", bal)
            for value, bal in grouped.items() if not bal.is_empty()]
    rows.sort(key=lambda r: (r.value is None, r.name.lower()))
    total = {k: ZERO for k in Balances().out()}
    for row in rows:
        for k, v in row.balances.out().items():
            total[k] += v
    return rows, total


# --- Расшифровка: проводки, из которых сложилась цифра ---


@dataclass
class DrillResult:
    rows: list[dict]
    count: int
    debit: Decimal
    credit: Decimal


def drill(db: Session, f: ReportFilter, period: str, *, account_ids: list[int] | None = None,
          side: str | None = None, analytics: dict[str, int | None] | None = None,
          exclude_corr_account_ids: list[int] | None = None, opening: bool | None = None) -> DrillResult:
    """period: opening — до начала периода, turnover — за период, closing — по конец периода."""
    bounds = {
        "opening": (None, f.date_from - dt.timedelta(days=1)),
        "turnover": (f.date_from, f.date_to),
        "closing": (None, f.date_to),
    }[period]
    lines = f.lines(date_from=bounds[0], date_to=bounds[1], account_ids=account_ids or None, side=side,
                    analytics=analytics, exclude_corr_account_ids=exclude_corr_account_ids,
                    opening=opening).subquery()
    count, debit, credit = db.execute(select(
        func.count(),
        func.coalesce(func.sum(case((lines.c.side == "debit", lines.c.amount_rub))), 0),
        func.coalesce(func.sum(case((lines.c.side == "credit", lines.c.amount_rub))), 0),
    )).one()

    found = db.execute(
        select(lines, Operation.number, Operation.description)
        .join(Operation, Operation.id == lines.c.operation_id)
        .order_by(lines.c.op_date, Operation.number, lines.c.posting_id, lines.c.side.desc())
        .limit(DRILL_LIMIT)
    ).mappings().all()
    codes = dict(db.execute(select(Account.id, Account.code)).all())
    names = {kind: analytic_names(db, kind, [r[kind] for r in found]) for kind in ANALYTIC_COLUMNS}
    rows = []
    for r in found:
        details = [names[kind][r[kind]] for kind in ANALYTIC_COLUMNS if r[kind] is not None and r[kind] in names[kind]]
        rows.append({
            "operation_id": r["operation_id"], "number": r["number"], "op_date": r["op_date"], "status": r["status"],
            "description": r["description"], "account": codes.get(r["account_id"], ""),
            "corr_account": codes.get(r["corr_account_id"], ""), "side": r["side"],
            "debit": r["amount_rub"] if r["side"] == "debit" else None,
            "credit": r["amount_rub"] if r["side"] == "credit" else None,
            "analytics": ", ".join(details),
        })
    return DrillResult(rows=rows, count=count, debit=Decimal(debit) + ZERO, credit=Decimal(credit) + ZERO)


# --- ДДС ---

MONEY_ROOTS = ("50", "51", "52", "55")
ACTIVITIES = [("operating", "Операционная деятельность"), ("investing", "Инвестиционная деятельность"),
              ("financing", "Финансовая деятельность")]
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
          "Ноябрь", "Декабрь"]


@dataclass
class Column:
    key: str
    title: str
    date_from: dt.date
    date_to: dt.date


def month_columns(date_from: dt.date, date_to: dt.date) -> list[Column]:
    """Колонки по месяцам периода (первый и последний месяц — обрезаны границами периода) и «Итого»."""
    columns = []
    start = date_from
    while start <= date_to:
        next_month = (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        end = min(next_month - dt.timedelta(days=1), date_to)
        columns.append(Column(f"{start:%Y-%m}", f"{MONTHS[start.month - 1]} {start.year}", start, end))
        start = next_month
    columns.append(Column("total", "Итого", date_from, date_to))
    return columns


@dataclass
class ReportRow:
    key: str
    title: str
    level: int
    kind: str  # balance / section / group / item / total
    values: dict[str, Decimal]
    drill: dict | None = None  # условия расшифровки ячейки (дополняются счетами и датами колонки)


@dataclass
class DdsResult:
    columns: list[Column]
    money_account_ids: list[int]
    rows: list[ReportRow]


def money_account_ids(db: Session) -> list[int]:
    return list(db.scalars(select(Account.id).where(Account.code.in_(MONEY_ROOTS))))


def dds(db: Session, f: ReportFilter) -> DdsResult:
    columns = month_columns(f.date_from, f.date_to)
    months = columns[:-1]
    money_ids = money_account_ids(db)
    lines = f.lines(date_to=f.date_to, account_ids=money_ids).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    marked = select(
        case((lines.c.op_date >= f.date_from, func.to_char(lines.c.op_date, "YYYY-MM"))).label("month"),
        lines.c.cash_flow_item, lines.c.is_opening,
        # корреспондирующий счёт — тоже деньги: перемещение между своими счетами, не поток
        func.coalesce(lines.c.corr_account_id.in_(subtree(money_ids)), False).label("internal"),
        signed.label("signed"),
    ).subquery()
    grouped = db.execute(
        select(marked.c.month, marked.c.cash_flow_item, marked.c.is_opening, marked.c.internal,
               func.coalesce(func.sum(marked.c.signed), 0))
        .group_by(marked.c.month, marked.c.cash_flow_item, marked.c.is_opening, marked.c.internal)
    ).all()

    def empty() -> dict[str, Decimal]:
        return {c.key: ZERO for c in columns}

    before = ZERO
    by_article: dict[int | None, dict[str, Decimal]] = {}
    moves, opening_entries = empty(), empty()
    for key, article_id, is_opening, is_internal, amount in grouped:
        amount = Decimal(amount) + ZERO
        if key is None:
            before += amount
            continue
        if is_internal:
            target = moves
        elif is_opening:
            target = opening_entries
        else:
            target = by_article.setdefault(article_id, empty())
        target[key] += amount
        target["total"] += amount

    articles = {a.id: a for a in db.scalars(select(Article).where(Article.id.in_([k for k in by_article if k])))}
    flow_drill = {"period": "turnover", "exclude_corr": True, "opening": False}
    rows: list[ReportRow] = []
    net_flow = empty()

    def add(target: dict, values: dict) -> None:
        for k, v in values.items():
            target[k] += v

    for activity, title in ACTIVITIES:
        in_section = [a for a in articles.values() if a.cash_flow_activity == activity]
        if not in_section:
            continue
        section = ReportRow(f"act:{activity}", f"Сальдо: {title.lower()}", 0, "section", empty())
        section_rows = []
        for direction, group_title in (("inflow", "Поступления"), ("outflow", "Выплаты")):
            items = sorted((a for a in in_section if a.cash_flow_direction == direction), key=lambda a: a.name.lower())
            if not items:
                continue
            group = ReportRow(f"grp:{activity}:{direction}", group_title, 1, "group", empty())
            item_rows = []
            for a in items:
                item_rows.append(ReportRow(f"art:{a.id}", a.name, 2, "item", by_article[a.id],
                                           {**flow_drill, "a": [f"cash_flow_item:{a.id}"]}))
                add(group.values, by_article[a.id])
            section_rows += [group, *item_rows]
        add(section.values, {k: sum((r.values[k] for r in section_rows if r.kind == "group"), ZERO) for k in section.values})
        rows.append(ReportRow(f"title:{activity}", title, 0, "title", {}))
        rows += section_rows
        rows.append(section)
        add(net_flow, section.values)

    unknown = [k for k in by_article if k is None or k not in articles or not articles[k].cash_flow_activity]
    for k in unknown:
        name = "Статья ДДС не указана" if k is None else analytic_names(db, "cash_flow_item", [k]).get(k, "?")
        a_value = "none" if k is None else str(k)
        rows.append(ReportRow(f"art:{k}", name, 0, "item", by_article[k],
                              {**flow_drill, "a": [f"cash_flow_item:{a_value}"]}))
        add(net_flow, by_article[k])

    rows.append(ReportRow("net", "Чистый денежный поток", 0, "total", net_flow))
    if any(moves.values()):
        rows.append(ReportRow("moves", "Перемещения между своими счетами", 0, "item", moves))
    if any(opening_entries.values()):
        rows.append(ReportRow("opening_entries", "Ввод начальных остатков", 0, "item", opening_entries,
                              {"period": "turnover", "opening": True}))

    # остатки на начало и конец каждого месяца
    opening_row, closing_row = empty(), empty()
    balance = before
    for c in months:
        opening_row[c.key] = balance
        balance += net_flow[c.key] + moves[c.key] + opening_entries[c.key]
        closing_row[c.key] = balance
    opening_row["total"], closing_row["total"] = before, balance
    rows.insert(0, ReportRow("opening", "Остаток денег на начало", 0, "balance", opening_row, {"period": "opening"}))
    rows.append(ReportRow("closing", "Остаток денег на конец", 0, "balance", closing_row, {"period": "closing"}))
    return DdsResult(columns=columns, money_account_ids=money_ids, rows=rows)
