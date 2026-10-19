"""Правила журнала операций (docs/TZ.md, раздел 9).

save_draft     — записать черновик: проверяются только ссылки (существуют, из этой организации)
posting_errors — что мешает провести: полнота аналитики, счета, суммы, количество, юрлицо
post / unpost / cancel / restore — смена статуса с записью в историю
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.analytics import ANALYTIC_TITLES, OPTIONAL_ANALYTICS, AnalyticKind
from app.core.money import BASE_CURRENCY, to_rub
from app.models import (
    Account,
    Article,
    BankAccount,
    Cfo,
    Contract,
    Counterparty,
    Currency,
    Employee,
    Executor,
    FixedAsset,
    LegalEntity,
    Nomenclature,
    Operation,
    OperationEvent,
    Organization,
    Posting,
    User,
    Warehouse,
)
from app.models.journal import ANALYTIC_COLUMNS, analytic_column
from app.schemas.journal import OperationIn
from app.services.rates import RateNotFound, rate_on

REF_MODELS = {
    "bank_account": BankAccount,
    "counterparty": Counterparty,
    "contract": Contract,
    "employee": Employee,
    "nomenclature": Nomenclature,
    "warehouse": Warehouse,
    "fixed_asset": FixedAsset,
    "cfo": Cfo,
    "executor": Executor,
    "cash_flow_item": Article,
    "pl_item": Article,
}
SIDE_TITLES = {"debit": "дебет", "credit": "кредит"}
OPENING_ACCOUNT = "00"  # вспомогательный счёт ввода начальных остатков


class JournalError(ValueError):
    """Операцию нельзя сохранить или провести; текст — для пользователя."""


def _now() -> datetime:
    return datetime.now(UTC)


def _event(db: Session, op: Operation, user: User, action: str) -> None:
    db.add(OperationEvent(operation_id=op.id, user_id=user.id, action=action))


def next_number(db: Session, organization_id: int) -> int:
    # Блокируем строку организации, чтобы два одновременных сохранения не получили один номер
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    current = db.scalar(select(func.max(Operation.number)).where(Operation.organization_id == organization_id))
    return (current or 0) + 1


def base_currency_id(db: Session) -> int:
    return db.scalar(select(Currency.id).where(Currency.code == BASE_CURRENCY))


def _check_ref(db: Session, organization_id: int, kind: str, ref_id: int, where: str) -> None:
    obj = db.get(REF_MODELS[kind], ref_id)
    title = ANALYTIC_TITLES[AnalyticKind(kind)]
    if obj is None or (kind not in ("cash_flow_item", "pl_item") and obj.organization_id != organization_id):
        raise JournalError(f"{where}: {title} не найден(а) в этой организации")
    if kind == "cash_flow_item" and not obj.is_cash_flow:
        raise JournalError(f"{where}: статья «{obj.name}» не является статьёй ДДС")
    if kind == "pl_item" and not obj.is_pl:
        raise JournalError(f"{where}: статья «{obj.name}» не является статьёй ОПиУ")


def save_draft(db: Session, op: Operation, body: OperationIn, user: User) -> None:
    """Заполнить операцию из запроса. Статус не меняется; вызывающий решает, коммитить ли."""
    entity = db.get(LegalEntity, body.legal_entity_id)
    if entity is None or entity.organization_id != op.organization_id:
        raise JournalError("Юрлицо не найдено в этой организации")
    if body.article_id is not None and db.get(Article, body.article_id) is None:
        raise JournalError("Статья не найдена")

    rub_id = base_currency_id(db)
    op.legal_entity_id = body.legal_entity_id
    op.op_date = body.op_date
    if op.is_opening:
        # остатки вводятся строго на дату начальных остатков организации
        opening_date = db.get(Organization, op.organization_id).opening_balance_date
        if opening_date is None:
            raise JournalError("Сначала задайте дату начальных остатков организации")
        op.op_date = opening_date
    op.article_id = body.article_id
    op.description = body.description
    if op.id is not None:
        op.updated_by = user.id
        op.updated_at = _now()

    postings = []
    for line_no, p in enumerate(body.postings, start=1):
        where = f"Проводка {line_no}"
        for account_id in (p.debit_account_id, p.credit_account_id):
            if account_id is not None and db.get(Account, account_id) is None:
                raise JournalError(f"{where}: счёт не найден")
        currency_id = p.currency_id or rub_id
        currency = db.get(Currency, currency_id)
        if currency is None:
            raise JournalError(f"{where}: валюта не найдена")

        posting = Posting(
            line_no=line_no,
            debit_account_id=p.debit_account_id,
            credit_account_id=p.credit_account_id,
            amount=p.amount,
            currency_id=currency_id,
            quantity=p.quantity,
        )
        for side in ("debit", "credit"):
            for kind, ref_id in getattr(p, side).model_dump().items():
                if ref_id is not None:
                    _check_ref(db, op.organization_id, kind, ref_id, f"{where}, {SIDE_TITLES[side]}")
                setattr(posting, analytic_column(side, kind), ref_id)

        if p.amount_rub is not None:
            posting.amount_rub, posting.amount_rub_manual = p.amount_rub, True
        else:
            posting.amount_rub = _amount_rub(db, currency, p.amount, body.op_date)
        postings.append(posting)

    op.postings = postings


def _amount_rub(db: Session, currency: Currency, amount: Decimal | None, on_date) -> Decimal | None:
    if amount is None:
        return None
    if currency.code == BASE_CURRENCY:
        return amount
    try:
        rate = rate_on(db, currency, on_date)
    except RateNotFound:
        return None  # в черновике можно; провести не дадим
    return to_rub(amount, rate.rate, rate.nominal)


def posting_errors(db: Session, op: Operation) -> list[str]:
    """Что мешает провести операцию. Пустой список — можно проводить."""
    errors: list[str] = []
    if not op.postings:
        return ["Нет ни одной проводки"]
    parents = set(db.scalars(select(Account.parent_id).where(Account.parent_id.is_not(None))))
    opening_date = db.get(Organization, op.organization_id).opening_balance_date
    if not op.is_opening and opening_date is not None and op.op_date <= opening_date:
        errors.append(
            f"Дата операции должна быть позже даты начальных остатков ({opening_date:%d.%m.%Y})"
        )

    for p in op.postings:
        where = f"Проводка {p.line_no}"
        if p.amount is None:
            errors.append(f"{where}: не указана сумма")
        elif p.amount_rub is None:
            currency = db.get(Currency, p.currency_id)
            errors.append(f"{where}: нет курса {currency.code} на {op.op_date:%d.%m.%Y} — введите курс или сумму в рублях")

        codes = [db.get(Account, a).code for a in (p.debit_account_id, p.credit_account_id) if a is not None]
        if op.is_opening and len(codes) == 2 and codes.count(OPENING_ACCOUNT) != 1:
            errors.append(f"{where}: в проводке ввода остатков одна из сторон — счёт 00")
        if not op.is_opening and OPENING_ACCOUNT in codes:
            errors.append(f"{where}: счёт 00 используется только для ввода начальных остатков")

        quantitative = False
        for side, account_id in (("debit", p.debit_account_id), ("credit", p.credit_account_id)):
            side_where = f"{where}, {SIDE_TITLES[side]}"
            if account_id is None:
                errors.append(f"{side_where}: не указан счёт")
                continue
            account = db.get(Account, account_id)
            if not account.is_active:
                errors.append(f"{side_where}: счёт {account.code} в архиве")
            if account.id in parents:
                errors.append(f"{side_where}: по счёту {account.code} проводить нельзя — выберите субсчёт")
            quantitative |= account.is_quantitative

            required = [a.kind for a in account.analytics]
            for kind in ANALYTIC_COLUMNS:
                value = getattr(p, analytic_column(side, kind))
                title = ANALYTIC_TITLES[AnalyticKind(kind)]
                if kind in required and value is None:
                    errors.append(f"{side_where} {account.code}: не заполнено «{title}»")
                elif kind not in required and value is not None and OPTIONAL_ANALYTICS.get(kind) not in required:
                    errors.append(f"{side_where} {account.code}: «{title}» на этом счёте не ведётся")

            errors.extend(_entity_errors(db, op, p, side, side_where))

        if quantitative and p.quantity is None:
            errors.append(f"{where}: на количественном счёте нужно указать количество")
        if not quantitative and p.quantity is not None:
            errors.append(f"{where}: количество указывается только на количественных счетах (10, 41, 43)")
    return errors


def _entity_errors(db: Session, op: Operation, p: Posting, side: str, where: str) -> list[str]:
    """Договор и расчётный счёт — того же юрлица, что и операция; договор — с тем же контрагентом."""
    errors = []
    contract_id = getattr(p, analytic_column(side, "contract"))
    bank_account_id = getattr(p, analytic_column(side, "bank_account"))
    counterparty_id = getattr(p, analytic_column(side, "counterparty"))
    if contract_id is not None:
        contract = db.get(Contract, contract_id)
        if contract.legal_entity_id != op.legal_entity_id:
            errors.append(f"{where}: договор заключён другим юрлицом")
        if counterparty_id is not None and contract.counterparty_id != counterparty_id:
            errors.append(f"{where}: договор заключён с другим контрагентом")
    if bank_account_id is not None and db.get(BankAccount, bank_account_id).legal_entity_id != op.legal_entity_id:
        errors.append(f"{where}: расчётный счёт принадлежит другому юрлицу")
    return errors


# --- Смена статуса ---


def post(db: Session, op: Operation, user: User) -> None:
    if op.status != "draft":
        raise JournalError("Провести можно только черновик")
    errors = posting_errors(db, op)
    if errors:
        raise JournalError("; ".join(errors))
    op.status, op.posted_by, op.posted_at = "posted", user.id, _now()
    _event(db, op, user, "posted")


def create_posted(db: Session, organization_id: int, body: OperationIn, user: User) -> Operation:
    """Новая проведённая операция (для операций, которые программа создаёт сама: начисления, закрытие)."""
    op = Operation(organization_id=organization_id, created_by=user.id, status="draft")
    save_draft(db, op, body, user)
    op.number = next_number(db, organization_id)
    db.add(op)
    db.flush()
    _event(db, op, user, "created")
    post(db, op, user)
    db.flush()
    return op


def unpost(db: Session, op: Operation, user: User) -> None:
    if op.status != "posted":
        raise JournalError("Вернуть в черновик можно только проведённую операцию")
    op.status, op.posted_by, op.posted_at = "draft", None, None
    _event(db, op, user, "unposted")


def cancel(db: Session, op: Operation, user: User) -> None:
    if op.status == "cancelled":
        raise JournalError("Операция уже аннулирована")
    op.status, op.posted_by, op.posted_at = "cancelled", None, None
    _event(db, op, user, "cancelled")


def restore(db: Session, op: Operation, user: User) -> None:
    if op.status != "cancelled":
        raise JournalError("Восстановить можно только аннулированную операцию")
    op.status = "draft"
    _event(db, op, user, "restored")


TRANSITIONS = {"post": post, "unpost": unpost, "cancel": cancel, "restore": restore}
