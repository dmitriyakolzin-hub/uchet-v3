"""Реестр накладных поставщиков (docs/TZ.md, раздел 24, «Детали 14.5»).

Накладные загружаются из Excel в формате листа «ПОСТАВЩИКИ» владельца (тип документа, дата, №, сумма, контрагент)
для выбранного юрлица-покупателя. Срок оплаты — дата документа + отсрочка договора, лимит задолженности — в договоре.
Дату оплаты финансовая служба ставит после загрузки: «Запланировать оплату» создаёт заявку (источник «поставщики»,
маршрут финансовый менеджер → финансовый директор); сумма разносится по накладным от старых к новым, можно частично.
"""

import datetime as dt
import io
import re
import zipfile
from collections import Counter as Count
from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Contract,
    Counterparty,
    LegalEntity,
    PaymentRequest,
    SupplierInvoice,
    SupplierPayment,
    User,
)
from app.services import approval
from app.services import payment_requests as service
from app.services.articles import system_article
from app.services.excel_import import ExcelError, RowProblem, _date, _decimal, _digits, _text

ZERO = Decimal("0.00")
MAX_ROWS = 20000
KIND_TITLES = {"invoice": "Накладная", "advance": "Аванс"}
# заголовок колонки (без регистра) → ключ; «Сумма…» — любая колонка, начинающаяся с «сумма»
HEADERS = {"тип документа": "kind", "вид документа": "kind", "дата документа": "date", "дата": "date",
           "№ документа": "number", "номер документа": "number", "№": "number", "контрагент": "counterparty",
           "поставщик": "counterparty", "инн": "inn", "инн контрагента": "inn", "инн поставщика": "inn",
           "юрлицо": "entity", "юрлицо (инн)": "entity", "организация": "entity", "покупатель": "entity"}
REQUIRED = {"date": "Дата документа", "number": "№ документа", "amount": "Сумма", "counterparty": "Контрагент"}
LEGAL_FORMS = re.compile(r"^(ооо|ао|пао|зао|оао|ип|нао)\s+|\s+(ооо|ао|пао|зао|оао|ип)$")


class SupplierError(ValueError):
    pass


@dataclass
class ImportResult:
    rows: int = 0
    created: int = 0
    duplicates: int = 0
    zero: int = 0  # строки с нулевой суммой пропускаются
    errors: list[tuple[int, str]] = field(default_factory=list)
    missing: list[tuple[str, int]] = field(default_factory=list)  # контрагент не найден: имя, сколько строк


def _norm(value: str) -> str:
    text = re.sub(r"[«»\"'“”]", " ", _text(value).lower().replace("ё", "е"))
    text = re.sub(r"\s+", " ", text).strip()
    return LEGAL_FORMS.sub("", text).strip()


def _kind(value) -> str:
    text = _text(value).lower()
    if not text or "наклад" in text or "упд" in text or "поступлен" in text:
        return "invoice"
    if "аванс" in text or "предоплат" in text:
        return "advance"
    raise RowProblem(f"«Тип документа»: «{_text(value)}» — загружаются накладные и авансы")


def _doc_date(value) -> dt.date:
    """Дата; число-строка вроде «46204» — порядковый номер дня Excel."""
    text = _text(value)
    if isinstance(value, (int, float)) or text.isdigit():
        number = int(float(value)) if not text.isdigit() else int(text)
        if 20000 < number < 80000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=number)
    return _date(value)


def template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "ПОСТАВЩИКИ"
    titles = ["Тип документа", "Дата документа", "№ документа", "Сумма, р.", "Контрагент", "ИНН контрагента"]
    ws.append(titles)
    for i, width in enumerate((22, 14, 14, 14, 32, 16), start=1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = Font(bold=True), PatternFill("solid", fgColor="E8EEF8")
        ws.column_dimensions[cell.column_letter].width = width
    ws.append(["Приходная накладная", "01.11.2026", "1001", 15000, "Поставщик Плюс", ""])
    ws.append(["аванс", "02.11.2026", "17", 5000, "Поставщик Плюс", ""])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _read(raw: bytes) -> list[tuple[int, dict]]:
    """Лист «ПОСТАВЩИКИ» (или первый); строка заголовков — среди первых 30 строк."""
    if not raw.startswith(b"PK"):
        raise ExcelError("Это не файл Excel (.xlsx). Сохраните файл в формате «Книга Excel (.xlsx)»")
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ExcelError(f"Не удалось прочитать файл Excel: {exc}") from None
    try:
        ws = next((w for w in wb.worksheets if "ПОСТАВЩ" in w.title.upper()), wb.worksheets[0])
        positions: dict[str, int] = {}
        result = []
        for number, values in enumerate(ws.iter_rows(values_only=True), start=1):
            if not positions:
                if number > 30:
                    break
                found = {}
                for i, value in enumerate(values):
                    title = _text(value).lower().rstrip("*").strip()
                    key = "amount" if title.startswith("сумма") else HEADERS.get(title)
                    if key and key not in found:
                        found[key] = i
                if {"date", "counterparty"} <= found.keys():
                    missing = [t for k, t in REQUIRED.items() if k not in found]
                    if missing:
                        raise ExcelError("В строке заголовков нет колонок: " + ", ".join(f"«{m}»" for m in missing))
                    positions = found
                continue
            row = {key: values[i] if i < len(values) else None for key, i in positions.items()}
            if all(_text(row.get(k)) == "" for k in REQUIRED):
                continue
            if len(result) >= MAX_ROWS:
                raise ExcelError(f"В файле больше {MAX_ROWS} строк — разделите его на части")
            result.append((number, row))
        if not positions:
            raise ExcelError("Не найдена строка заголовков: нужны колонки «Дата документа», «№ документа», «Сумма…», "
                             "«Контрагент». Скачайте шаблон")
        return result
    finally:
        wb.close()


class _Lookup:
    def __init__(self, db: Session, organization_id: int):
        self.db, self.org = db, organization_id
        self.by_inn: dict[str, Counterparty] = {}
        self.by_name: dict[str, list[Counterparty]] = {}
        for cp in db.scalars(select(Counterparty).where(Counterparty.organization_id == organization_id,
                                                        Counterparty.is_active.is_(True))):
            if cp.inn:
                self.by_inn[cp.inn] = cp
            for name in {_norm(cp.name), _norm(cp.full_name)} - {""}:
                self.by_name.setdefault(name, []).append(cp)
        self.entities = list(db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)))
        self.contracts: dict[tuple[int, int], int | None] = {}

    def counterparty(self, name, inn) -> Counterparty | None:
        inn = _digits(inn)
        if inn:
            if inn not in self.by_inn:
                raise RowProblem(f"Контрагент с ИНН {inn} не найден в справочнике")
            return self.by_inn[inn]
        found = {cp.id: cp for cp in self.by_name.get(_norm(name), [])}
        if len(found) > 1:
            raise RowProblem(f"«{_text(name)}»: несколько контрагентов с таким наименованием — укажите ИНН")
        return next(iter(found.values()), None)

    def entity(self, value, default: LegalEntity | None) -> LegalEntity:
        text = _text(value)
        if not text:
            if default is None:
                raise RowProblem("Не указано юрлицо-покупатель")
            return default
        digits = _digits(value)
        for e in self.entities:
            if digits == e.inn or _norm(text) in {_norm(e.short_name), _norm(e.full_name)}:
                return e
        raise RowProblem(f"Юрлицо «{text}» не найдено в организации")

    def contract(self, entity_id: int, counterparty_id: int) -> int | None:
        key = (entity_id, counterparty_id)
        if key not in self.contracts:
            found = list(self.db.scalars(select(Contract).where(
                Contract.legal_entity_id == entity_id, Contract.counterparty_id == counterparty_id,
                Contract.kind == "supplier", Contract.is_active.is_(True))))
            with_terms = [c for c in found if c.deferral_days is not None or c.debt_limit is not None]
            pick = found if len(found) == 1 else with_terms
            self.contracts[key] = pick[0].id if len(pick) == 1 else None
        return self.contracts[key]


def import_invoices(db: Session, organization_id: int, user: User, raw: bytes,
                    legal_entity_id: int | None) -> ImportResult:
    lookup = _Lookup(db, organization_id)
    default = next((e for e in lookup.entities if e.id == legal_entity_id), None)
    if legal_entity_id is not None and default is None:
        raise SupplierError("Юрлицо не найдено в этой организации")
    rows = _read(raw)
    existing = set(db.execute(select(SupplierInvoice.legal_entity_id, SupplierInvoice.counterparty_id,
                                     SupplierInvoice.kind, SupplierInvoice.number, SupplierInvoice.doc_date)
                              .where(SupplierInvoice.organization_id == organization_id)).all())
    result = ImportResult(rows=len(rows))
    missing: Count = Count()
    for number, row in rows:
        try:
            kind = _kind(row.get("kind"))
            doc_date = _doc_date(row.get("date"))
            doc_number = _text(row.get("number"))
            if not doc_number:
                raise RowProblem("Не указан № документа")
            if len(doc_number) > 50:
                raise RowProblem("№ документа длиннее 50 символов")
            if _text(row.get("amount")) in ("0", "0.0", "0,00", "0.00"):
                result.zero += 1
                continue
            amount = _decimal(row.get("amount"), "Сумма", 2)
            if amount is None:
                raise RowProblem("Не указана сумма")
            entity = lookup.entity(row.get("entity"), default)
            name = _text(row.get("counterparty"))
            if not name and not _digits(row.get("inn")):
                raise RowProblem("Не указан контрагент")
            counterparty = lookup.counterparty(name, row.get("inn"))
            if counterparty is None:
                missing[name] += 1
                continue
        except RowProblem as exc:
            result.errors.append((number, str(exc)))
            continue
        key = (entity.id, counterparty.id, kind, doc_number, doc_date)
        if key in existing:
            result.duplicates += 1
            continue
        existing.add(key)
        db.add(SupplierInvoice(organization_id=organization_id, legal_entity_id=entity.id,
                               counterparty_id=counterparty.id, contract_id=lookup.contract(entity.id, counterparty.id),
                               kind=kind, number=doc_number, doc_date=doc_date, amount=amount, created_by=user.id))
        result.created += 1
    result.missing = sorted(missing.items())
    db.flush()
    return result


# --- Состояние накладных ---


@dataclass
class InvoiceState:
    invoice: SupplierInvoice
    due: dt.date
    paid: Decimal = ZERO
    planned: Decimal = ZERO  # в неоплаченных заявках (черновик, на согласовании, согласована)
    requests: list[tuple[PaymentRequest, Decimal]] = field(default_factory=list)

    @property
    def debt(self) -> Decimal:
        return self.invoice.amount - self.paid

    @property
    def remaining(self) -> Decimal:
        """Ещё не запланировано к оплате."""
        return self.invoice.amount - self.paid - self.planned


def states(db: Session, invoices: list[SupplierInvoice]) -> list[InvoiceState]:
    contracts = {c.id: c for c in db.scalars(select(Contract).where(
        Contract.id.in_({i.contract_id for i in invoices if i.contract_id})))}
    result = {}
    for inv in invoices:
        contract = contracts.get(inv.contract_id)
        deferral = contract.deferral_days if contract is not None and inv.kind == "invoice" else None
        result[inv.id] = InvoiceState(inv, inv.doc_date + dt.timedelta(days=deferral or 0))
    rows = db.execute(select(SupplierPayment, PaymentRequest).join(PaymentRequest, PaymentRequest.id == SupplierPayment.request_id)
                      .where(SupplierPayment.invoice_id.in_(result)).order_by(PaymentRequest.pay_date, PaymentRequest.id)).all()
    for alloc, req in rows:
        st = result[alloc.invoice_id]
        if req.status == "paid":
            st.paid += alloc.amount
        elif req.status != "rejected":
            st.planned += alloc.amount
        st.requests.append((req, alloc.amount))
    return sorted(result.values(), key=lambda s: (s.due, s.invoice.doc_date, s.invoice.id))


def invoices_of(db: Session, organization_id: int, legal_entity_id: int | None = None,
                counterparty_id: int | None = None) -> list[SupplierInvoice]:
    query = select(SupplierInvoice).where(SupplierInvoice.organization_id == organization_id)
    if legal_entity_id:
        query = query.where(SupplierInvoice.legal_entity_id == legal_entity_id)
    if counterparty_id:
        query = query.where(SupplierInvoice.counterparty_id == counterparty_id)
    return list(db.scalars(query))


def plan_payment(db: Session, organization_id: int, user: User, legal_entity_id: int, counterparty_id: int,
                 pay_date: dt.date, amount: Decimal | None = None, invoice_ids: list[int] | None = None,
                 bank_account_id: int | None = None) -> PaymentRequest:
    """Заявка на оплату накладных: сумма разносится от старых к новым (по сроку оплаты)."""
    invoices = invoices_of(db, organization_id, legal_entity_id, counterparty_id)
    if invoice_ids:
        chosen = [i for i in invoices if i.id in set(invoice_ids)]
        if len(chosen) != len(set(invoice_ids)):
            raise SupplierError("Накладные — одного юрлица и одного контрагента")
        invoices = chosen
    open_ = [s for s in states(db, invoices) if s.remaining > 0]
    total = sum((s.remaining for s in open_), ZERO)
    if total <= 0:
        raise SupplierError("Нет неоплаченных накладных, которые ещё не запланированы")
    amount = total if amount is None else amount
    if amount <= 0:
        raise SupplierError("Сумма должна быть больше нуля")
    if amount > total:
        raise SupplierError(f"Сумма больше долга по выбранным накладным ({total:.2f})")
    allocations, left = [], amount
    for st in open_:
        if left <= 0:
            break
        part = min(st.remaining, left)
        allocations.append((st.invoice, part))
        left -= part
    contracts = {inv.contract_id for inv, _ in allocations}
    article = system_article(db, organization_id, "default_out")
    names = ", ".join(f"{'аванс ' if inv.kind == 'advance' else ''}№ {inv.number} от {inv.doc_date:%d.%m.%Y}"
                      for inv, _ in allocations)
    req = PaymentRequest(organization_id=organization_id, number=service.next_number(db, organization_id),
                         direction="out", source="supplier", initiator="finance", status="draft", pay_date=pay_date,
                         legal_entity_id=legal_entity_id, bank_account_id=bank_account_id,
                         counterparty_id=counterparty_id, contract_id=contracts.pop() if len(contracts) == 1 else None,
                         article_id=article.id if article else None, amount=amount, created_by=user.id,
                         description=f"Оплата по накладным {names}"[:2000])
    try:
        service.validate(db, req, None)
    except service.RequestError as exc:
        raise SupplierError(str(exc)) from None
    db.add(req)
    db.flush()
    for inv, part in allocations:
        db.add(SupplierPayment(invoice_id=inv.id, request_id=req.id, amount=part))
    approval.event(db, req, user, "created", "по реестру накладных")
    approval.submit(db, req, user)
    db.flush()
    return req


def release(db: Session, req: PaymentRequest) -> None:
    """Заявку отклонили — накладные снова ждут оплаты (связи удаляются)."""
    for alloc in db.scalars(select(SupplierPayment).where(SupplierPayment.request_id == req.id)):
        db.delete(alloc)
