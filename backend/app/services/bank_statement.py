"""Разбор банковской выписки в формате обмена с 1С (1CClientBankExchange, файл kl_to_1c.txt).

Только чтение файла, без базы. Формат — строки «Ключ=Значение», секции:
  СекцияРасчСчет … КонецРасчСчет   — остатки и обороты по счёту (бывает по секции на каждый день)
  СекцияДокумент=<вид> … КонецДокумента — платёжный документ
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


class StatementError(ValueError):
    """Файл не похож на выписку или повреждён; текст — для пользователя."""


@dataclass
class AccountSection:
    account: str
    date_from: dt.date | None
    date_to: dt.date | None
    opening: Decimal | None
    closing: Decimal | None
    total_in: Decimal
    total_out: Decimal


@dataclass
class Document:
    kind: str  # «Платежное поручение», «Банковский ордер» …
    number: str
    doc_date: dt.date
    amount: Decimal
    payer_account: str
    payer_name: str
    payer_inn: str
    payee_account: str
    payee_name: str
    payee_inn: str
    debited_on: dt.date | None  # ДатаСписано
    credited_on: dt.date | None  # ДатаПоступило
    purpose: str
    payment_kind: str  # ВидОплаты: 01 — платёжное поручение, 17 — банковский ордер (комиссии) …
    kbk: str  # ПоказательКБК — заполнен у налоговых платежей


@dataclass
class Statement:
    accounts: list[str]  # РасчСчет из заголовка
    date_from: dt.date | None
    date_to: dt.date | None
    sections: list[AccountSection] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)

    def balances(self, account: str) -> tuple[Decimal | None, Decimal | None, Decimal, Decimal]:
        """Остаток на начало, на конец и обороты по счёту за весь период выписки."""
        own = sorted((s for s in self.sections if s.account == account), key=lambda s: s.date_from or dt.date.min)
        if not own:
            return None, None, Decimal(0), Decimal(0)
        return (
            own[0].opening,
            own[-1].closing,
            sum((s.total_in for s in own), Decimal(0)),
            sum((s.total_out for s in own), Decimal(0)),
        )


def decode(raw: bytes) -> str:
    """Кодировка — Windows-1251 (как правило) или UTF-8."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    return text


def _date(value: str) -> dt.date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        raise StatementError(f"Неверная дата в выписке: {value}") from None


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", ".") or "0")
    except InvalidOperation:
        raise StatementError(f"Неверная сумма в выписке: {value}") from None


def parse(raw: bytes) -> Statement:
    text = decode(raw)
    lines = [line.strip() for line in text.splitlines()]
    if not lines or lines[0] != "1CClientBankExchange":
        raise StatementError("Это не выписка в формате 1С: файл должен начинаться со строки 1CClientBankExchange")

    header: dict[str, str] = {}
    header_accounts: list[str] = []
    statement = Statement(accounts=header_accounts, date_from=None, date_to=None)
    current: dict[str, str] | None = None
    current_kind: str | None = None  # "account" или вид документа

    for line in lines[1:]:
        if not line:
            continue
        key, _, value = line.partition("=")
        if key == "СекцияРасчСчет":
            current, current_kind = {}, "account"
        elif key == "КонецРасчСчет" and current_kind == "account":
            statement.sections.append(AccountSection(
                account=current.get("РасчСчет", ""),
                date_from=_date(current.get("ДатаНачала", "")),
                date_to=_date(current.get("ДатаКонца", "")),
                opening=_amount(current["НачальныйОстаток"]) if current.get("НачальныйОстаток") else None,
                closing=_amount(current["КонечныйОстаток"]) if current.get("КонечныйОстаток") else None,
                total_in=_amount(current.get("ВсегоПоступило", "0")),
                total_out=_amount(current.get("ВсегоСписано", "0")),
            ))
            current, current_kind = None, None
        elif key == "СекцияДокумент":
            current, current_kind = {}, value or "Документ"
        elif key == "КонецДокумента" and current is not None and current_kind != "account":
            statement.documents.append(_document(current_kind, current))
            current, current_kind = None, None
        elif key == "КонецФайла":
            break
        elif current is not None:
            current[key] = value.strip()
        elif key == "РасчСчет":
            header_accounts.append(value.strip())
        else:
            header[key] = value.strip()

    statement.date_from = _date(header.get("ДатаНачала", ""))
    statement.date_to = _date(header.get("ДатаКонца", ""))
    if not header_accounts and not statement.sections:
        raise StatementError("В выписке не указан расчётный счёт")
    return statement


def _document(kind: str, f: dict[str, str]) -> Document:
    # Номер счёта бывает в «…Счет» или «…РасчСчет» (для банков-посредников — разные поля)
    return Document(
        kind=kind,
        number=f.get("Номер", ""),
        doc_date=_date(f.get("Дата", "")) or dt.date.min,
        amount=_amount(f.get("Сумма", "0")),
        payer_account=f.get("ПлательщикРасчСчет") or f.get("ПлательщикСчет", ""),
        payer_name=f.get("Плательщик") or f.get("Плательщик1", ""),
        payer_inn=f.get("ПлательщикИНН", ""),
        payee_account=f.get("ПолучательРасчСчет") or f.get("ПолучательСчет", ""),
        payee_name=f.get("Получатель") or f.get("Получатель1", ""),
        payee_inn=f.get("ПолучательИНН", ""),
        debited_on=_date(f.get("ДатаСписано", "")),
        credited_on=_date(f.get("ДатаПоступило", "")),
        purpose=f.get("НазначениеПлатежа", ""),
        payment_kind=f.get("ВидОплаты", ""),
        kbk=f.get("ПоказательКБК", ""),
    )
