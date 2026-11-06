"""Периодические платежи по договорам (docs/TZ.md, раздел 24, «Детали 14.3»).

Условия — записи «действует с — по» у договора: сумма, число месяца, счёт, статья ДДС, ЦФО, ЦФУ. Новая запись
закрывает прежнюю днём раньше. На каждый месяц (текущий и два следующих) по условиям создаётся заявка «периодический
платёж» — сразу на согласование финансового директора; число месяца в выходной — ближайший рабочий день раньше.
Пока заявка не согласована, она подстраивается под изменённые условия; согласованную, оплаченную и поправленную
вручную в календаре (перенос, сумма) не трогаем.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.payroll_rates import HOLIDAYS
from app.models import Contract, ContractPayment, PaymentRequest, User
from app.services import approval, payment_requests as service

HORIZON_MONTHS = 3  # текущий и два следующих
MONTHS_GENITIVE = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
                   "ноябрь", "декабрь"]


class PeriodicError(ValueError):
    pass


def _month_end(day: dt.date) -> dt.date:
    return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def _shift(day: dt.date, months: int) -> dt.date:
    index = day.year * 12 + day.month - 1 + months
    return dt.date(index // 12, index % 12 + 1, 1)


def is_working(day: dt.date) -> bool:
    return day.weekday() < 5 and (day.month, day.day) not in HOLIDAYS


def pay_day(month: dt.date, day: int) -> dt.date:
    """Число месяца (не позже последнего дня); выходной или праздник — ближайший рабочий день раньше
    (если раньше в месяце рабочих нет — первый рабочий после)."""
    target = month.replace(day=min(day, _month_end(month).day))
    candidate = target
    while not is_working(candidate) and candidate.day > 1:
        candidate -= dt.timedelta(days=1)
    if is_working(candidate):
        return candidate
    candidate = target
    while not is_working(candidate):
        candidate += dt.timedelta(days=1)
    return candidate


FREQUENCY_STEP = {"month": 1, "quarter": 3, "year": 12}
FREQUENCY_TITLES = {"month": "ежемесячно", "quarter": "раз в квартал", "year": "раз в год"}


def due_in(t: ContractPayment, month: dt.date) -> bool:
    """Платёж по этим условиям приходится на месяц: раз в месяц, квартал (каждые 3 месяца от «действует с») или год."""
    step = FREQUENCY_STEP.get(t.frequency or "month", 1)
    passed = (month.year - t.valid_from.year) * 12 + month.month - t.valid_from.month
    return passed >= 0 and passed % step == 0


def active_terms(terms: list[ContractPayment], month: dt.date) -> ContractPayment | None:
    """Условия, действующие в день оплаты месяца (последние по «действует с»), если платёж приходится на этот месяц."""
    found = [t for t in terms
             if t.valid_from <= pay_day(month, t.day) and (t.valid_to is None or t.valid_to >= pay_day(month, t.day))]
    current = max(found, key=lambda t: t.valid_from) if found else None
    return current if current is not None and due_in(current, month) else None


def group_key(t: ContractPayment) -> tuple:
    """Условия одного договора сменяют друг друга; условия без договора — сами по себе."""
    return ("c", t.contract_id) if t.contract_id else ("t", t.id)


def add_terms(db: Session, contract: Contract, terms: ContractPayment) -> None:
    """Новые условия: прежние открытые (или действующие после новой даты) закрываются днём раньше."""
    for old in db.scalars(select(ContractPayment).where(ContractPayment.contract_id == contract.id)):
        if old.id == terms.id:
            continue
        if old.valid_from >= terms.valid_from:
            raise PeriodicError(f"Уже есть условия с {old.valid_from:%d.%m.%Y} — новые должны начинаться позже")
        if old.valid_to is None or old.valid_to >= terms.valid_from:
            old.valid_to = terms.valid_from - dt.timedelta(days=1)
    db.add(terms)
    db.flush()


def months_ahead(today: dt.date) -> list[dt.date]:
    start = today.replace(day=1)
    return [_shift(start, i) for i in range(HORIZON_MONTHS)]


def ensure(db: Session, organization_id: int, today: dt.date | None = None) -> int:
    """Заявки периодических платежей на текущий и два следующих месяца; возвращает число новых и изменённых.
    Автор заявки и записей истории — тот, кто завёл условия платежа."""
    today = today or dt.date.today()
    months = months_ahead(today)
    terms_by_group: dict[tuple, list[ContractPayment]] = {}
    for t in db.scalars(select(ContractPayment).where(ContractPayment.organization_id == organization_id)):
        terms_by_group.setdefault(group_key(t), []).append(t)
    if not terms_by_group:
        return 0
    group_of = {t.id: key for key, terms in terms_by_group.items() for t in terms}
    contracts = {c.id: c for c in db.scalars(select(Contract).where(
        Contract.id.in_({k[1] for k in terms_by_group if k[0] == "c"})))}
    existing: dict[tuple, PaymentRequest] = {}
    for req in db.scalars(select(PaymentRequest).where(PaymentRequest.organization_id == organization_id,
                                                       PaymentRequest.source == "periodic",
                                                       PaymentRequest.period.in_(months))):
        key = group_of.get(req.periodic_id) or ("c", req.contract_id)
        existing[(key, req.period)] = req
    changed = 0
    for key, terms in terms_by_group.items():
        contract = contracts.get(key[1]) if key[0] == "c" else None
        if contract is not None and not contract.is_active:
            continue
        for month in months:
            current = active_terms(terms, month)
            req = existing.get((key, month))
            # поправленную вручную в календаре (дата, сумма) — не трогаем
            editable = req is not None and req.status in ("draft", "pending", "revision", "rejected") and not req.adjusted
            if current is None:
                if editable and req.status != "rejected":
                    db.delete(req)  # условия закончились — неутверждённая заявка не нужна
                    changed += 1
                continue
            date = pay_day(month, current.day)
            title = current.name or "Периодический платёж"
            fields = dict(pay_date=date,
                          legal_entity_id=contract.legal_entity_id if contract else current.legal_entity_id,
                          counterparty_id=contract.counterparty_id if contract else current.counterparty_id,
                          contract_id=contract.id if contract else None, bank_account_id=current.bank_account_id,
                          article_id=current.article_id, cfo_id=current.cfo_id, executor_id=current.executor_id,
                          amount=current.amount,
                          description=(f"{title} за {MONTHS_GENITIVE[month.month - 1]} {month.year}"
                                       + (f": {current.description}" if current.description else "")),
                          periodic_id=current.id)
            author = db.get(User, current.created_by)
            if req is None:
                req = PaymentRequest(organization_id=organization_id, number=service.next_number(db, organization_id),
                                     direction="out", source="periodic", initiator="finance", period=month,
                                     status="draft", created_by=author.id, **fields)
                db.add(req)
                db.flush()
                approval.event(db, req, author, "created", "по условиям периодического платежа")
                approval.submit(db, req, author)
                changed += 1
            elif editable and any(getattr(req, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(req, k, v)
                approval.event(db, req, author, "periodic_updated", "условия периодического платежа изменились")
                changed += 1
    db.flush()
    return changed
