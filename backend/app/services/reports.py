"""Отчёты (docs/TZ.md, раздел 12). Все цифры — из select_lines() (CLAUDE.md, правило 6):
расшифровка (lines) отбирает проводки той же функцией с теми же условиями, поэтому её итог
всегда равен цифре отчёта.
"""

import datetime as dt
from dataclasses import dataclass, field, replace
from decimal import Decimal

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Article,
    BankAccount,
    Cfo,
    Contract,
    Counterparty,
    Employee,
    FixedAsset,
    Nomenclature,
    Operation,
    Unit,
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
    # План вместо факта: месяц «ГГГГ-ММ» → версия бюджета, план которой берётся за этот месяц
    plan_versions: tuple[tuple[str, int], ...] | None = None

    @property
    def statuses(self) -> tuple[str, ...]:
        if self.plan_versions is not None:
            return ("plan",)
        return ("posted", "draft") if self.with_drafts else ("posted",)

    def lines(self, *, date_from: dt.date | None = None, date_to: dt.date | None = None, **conditions):
        """select_lines с фильтрами отчёта; период задаёт вызывающий."""
        query = select_lines(
            self.organization_id, statuses=self.statuses, date_from=date_from, date_to=date_to,
            legal_entity_id=self.legal_entity_id, cfo_branch=self.cfo_id,
            budget_version_ids=None if self.plan_versions is None else sorted({v for _, v in self.plan_versions}),
            **conditions,
        )
        if self.plan_versions is not None:
            c = query.selected_columns
            query = query.where(or_(false(), *(
                and_(c.budget_version_id == version, func.to_char(c.op_date, "YYYY-MM") == month)
                for month, version in self.plan_versions
            )))
        return query


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
        "fixed_asset": (FixedAsset, FixedAsset.name),
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
class ColumnsResult:
    columns: list[Column]
    money_account_ids: list[int]
    rows: list[ReportRow]


def money_account_ids(db: Session) -> list[int]:
    return list(db.scalars(select(Account.id).where(Account.code.in_(MONEY_ROOTS))))


def dds(db: Session, f: ReportFilter, include_all: bool = False) -> ColumnsResult:
    """include_all — показать все статьи ДДС (для план/факта: одинаковые строки у плана и факта)."""
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
    if include_all:
        for a in db.scalars(select(Article).where(Article.is_active, Article.is_cash_flow, ~Article.is_transfer)):
            articles.setdefault(a.id, a)
            by_article.setdefault(a.id, empty())
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
    return ColumnsResult(columns=columns, money_account_ids=money_ids, rows=rows)


# --- ОПиУ ---

PL_SECTIONS = [
    ("revenue", "Выручка"),
    ("cost", "Себестоимость продаж"),
    ("gross", "Валовая прибыль"),
    ("selling", "Коммерческие расходы"),
    ("admin", "Управленческие расходы"),
    ("operating", "Операционная прибыль"),
    ("other_income", "Прочие доходы"),
    ("other_expense", "Прочие расходы"),
    ("before_tax", "Прибыль до налогообложения"),
    ("income_tax", "Налог на прибыль"),
    ("net", "Чистая прибыль"),
]
# Итоговые строки: сумма разделов, начиная сверху
PL_TOTALS = {
    "gross": ["revenue", "cost"],
    "operating": ["revenue", "cost", "selling", "admin"],
    "before_tax": ["revenue", "cost", "selling", "admin", "other_income", "other_expense"],
    "net": ["revenue", "cost", "selling", "admin", "other_income", "other_expense", "income_tax"],
}


def pl(db: Session, f: ReportFilter, include_all: bool = False) -> ColumnsResult:
    """ОПиУ по аналитике «Статья ОПиУ»: по кредиту — доход (+), по дебету — расход (−).
    Перенос затрат между счетами с этой аналитикой (20 → 90.02, 90 → 99) взаимно гасится;
    реформация (99 → 84) в ОПиУ не попадает."""
    columns = month_columns(f.date_from, f.date_to)
    reformation = list(db.scalars(select(Account.id).where(Account.code == "84")))
    lines = f.lines(date_from=f.date_from, date_to=f.date_to, opening=False,
                    exclude_corr_account_ids=reformation).subquery()
    marked = select(
        func.to_char(lines.c.op_date, "YYYY-MM").label("month"), lines.c.pl_item,
        case((lines.c.side == "credit", lines.c.amount_rub), else_=-lines.c.amount_rub).label("signed"),
    ).where(lines.c.pl_item.is_not(None)).subquery()
    grouped = db.execute(
        select(marked.c.month, marked.c.pl_item, func.coalesce(func.sum(marked.c.signed), 0))
        .group_by(marked.c.month, marked.c.pl_item)
    ).all()

    def empty() -> dict[str, Decimal]:
        return {c.key: ZERO for c in columns}

    by_article: dict[int, dict[str, Decimal]] = {}
    for key, article_id, amount in grouped:
        values = by_article.setdefault(article_id, empty())
        values[key] += Decimal(amount) + ZERO
        values["total"] += Decimal(amount) + ZERO

    if include_all:
        for article_id in db.scalars(select(Article.id).where(Article.is_active, Article.is_pl)):
            by_article.setdefault(article_id, {c.key: ZERO for c in columns})
    articles = {a.id: a for a in db.scalars(select(Article).where(Article.id.in_(list(by_article))))}
    sections = {key: empty() for key, _ in PL_SECTIONS}
    items: dict[str, list[ReportRow]] = {key: [] for key, _ in PL_SECTIONS}
    for article_id, values in by_article.items():
        if not any(values.values()) and not include_all:
            continue  # перенос затрат внутри статьи (20 → 90.02) погасился
        article = articles[article_id]
        section = article.pl_section or "other_expense"
        items[section].append(ReportRow(f"art:{article_id}", article.name, 1, "item", values,
                                        {"period": "turnover", "a": [f"pl_item:{article_id}"],
                                         "exclude_corr_ids": reformation}))
        for k, v in values.items():
            sections[section][k] += v

    rows: list[ReportRow] = []
    for key, title in PL_SECTIONS:
        if key in PL_TOTALS:
            values = {c.key: sum((sections[s][c.key] for s in PL_TOTALS[key]), ZERO) for c in columns}
            rows.append(ReportRow(key, title, 0, "total", values))
        else:
            rows.append(ReportRow(key, title, 0, "section", sections[key]))
            rows += sorted(items[key], key=lambda r: r.title.lower())
    return ColumnsResult(columns=columns, money_account_ids=[], rows=rows)


# --- Управленческий баланс ---

# Строки баланса: ключ → (раздел, название). Порядок — как в отчёте.
BALANCE_LINES = {
    "fixed": ("asset", "Основные средства и нематериальные активы"),
    "investments": ("asset", "Финансовые вложения"),
    "stock": ("asset", "Запасы"),
    "vat": ("asset", "НДС по приобретённым ценностям"),
    "money": ("asset", "Денежные средства"),
    "customers_dr": ("asset", "Дебиторская задолженность покупателей"),
    "suppliers_dr": ("asset", "Авансы, выданные поставщикам"),
    "taxes_dr": ("asset", "Переплата по налогам и взносам"),
    "staff_dr": ("asset", "Расчёты с персоналом (дебет)"),
    "loans_dr": ("asset", "Переплата по кредитам и займам"),
    "other_dr": ("asset", "Прочая дебиторская задолженность"),
    "deferred": ("asset", "Расходы будущих периодов"),
    "other_assets": ("asset", "Прочие активы"),
    "share_capital": ("capital", "Уставный капитал"),
    "reserves": ("capital", "Резервный и добавочный капитал"),
    "retained": ("capital", "Нераспределённая прибыль прошлых лет"),
    "current_profit": ("capital", "Прибыль (убыток) текущего периода"),
    "loans_cr": ("liability", "Кредиты и займы"),
    "suppliers_cr": ("liability", "Кредиторская задолженность поставщикам"),
    "customers_cr": ("liability", "Авансы, полученные от покупателей"),
    "taxes_cr": ("liability", "Задолженность по налогам и взносам"),
    "staff_cr": ("liability", "Задолженность перед персоналом"),
    "other_cr": ("liability", "Прочая кредиторская задолженность"),
    "deferred_income": ("liability", "Доходы будущих периодов и резервы"),
    "other_liabilities": ("liability", "Прочие обязательства"),
    "unbalanced": ("liability", "Несведённые начальные остатки (счёт 00)"),
}
# Счета, сальдо которых целиком идёт в одну строку (по коду счёта верхнего уровня)
WHOLE = {
    **dict.fromkeys(["01", "02", "03", "04", "05", "07", "08"], "fixed"),
    "58": "investments",
    **dict.fromkeys(["10", "11", "15", "16", "41", "43", "45"], "stock"),
    "19": "vat",
    **dict.fromkeys(["50", "51", "52", "55", "57"], "money"),
    "97": "deferred",
    "80": "share_capital",
    **dict.fromkeys(["82", "83"], "reserves"),
    "84": "retained",
    # счета затрат и финансового результата до закрытия периода — прибыль текущего периода
    **dict.fromkeys(["20", "23", "25", "26", "28", "29", "44", "90", "91", "94", "99"], "current_profit"),
    **dict.fromkeys(["96", "98"], "deferred_income"),
    "00": "unbalanced",
}
# Расчётные счета: сальдо развёрнуто по контрагенту / договору / сотруднику; дебетовое — актив, кредитовое — пассив
SPLIT = {
    "62": ("customers_dr", "customers_cr"),
    "60": ("suppliers_dr", "suppliers_cr"),
    "66": ("loans_dr", "loans_cr"), "67": ("loans_dr", "loans_cr"),
    "68": ("taxes_dr", "taxes_cr"), "69": ("taxes_dr", "taxes_cr"),
    "70": ("staff_dr", "staff_cr"), "71": ("staff_dr", "staff_cr"), "73": ("staff_dr", "staff_cr"),
}
SPLIT_KINDS = ("counterparty", "contract", "employee")


def _balance_line(account: Account, net: Decimal) -> str:
    top = account.code.split(".")[0]
    if top in WHOLE:
        return WHOLE[top]
    if top in SPLIT:
        return SPLIT[top][0 if net > 0 else 1]
    if account.account_type == "active":
        return "other_assets"
    if account.account_type == "passive":
        return "other_liabilities"
    return "other_dr" if net > 0 else "other_cr"


def _balance_groups(db: Session, f: ReportFilter):
    """Сальдо (дебет − кредит) на начало и на конец периода по счёту и аналитике расчётов."""
    lines = f.lines(date_to=f.date_to).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    keys = [lines.c.account_id, *(getattr(lines.c, k) for k in SPLIT_KINDS)]
    return db.execute(
        select(*keys,
               func.coalesce(func.sum(case((lines.c.op_date < f.date_from, signed))), 0),
               func.coalesce(func.sum(signed), 0))
        .group_by(*keys)
    ).all()


def balance_columns(f: ReportFilter) -> list[Column]:
    start = f.date_from - dt.timedelta(days=1)
    return [Column("opening", f"На {start:%d.%m.%Y}", start, start),
            Column("closing", f"На {f.date_to:%d.%m.%Y}", f.date_to, f.date_to)]


def balance(db: Session, f: ReportFilter) -> ColumnsResult:
    columns = balance_columns(f)
    accounts = {a.id: a for a in db.scalars(select(Account))}
    values = {key: {"opening": ZERO, "closing": ZERO} for key in BALANCE_LINES}
    whole_accounts: dict[str, set[int]] = {}
    for account_id, _cp, _contract, _employee, opening, closing in _balance_groups(db, f):
        account = accounts[account_id]
        for col, net in (("opening", Decimal(opening) + ZERO), ("closing", Decimal(closing) + ZERO)):
            if net == 0:
                continue
            line = _balance_line(account, net)
            values[line][col] += net if BALANCE_LINES[line][0] == "asset" else -net
            if line in WHOLE.values() or line in ("other_assets", "other_liabilities"):
                whole_accounts.setdefault(line, set()).add(account_id)

    def row(key: str) -> ReportRow:
        title = BALANCE_LINES[key][1]
        if key in whole_accounts:
            # строка из целых счетов: расшифровка — проводки этих счетов на дату
            drill = {"period": "closing", "account_id": sorted(whole_accounts[key])}
        else:
            drill = {"details": key}  # развёрнутое сальдо: сначала список контрагентов
        return ReportRow(key, title, 1, "item", values[key], drill)

    def total(key: str, title: str, keys: list[str], kind: str = "total") -> ReportRow:
        return ReportRow(key, title, 0, kind, {c.key: sum((values[k][c.key] for k in keys), ZERO) for c in columns})

    def section(name: str) -> list[str]:
        return [k for k, (s, _) in BALANCE_LINES.items() if s == name and any(values[k].values())]

    assets, capital, liabilities = section("asset"), section("capital"), section("liability")
    rows = [ReportRow("title:asset", "АКТИВ", 0, "title", {}), *(row(k) for k in assets),
            total("total_assets", "Итого актив", assets),
            ReportRow("title:passive", "ПАССИВ", 0, "title", {}),
            total("capital", "Капитал", capital, "section"), *(row(k) for k in capital),
            total("liabilities", "Обязательства", liabilities, "section"), *(row(k) for k in liabilities),
            total("total_passive", "Итого пассив", capital + liabilities)]
    return ColumnsResult(columns=columns, money_account_ids=[], rows=rows)


@dataclass
class BalanceDetail:
    account_id: int
    account: str
    analytics: dict[str, int | None]
    name: str
    amount: Decimal


def balance_details(db: Session, f: ReportFilter, line: str) -> list[BalanceDetail]:
    """Из чего сложилась строка с развёрнутым сальдо на конец периода: счёт + контрагент / договор / сотрудник."""
    accounts = {a.id: a for a in db.scalars(select(Account))}
    found = []
    for account_id, cp, contract, employee, _opening, closing in _balance_groups(db, f):
        net = Decimal(closing) + ZERO
        if net == 0 or _balance_line(accounts[account_id], net) != line:
            continue
        found.append((accounts[account_id], {"counterparty": cp, "contract": contract, "employee": employee}, net))
    names = {k: analytic_names(db, k, [a[k] for _, a, _ in found]) for k in SPLIT_KINDS}
    sign = 1 if BALANCE_LINES[line][0] == "asset" else -1
    result = [
        BalanceDetail(
            account.id, account.code, analytics,
            ", ".join(names[k][v] for k, v in analytics.items() if v is not None) or "— без аналитики —",
            net * sign,
        )
        for account, analytics, net in found
    ]
    return sorted(result, key=lambda d: (-d.amount, d.account, d.name))


# --- Дебиторская и кредиторская задолженность ---

DEBT_ROOTS = ("60", "62", "66", "67", "71", "76")
DEBT_COLUMNS = ("receivable", "advance_paid", "payable", "advance_received")


def _debt_column(code: str, net: Decimal) -> str:
    top = code.split(".")[0]
    if top == "62":
        return "receivable" if net > 0 else "advance_received"
    if top == "60":
        return "advance_paid" if net > 0 else "payable"
    return "receivable" if net > 0 else "payable"


@dataclass
class DebtRow:
    key: str
    parent: str | None
    title: str
    account: str
    values: dict[str, Decimal]
    drill: dict


def debts(db: Session, f: ReportFilter) -> tuple[list[DebtRow], dict[str, Decimal]]:
    """Задолженность на конец периода по контрагентам (подотчётным лицам) с раскрытием по счетам и договорам.
    Сальдо каждой группы «счёт + контрагент + договор + сотрудник» — в одной из колонок по знаку."""
    root_ids = list(db.scalars(select(Account.id).where(Account.code.in_(DEBT_ROOTS))))
    accounts = {a.id: a for a in db.scalars(select(Account))}
    lines = f.lines(date_to=f.date_to, account_ids=root_ids).subquery()
    signed = case((lines.c.side == "debit", lines.c.amount_rub), else_=-lines.c.amount_rub)
    keys = [lines.c.account_id, lines.c.counterparty, lines.c.contract, lines.c.employee]
    groups = db.execute(
        select(*keys, func.coalesce(func.sum(case((lines.c.op_date < f.date_from, signed))), 0),
               func.coalesce(func.sum(signed), 0)).group_by(*keys)
    ).all()

    names = {k: analytic_names(db, k, [g[i] for g in groups]) for i, k in
             ((1, "counterparty"), (2, "contract"), (3, "employee"))}

    def empty() -> dict[str, Decimal]:
        return {k: ZERO for k in (*DEBT_COLUMNS, "opening", "saldo")}

    parents: dict[str, DebtRow] = {}
    children: list[DebtRow] = []
    for account_id, cp, contract, employee, opening, closing in groups:
        opening, closing = Decimal(opening) + ZERO, Decimal(closing) + ZERO
        if opening == 0 and closing == 0:
            continue
        if cp is not None:
            pkey, title, a_parent = f"cp:{cp}", names["counterparty"].get(cp, "?"), [f"counterparty:{cp}"]
        elif employee is not None:
            pkey, title = f"emp:{employee}", f"{names['employee'].get(employee, '?')} (сотрудник)"
            a_parent = [f"counterparty:none", f"employee:{employee}"]
        else:
            pkey, title, a_parent = "none", "Без контрагента", ["counterparty:none", "employee:none"]
        parent = parents.setdefault(pkey, DebtRow(pkey, None, title, "", empty(),
                                                  {"account_id": root_ids, "a": a_parent}))
        code = accounts[account_id].code
        values = empty()
        if closing != 0:
            values[_debt_column(code, closing)] = abs(closing)
        values["opening"], values["saldo"] = opening, closing
        detail = names["contract"].get(contract, "без договора") if contract is not None else "без договора"
        if employee is not None and cp is not None:
            detail += f", {names['employee'].get(employee, '?')}"
        a = [f"{k}:{'none' if v is None else v}" for k, v in
             (("counterparty", cp), ("contract", contract), ("employee", employee))]
        children.append(DebtRow(f"{pkey}/{account_id}/{contract}/{employee}", pkey, detail, code, values,
                                {"account_id": [account_id], "a": a}))
        for k, v in values.items():
            parent.values[k] += v

    total = empty()
    for p in parents.values():
        for k, v in p.values.items():
            total[k] += v
    ordered = []
    for p in sorted(parents.values(), key=lambda r: (r.key == "none", r.title.lower())):
        ordered.append(p)
        ordered += sorted((c for c in children if c.parent == p.key), key=lambda c: (c.account, c.title))
    return ordered, total


# --- План / факт ---


def actual_plan_versions(db: Session, organization_id: int, months: list[str], kind: str) -> dict[str, int]:
    """Для каждого месяца — утверждённая версия самого подробного бюджета (месяц, иначе квартал, иначе год),
    в которой есть план этого вида (ОПиУ или ДДС)."""
    from app.models import Budget, BudgetVersion

    with_plan = select(Operation.budget_version_id).where(Operation.plan_kind == kind).distinct()
    approved = {
        (b.kind, b.year, b.period): v.id
        for b, v in db.execute(
            select(Budget, BudgetVersion).join(BudgetVersion, BudgetVersion.budget_id == Budget.id)
            .where(Budget.organization_id == organization_id, BudgetVersion.status == "approved",
                   BudgetVersion.id.in_(with_plan))
        ).all()
    }
    result = {}
    for key in months:
        year, month = int(key[:4]), int(key[5:])
        for candidate in (("month", year, month), ("quarter", year, (month - 1) // 3 + 1), ("year", year, 1)):
            if candidate in approved:
                result[key] = approved[candidate]
                break
    return result


def version_plan_months(db: Session, version_id: int, months: list[str]) -> dict[str, int]:
    """Выбранная версия — на все месяцы её бюджета, попавшие в период."""
    from app.models import Budget, BudgetVersion

    budget = db.get(Budget, db.get(BudgetVersion, version_id).budget_id)
    covered = {f"{budget.year}-{m:02d}" for m in budget.months}
    return {k: version_id for k in months if k in covered}


@dataclass
class PlanFactResult:
    columns: list[Column]
    plan_versions: dict[str, int]
    money_account_ids: list[int]
    rows: list[dict]




def plan_fact(db: Session, f: ReportFilter, kind: str, version_id: int | None = None) -> PlanFactResult:
    """План и факт одними и теми же отчётами (ОПиУ или ДДС): факт — проведённые операции, план — плановые
    операции выбранной версии (или актуального утверждённого бюджета по каждому месяцу)."""
    columns = month_columns(f.date_from, f.date_to)
    month_keys = [c.key for c in columns[:-1]]
    versions = (version_plan_months(db, version_id, month_keys) if version_id
                else actual_plan_versions(db, f.organization_id, month_keys, kind))
    plan_filter = replace(f, with_drafts=False, plan_versions=tuple(sorted(versions.items())))
    build = pl if kind == "pl" else dds
    fact = build(db, f, include_all=True)
    plan = build(db, plan_filter, include_all=True)
    plan_rows = {r.key: r for r in plan.rows}

    def cell(p: Decimal, fa: Decimal) -> dict:
        pct = None if p == 0 else (fa / p * 100).quantize(Decimal("0.1")) + 0  # + 0: без «-0.0»
        return {"plan": p, "fact": fa, "dev": fa - p, "pct": pct}

    rows = []
    for r in fact.rows:
        if kind == "cf" and r.key in ("opening", "closing", "moves", "opening_entries"):
            continue  # остатков и перемещений в плане ДДС нет
        p = plan_rows.get(r.key)
        values = {c.key: cell(p.values.get(c.key, ZERO) if p and p.values else ZERO,
                              r.values.get(c.key, ZERO) if r.values else ZERO) for c in columns}
        rows.append({"key": r.key, "title": r.title, "level": r.level, "kind": r.kind, "values": values,
                     "drill": r.drill, "empty": r.kind == "item" and not any(
                         v["plan"] or v["fact"] for v in values.values())})
    # статьи без плана и факта не показываем; группы ДДС без статей — тоже
    rows = [r for r in rows if not r["empty"]]
    if kind == "cf":
        rows = _prune_cash_sections(rows)
    for r in rows:
        r.pop("empty")
    return PlanFactResult(columns=columns, plan_versions=versions, money_account_ids=fact.money_account_ids, rows=rows)


def _prune_cash_sections(rows: list[dict]) -> list[dict]:
    """ДДС: убрать группы «Поступления / Выплаты» и виды деятельности, в которых не осталось статей."""
    def has_items(start: int, stop_kinds: tuple[str, ...]) -> bool:
        for r in rows[start + 1:]:
            if r["kind"] in stop_kinds:
                return False
            if r["kind"] == "item" and r["level"] == 2:
                return True
        return False

    kept, dropped_activities = [], set()
    for i, r in enumerate(rows):
        if r["kind"] == "group" and not has_items(i, ("group", "section", "title")):
            continue
        if r["kind"] == "title" and not has_items(i, ("section",)):
            dropped_activities.add(r["key"].split(":", 1)[1])
            continue
        if r["kind"] == "section" and r["key"].split(":", 1)[1] in dropped_activities:
            continue
        kept.append(r)
    return kept


# --- Склад: остатки и обороты по номенклатуре ---

STOCK_ROOTS = ("10", "41", "43")
STOCK_FIELDS = ("opening_qty", "opening_sum", "in_qty", "in_sum", "out_qty", "out_sum", "closing_qty", "closing_sum")
QTY_ZERO = Decimal("0.000")


@dataclass
class StockRow:
    key: str
    parent: str | None
    title: str
    account: str
    unit: str
    values: dict[str, Decimal]
    drill: dict | None


def stock(db: Session, f: ReportFilter, account_id: int | None = None,
          warehouse_id: int | None = None) -> tuple[list[StockRow], dict[str, Decimal]]:
    """Остатки и обороты (количество и сумма) по складам и номенклатуре; приход — дебет, расход — кредит."""
    roots = [account_id] if account_id else list(db.scalars(select(Account.id).where(Account.code.in_(STOCK_ROOTS))))
    accounts = {a.id: a for a in db.scalars(select(Account))}
    analytics = {"warehouse": warehouse_id} if warehouse_id else None
    lines = f.lines(date_to=f.date_to, account_ids=roots, analytics=analytics).subquery()
    before = lines.c.op_date < f.date_from
    debit = lines.c.side == "debit"
    qty = func.coalesce(lines.c.quantity, 0)

    def total(expr, cond):
        return func.coalesce(func.sum(case((cond, expr), else_=0)), 0)

    keys = [lines.c.warehouse, lines.c.nomenclature, lines.c.account_id]
    groups = db.execute(select(
        *keys,
        total(qty, before & debit) - total(qty, before & ~debit),
        total(lines.c.amount_rub, before & debit) - total(lines.c.amount_rub, before & ~debit),
        total(qty, ~before & debit), total(lines.c.amount_rub, ~before & debit),
        total(qty, ~before & ~debit), total(lines.c.amount_rub, ~before & ~debit),
    ).group_by(*keys)).all()

    nomenclature_ids = [g[1] for g in groups if g[1] is not None]
    units = dict(db.execute(select(Nomenclature.id, Unit.short_name).join(Unit, Unit.id == Nomenclature.unit_id)
                            .where(Nomenclature.id.in_(nomenclature_ids))).all())
    names = {k: analytic_names(db, k, [g[i] for g in groups]) for i, k in ((0, "warehouse"), (1, "nomenclature"))}

    def empty() -> dict[str, Decimal]:
        return {k: (QTY_ZERO if k.endswith("qty") else ZERO) for k in STOCK_FIELDS}

    parents: dict[str, StockRow] = {}
    children: list[StockRow] = []
    for warehouse, item, acc_id, o_qty, o_sum, i_qty, i_sum, x_qty, x_sum in groups:
        values = {"opening_qty": Decimal(o_qty), "opening_sum": Decimal(o_sum), "in_qty": Decimal(i_qty),
                  "in_sum": Decimal(i_sum), "out_qty": Decimal(x_qty), "out_sum": Decimal(x_sum)}
        values["closing_qty"] = values["opening_qty"] + values["in_qty"] - values["out_qty"]
        values["closing_sum"] = values["opening_sum"] + values["in_sum"] - values["out_sum"]
        values = {k: v + (QTY_ZERO if k.endswith("qty") else ZERO) for k, v in values.items()}
        if not any(values.values()):
            continue
        pkey = f"wh:{warehouse}" if warehouse is not None else "wh:none"
        a_wh = f"warehouse:{'none' if warehouse is None else warehouse}"
        parent = parents.setdefault(pkey, StockRow(
            pkey, None, names["warehouse"].get(warehouse, "?") if warehouse is not None else "Без склада", "", "",
            empty(), {"account_id": roots, "a": [a_wh]}))
        title = names["nomenclature"].get(item, "?") if item is not None else "Без номенклатуры"
        children.append(StockRow(f"{pkey}/{item}/{acc_id}", pkey, title, accounts[acc_id].code, units.get(item, ""),
                                 values, {"account_id": [acc_id],
                                          "a": [a_wh, f"nomenclature:{'none' if item is None else item}"]}))
        for k, v in values.items():
            parent.values[k] += v

    total_values = empty()
    for p in parents.values():
        for k, v in p.values.items():
            total_values[k] += v
    rows: list[StockRow] = []
    for pkey, parent in sorted(parents.items(), key=lambda kv: (kv[0] == "wh:none", kv[1].title.lower())):
        rows.append(parent)
        rows += sorted((c for c in children if c.parent == pkey), key=lambda c: (c.title.lower(), c.account))
    return rows, total_values

