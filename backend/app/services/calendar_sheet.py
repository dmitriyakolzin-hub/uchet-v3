"""Платёжный календарь по форме владельца (docs/TZ.md, раздел 24, «Детали 14.6»; раздел 26, «Детали 16.1»).

Колонки — дни. Источник денег — расчётный счёт или касса юрлица. По каждому источнику и дню:
- остаток на начало: введённый вручную (по банк-клиенту) > по журналу (прошедшие дни и сегодня) > прогноз;
- поступления и оплаты: факт (проведённые операции журнала по счетам денег — из выписки) и план (ожидаемые платежи
  календаря: заявки, ведомости, регламентные операции, инвестпроекты; просроченные — на сегодня);
- технические операции — движение между своими деньгами (корр. счёт — тоже деньги): переводы между счетами,
  инкассация (касса → банк), снятие с расчётного счёта в кассу, перемещение между кассами;
- остаток на конец = начало + поступления − оплаты ± технические.
Строки расшифровки: направление + источник + статья ДДС + контрагент + договор, в ячейке — операции и плановые платежи.
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
    CalendarBalance,
    CalendarTransfer,
    Cfo,
    Contract,
    Counterparty,
    LegalEntity,
    Operation,
    Posting,
)
from app.services import calendar, reports
from app.services.articles import system_article
from app.services.ledger import select_lines, subtree

ZERO = Decimal("0.00")
# технические операции: (откуда, куда) → вид
TECH_TYPES = {("bank", "bank"): "transfer", ("cash", "bank"): "collection", ("bank", "cash"): "withdrawal",
              ("cash", "cash"): "cash_move"}
TECH_TITLES = {"transfer": "Переводы между счетами", "collection": "Инкассация (сдача наличных в банк)",
               "withdrawal": "Снятие с расчётного счёта в кассу", "cash_move": "Перемещение между кассами"}
TRANSFER_MATCH_DAYS = 14  # плановый перевод закрывается проведённым в пределах стольких дней


def from_statement(operation=Operation):
    """Операция загружена из банковской выписки (у загрузки из Excel ключ начинается с «xlsx:»)."""
    return (operation.import_key.is_not(None)) & (~operation.import_key.like("xlsx:%"))


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
class Source:
    key: str
    kind: str  # bank / cash
    title: str
    entity_id: int | None
    bank_account_id: int | None = None
    cash_cfo_id: int | None = None
    opening: dict[dt.date, Pair] = field(default_factory=dict)
    closing: dict[dt.date, Decimal] = field(default_factory=dict)
    fact_in: dict[dt.date, Decimal] = field(default_factory=dict)
    fact_out: dict[dt.date, Decimal] = field(default_factory=dict)
    plan_in: dict[dt.date, Decimal] = field(default_factory=dict)
    plan_out: dict[dt.date, Decimal] = field(default_factory=dict)
    # вид → встречный счёт / касса → день → сумма со знаком (факт и план) и операции / плановые переводы
    tech: dict[str, dict[str, dict[dt.date, Cell]]] = field(default_factory=dict)

    def tech_net(self, day: dt.date, plan: bool = False) -> Decimal:
        return sum(((c[day].plan if plan else c[day].fact) or ZERO
                    for by_corr in self.tech.values() for c in by_corr.values() if day in c), ZERO)

    @property
    def has_movement(self) -> bool:
        return bool(self.fact_in or self.fact_out or self.plan_in or self.plan_out or self.tech)


@dataclass
class Line:
    direction: str
    source_key: str
    article_id: int | None
    counterparty_id: int | None
    contract_id: int | None
    article_title: str = ""
    title: str = ""
    contract_title: str = ""
    cells: dict[dt.date, Cell] = field(default_factory=dict)


@dataclass
class Sheet:
    days: list[dt.date]
    today: dt.date
    entities: dict[int | None, str]
    sources: list[Source]
    lines: list[Line]
    titles: dict[str, str] = field(default_factory=dict)  # ключ источника → «юрлицо · счёт»


def _root_entity(db: Session, organization_id: int) -> dict[int, int | None]:
    """ЦФО → юрлицо его «Компании» (верхнего ЦФО)."""
    cfos = {c.id: c for c in db.scalars(select(Cfo).where(Cfo.organization_id == organization_id))}
    return {c.id: cfos[int(str(c.path).split(".")[0])].legal_entity_id for c in cfos.values()}


def build(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date, *,
          legal_entity_id: int | None = None, include_pending: bool = False, with_statements: bool = True,
          today: dt.date | None = None) -> Sheet:
    """with_statements=False — без операций из банковских выписок: только введённое вручную (раздел 26, «Детали 16.2»)."""
    today = today or dt.date.today()
    chain_start = min(date_from, today)
    days = [date_from + dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    shown = set(days)
    money_roots = reports.money_account_ids(db)
    money_ids = set(db.scalars(subtree(money_roots)))
    cash_ids = set(db.scalars(subtree(list(db.scalars(select(Account.id).where(Account.code == calendar.CASH_CODE))))))
    root_entity = _root_entity(db, organization_id)
    accounts = {b.id: b for b in db.scalars(select(BankAccount).where(BankAccount.organization_id == organization_id))}
    cfos = {c.id: c for c in db.scalars(select(Cfo).where(Cfo.organization_id == organization_id))}
    sources: dict[str, Source] = {}

    def bank_source(bank_id: int | None, entity_id: int | None) -> Source:
        account = accounts.get(bank_id) if bank_id else None
        entity = account.legal_entity_id if account else entity_id
        key = f"bank:{bank_id}" if account else f"bank:none:{entity}"
        if key not in sources:
            title = f"{account.name} ··{account.number[-4:]}" if account else "Счёт не выбран"
            sources[key] = Source(key, "bank", title, entity, bank_account_id=bank_id if account else None)
        return sources[key]

    org_entities = list(db.scalars(select(LegalEntity.id).where(LegalEntity.organization_id == organization_id)))
    single_entity = org_entities[0] if len(org_entities) == 1 else None

    def cash_source(cfo_id: int | None, entity_id: int | None) -> Source:
        # юрлицо кассы — юрлицо её «Компании», иначе юрлицо операции (или единственное юрлицо организации)
        entity = root_entity.get(cfo_id) or entity_id or single_entity
        key = f"cash:{cfo_id}:{entity}"
        if key not in sources:
            cfo = cfos.get(cfo_id)
            sources[key] = Source(key, "cash", f"Касса: {cfo.name}" if cfo else "Касса без ЦФО", entity,
                                  cash_cfo_id=cfo_id)
        return sources[key]

    def source_of(is_cash: bool, bank_id, cfo_id, entity_id) -> Source:
        return cash_source(cfo_id, entity_id) if is_cash else bank_source(bank_id, entity_id)

    # все действующие счета и кассы — даже без движения
    for account in accounts.values():
        if account.is_active and legal_entity_id in (None, account.legal_entity_id):
            bank_source(account.id, account.legal_entity_id)
    for cfo in cfos.values():
        if cfo.has_cash and legal_entity_id in (None, root_entity.get(cfo.id)):
            cash_source(cfo.id, root_entity.get(cfo.id))

    # --- факт из журнала: остаток до начала цепочки и движение по дням ---
    def manual_only(query, operation_col):
        if with_statements:
            return query
        return query.where(operation_col.not_in(select(Operation.id).where(from_statement())))

    base = select_lines(organization_id, account_ids=money_roots, legal_entity_id=legal_entity_id,
                        date_to=chain_start - dt.timedelta(days=1)).subquery()
    signed = case((base.c.side == "debit", base.c.amount_rub), else_=-base.c.amount_rub)
    start: dict[str, Decimal] = {}
    for account_id, bank_id, cfo_id, entity_id, amount in db.execute(manual_only(
            select(base.c.account_id, base.c.bank_account, base.c.cfo, base.c.legal_entity_id, func.sum(signed)),
            base.c.operation_id)
            .group_by(base.c.account_id, base.c.bank_account, base.c.cfo, base.c.legal_entity_id)).all():
        src = source_of(account_id in cash_ids, bank_id, cfo_id, entity_id)
        start[src.key] = start.get(src.key, ZERO) + Decimal(amount)

    lines_by_key: dict[tuple, Line] = {}

    def line(direction: str, src: Source, article_id, counterparty_id, contract_id) -> Line:
        key = (direction, src.key, article_id, counterparty_id, contract_id)
        if key not in lines_by_key:
            lines_by_key[key] = Line(direction, src.key, article_id, counterparty_id, contract_id)
        return lines_by_key[key]

    def tech_cell(src: Source, tech: str, corr: Source, day: dt.date) -> Cell:
        return src.tech.setdefault(tech, {}).setdefault(corr.key, {}).setdefault(day, Cell())

    fact_transfers: list[list] = []  # [источник, встречный, сумма со знаком, день, использован]
    if chain_start <= today:
        lines = select_lines(organization_id, account_ids=money_roots, legal_entity_id=legal_entity_id,
                             date_from=chain_start, date_to=min(date_to, today)).subquery()
        debit = lines.c.side == "debit"
        rows = db.execute(manual_only(
            select(lines.c.op_date, lines.c.operation_id, Operation.number, lines.c.side, lines.c.account_id,
                   lines.c.corr_account_id, lines.c.bank_account, lines.c.cfo, lines.c.legal_entity_id,
                   lines.c.cash_flow_item,
                   case((debit, Posting.ct_counterparty_id), else_=Posting.dt_counterparty_id),
                   case((debit, Posting.ct_contract_id), else_=Posting.dt_contract_id), lines.c.amount_rub,
                   case((debit, Posting.ct_bank_account_id), else_=Posting.dt_bank_account_id),
                   case((debit, Posting.ct_cfo_id), else_=Posting.dt_cfo_id))
            .join(Posting, Posting.id == lines.c.posting_id).join(Operation, Operation.id == lines.c.operation_id),
            lines.c.operation_id).order_by(lines.c.op_date, Operation.number)).all()
        for (day, op_id, number, side, account_id, corr_id, bank_id, cfo_id, entity_id, article_id, cp_id,
             contract_id, amount, corr_bank, corr_cfo) in rows:
            amount = Decimal(amount)
            kind = "cash" if account_id in cash_ids else "bank"
            src = source_of(kind == "cash", bank_id, cfo_id, entity_id)
            if corr_id in money_ids:  # техническая операция: деньги ↔ деньги, разбивка по встречным деньгам
                corr_kind = "cash" if corr_id in cash_ids else "bank"
                corr = source_of(corr_kind == "cash", corr_bank, corr_cfo, entity_id)
                tech = TECH_TYPES[(corr_kind, kind) if side == "debit" else (kind, corr_kind)]
                value = amount if side == "debit" else -amount
                cell = tech_cell(src, tech, corr, day)
                cell.fact = (cell.fact or ZERO) + value
                cell.ops.append(Op(op_id, number, value))
                fact_transfers.append([src.key, corr.key, value, day, False])
                continue
            bucket = src.fact_in if side == "debit" else src.fact_out
            bucket[day] = bucket.get(day, ZERO) + amount
            if day in shown:
                cell = line("in" if side == "debit" else "out", src, article_id, cp_id, contract_id).cells \
                    .setdefault(day, Cell())
                cell.add(fact=amount)
                cell.ops.append(Op(op_id, number, amount))

    # --- план: ожидаемые платежи; просроченные — на сегодня ---
    if date_to >= today:
        salary = system_article(db, organization_id, "salary_payment")
        for item in calendar.expected(db, organization_id, today - dt.timedelta(days=calendar.LOOKBACK_DAYS), date_to,
                                      include_pending=include_pending, legal_entity_id=legal_entity_id, today=today):
            day = max(item.date, today)
            src = source_of(item.cash_cfo_id is not None, item.bank_account_id, item.cash_cfo_id, item.legal_entity_id)
            bucket = src.plan_in if item.direction == "in" else src.plan_out
            bucket[day] = bucket.get(day, ZERO) + item.amount
            if day in shown:
                article_id = item.article_id or (salary.id if item.source == "payout" and salary else None)
                cell = line(item.direction, src, article_id, item.counterparty_id, item.contract_id).cells \
                    .setdefault(day, Cell())
                cell.add(plan=item.amount)
                cell.items.append(item)

    # --- плановые технические операции, введённые в календаре; проведённый такой же перевод их закрывает ---
    transfers = select(CalendarTransfer).where(CalendarTransfer.organization_id == organization_id,
                                               CalendarTransfer.day <= date_to)
    if legal_entity_id is not None:
        transfers = transfers.where(CalendarTransfer.legal_entity_id == legal_entity_id)
    for t in db.scalars(transfers.order_by(CalendarTransfer.day, CalendarTransfer.id)):
        src = source_of(t.from_cash_cfo_id is not None, t.from_bank_account_id, t.from_cash_cfo_id, t.legal_entity_id)
        dst = source_of(t.to_cash_cfo_id is not None, t.to_bank_account_id, t.to_cash_cfo_id, t.legal_entity_id)
        done = next((f for f in fact_transfers if not f[4] and f[0] == src.key and f[1] == dst.key
                     and f[2] == -t.amount and abs((f[3] - t.day).days) <= TRANSFER_MATCH_DAYS), None)
        if done is not None:
            done[4] = True
            for f in fact_transfers:  # и встречная строка того же перевода
                if not f[4] and f[0] == dst.key and f[1] == src.key and f[2] == t.amount and f[3] == done[3]:
                    f[4] = True
                    break
            continue
        if t.day < today - dt.timedelta(days=calendar.LOOKBACK_DAYS):
            continue
        day = max(t.day, today)
        tech = TECH_TYPES[(src.kind, dst.kind)]
        title = f"Перевод: {src.title} → {dst.title}" + (f" ({t.description})" if t.description else "")
        for own, corr, value, direction in ((src, dst, -t.amount, "out"), (dst, src, t.amount, "in")):
            cell = tech_cell(own, tech, corr, day)
            cell.plan = (cell.plan or ZERO) + value
            cell.items.append(calendar.Item("transfer", t.id, title, t.day, direction, t.amount, own.entity_id,
                                            own.bank_account_id, own.cash_cfo_id, status="plan",
                                            overdue=t.day < today))

    # --- остатки: вручную > по журналу (прошедшие дни и сегодня) > прогноз ---
    manual: dict[tuple[str, dt.date], Decimal] = {}
    by_cfo: dict[int, str] = {}
    for src in sources.values():
        if src.cash_cfo_id is not None:
            by_cfo.setdefault(src.cash_cfo_id, src.key)
    for b in db.scalars(select(CalendarBalance).where(CalendarBalance.organization_id == organization_id,
                                                      CalendarBalance.day.between(chain_start, date_to))):
        key = f"bank:{b.bank_account_id}" if b.bank_account_id else by_cfo.get(b.cash_cfo_id)
        if key in sources:
            manual[(key, b.day)] = b.amount
    for src in sources.values():
        journal = start.get(src.key, ZERO)
        closing = ZERO
        day = chain_start
        while day <= date_to:
            hand = manual.get((src.key, day))
            opening = hand if hand is not None else (journal if day <= today else closing)
            if day in shown:
                real = src.bank_account_id is not None or src.kind == "cash"  # у «Счёт не выбран» остатка нет
                src.opening[day] = Pair(fact=journal if day <= today and real else None,
                                        plan=hand if hand is not None else (opening if day > today else None))
            fact = src.fact_in.get(day, ZERO) - src.fact_out.get(day, ZERO) + src.tech_net(day)
            closing = (opening + fact + src.plan_in.get(day, ZERO) - src.plan_out.get(day, ZERO)
                       + src.tech_net(day, plan=True))
            journal += fact
            if day in shown:
                src.closing[day] = closing
            day += dt.timedelta(days=1)
    for src in sources.values():  # техническое движение — только за показанные дни
        for by_corr in src.tech.values():
            for cells in by_corr.values():
                for day in [d for d in cells if d not in shown]:
                    del cells[day]
        src.tech = {t: {k: v for k, v in by_corr.items() if v} for t, by_corr in src.tech.items()}
        src.tech = {t: v for t, v in src.tech.items() if v}

    # --- названия и порядок ---
    entity_ids = {s.entity_id for s in sources.values()}  # после чистки
    entities = {e.id: e.short_name for e in db.scalars(select(LegalEntity).where(LegalEntity.id.in_(
        {i for i in entity_ids if i is not None})))}
    if None in entity_ids:
        entities[None] = "Без юрлица"
    all_lines = list(lines_by_key.values())
    article_ids = {ln.article_id for ln in all_lines if ln.article_id}
    articles = {a.id: a for a in db.scalars(select(Article).where(Article.id.in_(article_ids)))} if article_ids else {}
    cp_ids = {ln.counterparty_id for ln in all_lines if ln.counterparty_id}
    counterparties = dict(db.execute(select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(cp_ids))).all()) \
        if cp_ids else {}
    ct_ids = {ln.contract_id for ln in all_lines if ln.contract_id}
    contracts = {c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(ct_ids)))} if ct_ids else {}
    for ln in all_lines:
        article = articles.get(ln.article_id)
        ln.article_title = (f"{article.code} {article.name}" if article.code else article.name) if article else "Без статьи"
        ln.title = counterparties.get(ln.counterparty_id, "Без контрагента")
        contract = contracts.get(ln.contract_id)
        ln.contract_title = (contract.name or f"№ {contract.number}") if contract else ""

    def article_sort(ln: Line):
        article = articles.get(ln.article_id)
        return (article is None, reports._natural(article.code if article else None), ln.article_title.lower(),
                ln.title.lower(), ln.contract_title)

    # касса без юрлица и без движения, если та же касса есть у юрлица, — лишняя
    with_entity = {src.cash_cfo_id for src in sources.values() if src.kind == "cash" and src.entity_id is not None}
    for key in [k for k, src in sources.items() if src.kind == "cash" and src.entity_id is None
                and src.cash_cfo_id in with_entity and not src.has_movement and not start.get(k)]:
        del sources[key]
    shown_sources = [s for s in sources.values() if s.bank_account_id is not None or s.kind == "cash" or s.has_movement]
    shown_sources.sort(key=lambda s: (entities.get(s.entity_id, ""), s.kind != "bank", s.bank_account_id is None,
                                      s.title.lower()))
    titles = {k: (f"{entities.get(src.entity_id, '')} · {src.title}" if len(entities) > 1 else src.title)
              for k, src in sources.items()}
    return Sheet(days, today, dict(sorted(entities.items(), key=lambda kv: (kv[0] is None, kv[1]))), shown_sources,
                 sorted(all_lines, key=article_sort), titles)
