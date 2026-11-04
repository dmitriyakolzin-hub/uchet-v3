"""Периодические платежи по договорам (docs/TZ.md, раздел 24, «Детали 14.3»).

Условия — записи «действует с — по» у договора: сумма, число месяца, счёт, статья ДДС, ЦФО, ЦФУ. Новая запись
закрывает прежнюю днём раньше. На каждый месяц (текущий и два следующих) по условиям создаётся заявка «периодический
платёж» — сразу на согласование финансового директора; число месяца в выходной — ближайший рабочий день раньше.
Пока заявка не согласована, она подстраивается под изменённые условия; согласованную и оплаченную не трогаем.
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


def active_terms(terms: list[ContractPayment], month: dt.date) -> ContractPayment | None:
    """Условия, действующие в день оплаты месяца (последние по «действует с»)."""
    found = [t for t in terms
             if t.valid_from <= pay_day(month, t.day) and (t.valid_to is None or t.valid_to >= pay_day(month, t.day))]
    return max(found, key=lambda t: t.valid_from) if found else None


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
    contracts = {c.id: c for c in db.scalars(select(Contract).where(Contract.organization_id == organization_id))}
    if not contracts:
        return 0
    terms_by_contract: dict[int, list[ContractPayment]] = {}
    for t in db.scalars(select(ContractPayment).where(ContractPayment.contract_id.in_(contracts))):
        terms_by_contract.setdefault(t.contract_id, []).append(t)
    existing: dict[tuple[int, dt.date], PaymentRequest] = {}
    for req in db.scalars(select(PaymentRequest).where(PaymentRequest.organization_id == organization_id,
                                                       PaymentRequest.source == "periodic",
                                                       PaymentRequest.period.in_(months))):
        existing[(req.contract_id, req.period)] = req
    changed = 0
    for contract_id, terms in terms_by_contract.items():
        contract = contracts[contract_id]
        if not contract.is_active:
            continue
        for month in months:
            current = active_terms(terms, month)
            req = existing.get((contract_id, month))
            editable = req is not None and req.status in ("draft", "pending", "revision", "rejected")
            if current is None:
                if editable and req.status != "rejected":
                    db.delete(req)  # условия закончились — неутверждённая заявка не нужна
                    changed += 1
                continue
            date = pay_day(month, current.day)
            fields = dict(pay_date=date, legal_entity_id=contract.legal_entity_id, counterparty_id=contract.counterparty_id,
                          contract_id=contract.id, bank_account_id=current.bank_account_id, article_id=current.article_id,
                          cfo_id=current.cfo_id, executor_id=current.executor_id, amount=current.amount,
                          description=(f"Периодический платёж за {MONTHS_GENITIVE[month.month - 1]} {month.year}"
                                       + (f": {current.description}" if current.description else "")),
                          periodic_id=current.id)
            author = db.get(User, current.created_by)
            if req is None:
                req = PaymentRequest(organization_id=organization_id, number=service.next_number(db, organization_id),
                                     direction="out", source="periodic", initiator="finance", period=month,
                                     status="draft", created_by=author.id, **fields)
                db.add(req)
                db.flush()
                approval.event(db, req, author, "created", "по условиям договора")
                approval.submit(db, req, author)
                changed += 1
            elif editable and any(getattr(req, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(req, k, v)
                approval.event(db, req, author, "periodic_updated", "условия договора изменились")
                changed += 1
    db.flush()
    return changed
