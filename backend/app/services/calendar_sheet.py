"""Платёжный календарь по форме владельца — «лист 1» (docs/TZ.md, раздел 24, «Детали 14.6»).

Колонки — дни. Вверху — остатки на начало по каждому расчётному счёту и кассе, поступления и платежи (безнал и касса),
остатки на конец. Ниже — поступления и платежи по статьям ДДС, строки — контрагент + договор. В ячейке факт
(проведённые операции журнала по счетам денег — из выписки, зелёным) и план (ожидаемые платежи календаря — заявки,
ведомости, регламентные операции, инвестпроекты — серым). Остаток на начало дня: введённый вручную (по банк-клиенту),
иначе по журналу (прошедшие дни и сегодня), иначе — прогноз (остаток на конец предыдущего дня).
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Account, Article, BankAccount, CalendarBalance, Cfo, Contract, Counterparty, Operation, Posting
from app.services import calendar, reports
from app.services.articles import system_article
from app.services.ledger import select_lines, subtree

ZERO = Decimal("0.00")
NONE_SOURCE = "bank:none"


@dataclass
class Pair:
    """Значение ячейки: факт (из выписки / журнала) и план (из календаря); None — нет значения."""

    fact: Decimal | None = None
    plan: Decimal | None = None

    def add(self, fact: Decimal = ZERO, plan: Decimal = ZERO) -> None:
        if fact:
            self.fact = (self.fact or ZERO) + fact
        if plan:
            self.plan = (self.plan or ZERO) + plan

    @property
    def total(self) -> Decimal:
        return (self.fact or ZERO) + (self.plan or ZERO)


@dataclass
class Op:
    operation_id: int
    number: int
    amount: Decimal


@dataclass
class Cell(Pair):
    ops: list[Op] = field(default_factory=list)
    items: list[calendar.Item] = field(default_factory=list)


@dataclass
class Line:
    counterparty_id: int | None
    contract_id: int | None
    title: str = ""
    contract_title: str = ""
    cells: dict[dt.date, Cell] = field(default_factory=dict)
    bank: Decimal = ZERO
    cash: Decimal = ZERO


@dataclass
class Group:
    direction: str
    article_id: int | None
    title: str = ""
    sort: tuple = ()
    lines: dict[tuple, Line] = field(default_factory=dict)


@dataclass
class Source:
    key: str
    kind: str  # bank / cash
    title: str
    opening: dict[dt.date, Pair] = field(default_factory=dict)
    closing: dict[dt.date, Decimal] = field(default_factory=dict)
    fact_in: dict[dt.date, Decimal] = field(default_factory=dict)
    fact_out: dict[dt.date, Decimal] = field(default_factory=dict)
    plan_in: dict[dt.date, Decimal] = field(default_factory=dict)
    plan_out: dict[dt.date, Decimal] = field(default_factory=dict)


@dataclass
class Sheet:
    days: list[dt.date]
    today: dt.date
    sources: list[Source]
    groups: list[Group]


def _root_entity(db: Session, organization_id: int) -> dict[int, int | None]:
    """ЦФО → юрлицо его «Компании» (верхнего ЦФО)."""
    cfos = {c.id: c for c in db.scalars(select(Cfo).where(Cfo.organization_id == organization_id))}
    return {c.id: cfos[int(str(c.path).split(".")[0])].legal_entity_id for c in cfos.values()}


def _sources(db: Session, organization_id: int, legal_entity_id: int | None, cash_cfo_ids: set[int]) -> list[Source]:
    query = select(BankAccount).where(BankAccount.organization_id == organization_id, BankAccount.is_active.is_(True))
    if legal_entity_id is not None:
        query = query.where(BankAccount.legal_entity_id == legal_entity_id)
    result = [Source(f"bank:{b.id}", "bank", f"{b.name} ··{b.number[-4:]}") for b in db.scalars(query.order_by(BankAccount.name))]
    result.append(Source(NONE_SOURCE, "bank", "Счёт не выбран"))
    entity_of = _root_entity(db, organization_id) if legal_entity_id is not None else {}
    cfos = db.scalars(select(Cfo).where(Cfo.organization_id == organization_id,
                                        (Cfo.has_cash.is_(True)) | (Cfo.id.in_(cash_cfo_ids))).order_by(Cfo.path))
    for c in cfos:
        if legal_entity_id is None or entity_of.get(c.id) in (legal_entity_id, None):
            result.append(Source(f"cash:{c.id}", "cash", f"Касса: {c.name}"))
    return result


def _source_key(is_cash: bool, bank_account_id, cfo_id) -> str:
    if is_cash:
        return f"cash:{cfo_id}" if cfo_id else "cash:none"
    return f"bank:{bank_account_id}" if bank_account_id else NONE_SOURCE


def build(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date, *,
          legal_entity_id: int | None = None, include_pending: bool = False, today: dt.date | None = None) -> Sheet:
    today = today or dt.date.today()
    chain_start = min(date_from, today)
    days = [date_from + dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    money_roots = reports.money_account_ids(db)
    cash_ids = set(db.scalars(subtree(list(db.scalars(select(Account.id).where(Account.code == calendar.CASH_CODE))))))

    # --- факт из журнала: остаток до начала цепочки и движение по дням ---
    base = select_lines(organization_id, account_ids=money_roots, legal_entity_id=legal_entity_id,
                        date_to=chain_start - dt.timedelta(days=1)).subquery()
    signed = case((base.c.side == "debit", base.c.amount_rub), else_=-base.c.amount_rub)
    start: dict[str, Decimal] = {}
    for account_id, bank_id, cfo_id, amount in db.execute(
            select(base.c.account_id, base.c.bank_account, base.c.cfo, func.sum(signed))
            .group_by(base.c.account_id, base.c.bank_account, base.c.cfo)).all():
        key = _source_key(account_id in cash_ids, bank_id, cfo_id)
        start[key] = start.get(key, ZERO) + Decimal(amount)

    fact_rows = []
    if chain_start <= today:
        lines = select_lines(organization_id, account_ids=money_roots, legal_entity_id=legal_entity_id,
                             date_from=chain_start, date_to=min(date_to, today)).subquery()
        debit = lines.c.side == "debit"
        fact_rows = db.execute(
            select(lines.c.op_date, lines.c.operation_id, Operation.number, lines.c.side, lines.c.account_id,
                   lines.c.bank_account, lines.c.cfo, lines.c.cash_flow_item,
                   case((debit, Posting.ct_counterparty_id), else_=Posting.dt_counterparty_id),
                   case((debit, Posting.ct_contract_id), else_=Posting.dt_contract_id), lines.c.amount_rub)
            .join(Posting, Posting.id == lines.c.posting_id).join(Operation, Operation.id == lines.c.operation_id)
            .order_by(lines.c.op_date, Operation.number)).all()

    # --- план: ожидаемые платежи; просроченные — на сегодня ---
    items = []
    forecast_from = today
    if date_to >= forecast_from:
        items = calendar.expected(db, organization_id, today - dt.timedelta(days=calendar.LOOKBACK_DAYS), date_to,
                                  include_pending=include_pending, legal_entity_id=legal_entity_id, today=today)
    salary = system_article(db, organization_id, "salary_payment")

    cash_cfos = {r[6] for r in fact_rows if r[4] in cash_ids and r[6]} | {i.cash_cfo_id for i in items if i.cash_cfo_id}
    sources = {s.key: s for s in _sources(db, organization_id, legal_entity_id, cash_cfos)}

    def source(key: str) -> Source:
        if key not in sources:  # счёт в архиве или касса без признака, но с движением
            kind = "cash" if key.startswith("cash") else "bank"
            title = "Касса без ЦФО" if key == "cash:none" else "Счёт / касса вне списка"
            if kind == "bank" and key != NONE_SOURCE:
                account = db.get(BankAccount, int(key[5:]))
                title = f"{account.name} ··{account.number[-4:]}" if account else title
            if kind == "cash" and key != "cash:none":
                cfo = db.get(Cfo, int(key[5:]))
                title = f"Касса: {cfo.name}" if cfo else title
            sources[key] = Source(key, kind, title)
        return sources[key]

    groups: dict[tuple, Group] = {}

    def line(direction: str, article_id, counterparty_id, contract_id) -> Line:
        group = groups.setdefault((direction, article_id), Group(direction, article_id))
        return group.lines.setdefault((counterparty_id, contract_id), Line(counterparty_id, contract_id))

    shown = set(days)
    for op_date, op_id, number, side, account_id, bank_id, cfo_id, article_id, cp_id, contract_id, amount in fact_rows:
        amount = Decimal(amount)
        is_cash = account_id in cash_ids
        src = source(_source_key(is_cash, bank_id, cfo_id))
        bucket = src.fact_in if side == "debit" else src.fact_out
        bucket[op_date] = bucket.get(op_date, ZERO) + amount
        if op_date not in shown:
            continue
        row = line("in" if side == "debit" else "out", article_id, cp_id, contract_id)
        cell = row.cells.setdefault(op_date, Cell())
        cell.add(fact=amount)
        cell.ops.append(Op(op_id, number, amount))
        if is_cash:
            row.cash += amount
        else:
            row.bank += amount

    for item in items:
        effective = max(item.date, forecast_from)
        is_cash = item.cash_cfo_id is not None
        src = source(_source_key(is_cash, item.bank_account_id, item.cash_cfo_id))
        bucket = src.plan_in if item.direction == "in" else src.plan_out
        bucket[effective] = bucket.get(effective, ZERO) + item.amount
        if effective not in shown:
            continue
        article_id = item.article_id or (salary.id if item.source == "payout" and salary else None)
        row = line(item.direction, article_id, item.counterparty_id, item.contract_id)
        cell = row.cells.setdefault(effective, Cell())
        cell.add(plan=item.amount)
        cell.items.append(item)
        if is_cash:
            row.cash += item.amount
        else:
            row.bank += item.amount

    # --- остатки: вручную > по журналу (прошедшие дни и сегодня) > прогноз ---
    manual: dict[tuple[str, dt.date], Decimal] = {}
    for b in db.scalars(select(CalendarBalance).where(CalendarBalance.organization_id == organization_id,
                                                      CalendarBalance.day.between(chain_start, date_to))):
        manual[(_source_key(b.cash_cfo_id is not None, b.bank_account_id, b.cash_cfo_id), b.day)] = b.amount
    for src in sources.values():
        journal = start.get(src.key, ZERO)
        closing = None
        day = chain_start
        while day <= date_to:
            hand = manual.get((src.key, day))
            if day <= today:
                opening = hand if hand is not None else journal
            else:
                opening = hand if hand is not None else closing
            if day in shown:
                src.opening[day] = Pair(fact=journal if day <= today and src.key != NONE_SOURCE else None,
                                        plan=hand if hand is not None else (opening if day > today else None))
            flow = (src.fact_in.get(day, ZERO) - src.fact_out.get(day, ZERO)
                    + src.plan_in.get(day, ZERO) - src.plan_out.get(day, ZERO))
            closing = opening + flow
            journal += src.fact_in.get(day, ZERO) - src.fact_out.get(day, ZERO)
            if day in shown:
                src.closing[day] = closing
            day += dt.timedelta(days=1)

    # --- названия и порядок ---
    article_ids = {g.article_id for g in groups.values() if g.article_id}
    articles = {a.id: a for a in db.scalars(select(Article).where(Article.id.in_(article_ids)))} if article_ids else {}
    cp_ids = {k[0] for g in groups.values() for k in g.lines if k[0]}
    counterparties = dict(db.execute(select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(cp_ids))).all()) \
        if cp_ids else {}
    ct_ids = {k[1] for g in groups.values() for k in g.lines if k[1]}
    contracts = {c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(ct_ids)))} if ct_ids else {}
    for g in groups.values():
        article = articles.get(g.article_id)
        g.title = (f"{article.code} {article.name}".strip() if article and article.code else article.name) \
            if article else "Без статьи"
        g.sort = (g.direction != "in", article is None, reports._natural(article.code if article else None),
                  g.title.lower())
        for row in g.lines.values():
            row.title = counterparties.get(row.counterparty_id, "Без контрагента")
            contract = contracts.get(row.contract_id)
            row.contract_title = (contract.name or f"№ {contract.number}") if contract else ""
        g.lines = dict(sorted(g.lines.items(), key=lambda kv: (kv[1].title.lower(), kv[1].contract_title)))
    ordered_sources = [s for s in sources.values() if s.key != NONE_SOURCE or s.plan_in or s.plan_out or s.fact_in
                       or s.fact_out]
    return Sheet(days, today, ordered_sources, sorted(groups.values(), key=lambda g: g.sort))
