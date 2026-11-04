"""Реестр на оплату (docs/TZ.md, раздел 24, «Детали 14.4»).

Согласованные и ещё не оплаченные выплаты — по дням оплаты и расчётным счетам (касса — отдельно), с итогами
и проверкой остатка на счёте. Выгрузка платёжных поручений для банк-клиента в формате обмена с 1С
(1CClientBankExchange, кодировка Windows-1251); выгруженные заявки отмечаются «отправлено в банк».
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bank, BankAccount, Cfo, Counterparty, LegalEntity, PaymentRequest
from app.services import money

ZERO = Decimal("0.00")


class RegistryError(ValueError):
    pass


@dataclass
class Group:
    pay_date: dt.date
    bank_account_id: int | None
    cash_cfo_id: int | None
    requests: list[PaymentRequest] = field(default_factory=list)
    total: Decimal = ZERO
    available: Decimal | None = None  # остаток счёта с учётом предыдущих групп реестра (только расчётный счёт)

    @property
    def shortage(self) -> Decimal:
        if self.available is None:
            return ZERO
        return max(ZERO, self.total - self.available)


def approved(db: Session, organization_id: int, date_from: dt.date | None, date_to: dt.date | None,
             include_sent: bool = True) -> list[PaymentRequest]:
    query = select(PaymentRequest).where(PaymentRequest.organization_id == organization_id,
                                         PaymentRequest.direction == "out", PaymentRequest.status == "approved")
    if date_from:
        query = query.where(PaymentRequest.pay_date >= date_from)
    if date_to:
        query = query.where(PaymentRequest.pay_date <= date_to)
    if not include_sent:
        query = query.where(PaymentRequest.sent_at.is_(None))
    return list(db.scalars(query.order_by(PaymentRequest.pay_date, PaymentRequest.number)))


def groups(db: Session, organization_id: int, requests: list[PaymentRequest],
           today: dt.date | None = None) -> list[Group]:
    """Группы «день + счёт» по порядку дней; доступный остаток — остаток счёта сегодня минус предыдущие группы
    этого счёта (просроченные заявки — тоже из сегодняшнего остатка)."""
    today = today or dt.date.today()
    by_key: dict[tuple, Group] = {}
    for r in requests:
        key = (r.pay_date, r.bank_account_id, r.cash_cfo_id)
        group = by_key.setdefault(key, Group(r.pay_date, r.bank_account_id, r.cash_cfo_id))
        group.requests.append(r)
        group.total += r.amount
    result = sorted(by_key.values(), key=lambda g: (g.pay_date, g.cash_cfo_id is not None, g.bank_account_id or 0,
                                                    g.cash_cfo_id or 0))
    left: dict[int, Decimal] = {}
    for g in result:
        if g.bank_account_id is None:
            continue
        if g.bank_account_id not in left:
            left[g.bank_account_id] = money.bank_balance(db, organization_id, g.bank_account_id, today)
        g.available = left[g.bank_account_id]
        left[g.bank_account_id] -= g.total
    return result


def problems(db: Session, req: PaymentRequest) -> list[str]:
    """Чего не хватает для платёжного поручения."""
    result = []
    if req.cash_cfo_id is not None:
        return ["кассовая заявка — платёжное поручение не нужно"]
    if req.bank_account_id is None:
        result.append("не выбран расчётный счёт")
    counterparty = db.get(Counterparty, req.counterparty_id) if req.counterparty_id else None
    if counterparty is None:
        result.append("не указан контрагент")
        return result
    if not counterparty.inn and counterparty.kind != "person":
        result.append("у контрагента нет ИНН")
    if not counterparty.account_number:
        result.append("у контрагента нет расчётного счёта")
    if counterparty.bank_id is None:
        result.append("у контрагента не указан банк")
    if not req.description.strip():
        result.append("пустое назначение платежа")
    return result


def warnings(req: PaymentRequest) -> list[str]:
    if req.cash_cfo_id is None and "ндс" not in req.description.lower():
        return ["в назначении не указан НДС («в т.ч. НДС …» или «НДС не облагается»)"]
    return []


def _clean(value: str) -> str:
    """Значение строки файла: без переводов строк и знака «=» в начале."""
    return " ".join((value or "").split())


def _date(day: dt.date) -> str:
    return day.strftime("%d.%m.%Y")


def export_1c(db: Session, requests: list[PaymentRequest], now: dt.datetime | None = None) -> bytes:
    """Файл платёжных поручений для банк-клиента. Заявки — с одного или нескольких счетов; ошибки — RegistryError."""
    now = now or dt.datetime.now()
    errors = [f"№ {r.number}: {', '.join(p)}" for r in requests if (p := problems(db, r))]
    if errors:
        raise RegistryError("Нельзя выгрузить: " + "; ".join(errors))
    if not requests:
        raise RegistryError("Нет заявок для выгрузки")
    accounts = {r.bank_account_id: db.get(BankAccount, r.bank_account_id) for r in requests}
    dates = [r.pay_date for r in requests]
    lines = ["1CClientBankExchange", "ВерсияФормата=1.03", "Кодировка=Windows", "Отправитель=uchet-v3", "Получатель=",
             f"ДатаСоздания={_date(now.date())}", f"ВремяСоздания={now:%H:%M:%S}",
             f"ДатаНачала={_date(min(dates))}", f"ДатаКонца={_date(max(dates))}"]
    lines += [f"РасчСчет={a.number}" for a in accounts.values()]
    lines.append("Документ=Платежное поручение")
    for r in requests:
        account = accounts[r.bank_account_id]
        payer = db.get(LegalEntity, account.legal_entity_id)
        payer_bank = db.get(Bank, account.bank_id)
        counterparty = db.get(Counterparty, r.counterparty_id)
        payee_bank = db.get(Bank, counterparty.bank_id)
        payee_name = counterparty.full_name or counterparty.name
        lines += [
            "СекцияДокумент=Платежное поручение",
            f"Номер={r.number}",
            f"Дата={_date(now.date())}",  # дата документа — день выгрузки
            f"Сумма={r.amount:.2f}",
            f"ПлательщикСчет={account.number}",
            f"Плательщик=ИНН {payer.inn} {_clean(payer.full_name)}",
            f"ПлательщикИНН={payer.inn}",
            f"ПлательщикКПП={payer.kpp or ''}",
            f"Плательщик1={_clean(payer.full_name)}",
            f"ПлательщикРасчСчет={account.number}",
            f"ПлательщикБанк1={_clean(payer_bank.name)}",
            f"ПлательщикБанк2={_clean(payer_bank.city)}",
            f"ПлательщикБИК={payer_bank.bic}",
            f"ПлательщикКорсчет={payer_bank.corr_account or ''}",
            f"ПолучательСчет={counterparty.account_number}",
            "Получатель=" + (f"ИНН {counterparty.inn} " if counterparty.inn else "") + _clean(payee_name),
            f"ПолучательИНН={counterparty.inn or ''}",
            f"ПолучательКПП={counterparty.kpp or ''}",
            f"Получатель1={_clean(payee_name)}",
            f"ПолучательРасчСчет={counterparty.account_number}",
            f"ПолучательБанк1={_clean(payee_bank.name)}",
            f"ПолучательБанк2={_clean(payee_bank.city)}",
            f"ПолучательБИК={payee_bank.bic}",
            f"ПолучательКорсчет={payee_bank.corr_account or ''}",
            "ВидПлатежа=",
            "ВидОплаты=01",
            "Очередность=5",
            f"НазначениеПлатежа={_clean(r.description)}",
            "КонецДокумента",
        ]
    lines.append("КонецФайла")
    return ("\r\n".join(lines) + "\r\n").encode("cp1251", errors="replace")


def cash_name(db: Session, cfo_id: int) -> str:
    cfo = db.get(Cfo, cfo_id)
    return f"Касса: {cfo.name}" if cfo else "Касса"
