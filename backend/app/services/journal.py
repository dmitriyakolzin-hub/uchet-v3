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
    PayoutLine,
    PayoutSheet,
    Posting,
    User,
    Warehouse,
)
from app.models.journal import ANALYTIC_COLUMNS, analytic_column
from app.schemas.journal import OperationIn
from app.services import articles as article_tree
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
    if obj is None or obj.organization_id != organization_id:
        raise JournalError(f"{where}: {title} не найден(а) в этой организации")
    if kind in ("cash_flow_item", "pl_item") and article_tree.is_group(db, obj.id):
        raise JournalError(f"{where}: «{obj.name}» — группа статей, выберите подстатью")
    if kind == "cash_flow_item" and not obj.is_cash_flow:
        raise JournalError(f"{where}: статья «{obj.name}» не является статьёй ДДС")
    if kind == "pl_item" and not obj.is_pl:
        raise JournalError(f"{where}: статья «{obj.name}» не является статьёй ОПиУ")


def save_draft(db: Session, op: Operation, body: OperationIn, user: User) -> None:
    """Заполнить операцию из запроса. Статус не меняется; вызывающий решает, коммитить ли."""
    entity = db.get(LegalEntity, body.legal_entity_id)
    if entity is None or entity.organization_id != op.organization_id:
        raise JournalError("Юрлицо не найдено в этой организации")
    if body.article_id is not None:
        article = db.get(Article, body.article_id)
        if article is None or article.organization_id != op.organization_id:
            raise JournalError("Статья не найдена в этой организации")
        if article_tree.is_group(db, article.id):
            raise JournalError(f"«{article.name}» — группа статей, выберите подстатью")

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
    if "payout_sheet_id" in body.model_fields_set:  # не передано — ссылка на ведомость не меняется
        if body.payout_sheet_id is not None:
            sheet = db.get(PayoutSheet, body.payout_sheet_id)
            if sheet is None or sheet.organization_id != op.organization_id:
                raise JournalError("Ведомость не найдена в этой организации")
            if sheet.legal_entity_id != body.legal_entity_id:
                raise JournalError(f"Ведомость № {sheet.number} другого юрлица")
        op.payout_sheet_id = body.payout_sheet_id
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
    if op.payout_sheet_id is not None:
        errors.extend(_sheet_errors(db, op))
    return errors


def _ru(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _sheet_errors(db: Session, op: Operation) -> list[str]:
    """Выплата по ведомости: суммы Дт 70 по сотрудникам — те же, что в ведомости; ведомость ещё не оплачена другой операцией."""
    sheet = db.get(PayoutSheet, op.payout_sheet_id)
    where = f"Ведомость № {sheet.number}"
    if sheet.status == "paid" and sheet.operation_id not in (None, op.id):
        other = db.get(Operation, sheet.operation_id)
        return [f"{where} уже оплачена операцией № {other.number if other else sheet.operation_id}"]
    expected: dict[int, Decimal] = {}
    for line in db.scalars(select(PayoutLine).where(PayoutLine.sheet_id == sheet.id)):
        expected[line.employee_id] = expected.get(line.employee_id, Decimal(0)) + line.amount
    salary = db.scalar(select(Account.id).where(Account.code == "70"))
    actual: dict[int | None, Decimal] = {}
    for p in op.postings:
        if p.debit_account_id == salary and p.amount is not None:
            actual[p.dt_employee_id] = actual.get(p.dt_employee_id, Decimal(0)) + p.amount
    if actual == expected:
        return []
    errors = []
    total_expected, total_actual = sum(expected.values(), Decimal(0)), sum(actual.values(), Decimal(0))
    if total_expected != total_actual:
        errors.append(f"{where}: итого {_ru(total_expected)}, в операции по счёту 70 — {_ru(total_actual)}")
    names = dict(db.execute(select(Employee.id, Employee.full_name).where(
        Employee.id.in_([k for k in {*expected, *actual} if k is not None]))).all())
    diffs = [k for k in sorted({*expected, *actual}, key=lambda k: names.get(k, "")) if expected.get(k) != actual.get(k)]
    for k in diffs[:5]:
        errors.append(f"{where}, {names.get(k, 'без сотрудника')}: в ведомости {_ru(expected.get(k, Decimal(0)))}, "
                      f"в операции {_ru(actual.get(k, Decimal(0)))}")
    if len(diffs) > 5:
        errors.append(f"{where}: расхождений ещё {len(diffs) - 5}")
    return errors


def _mark_sheet(db: Session, op: Operation, paid: bool) -> None:
    """Проведение отмечает ведомость оплаченной; возврат в черновик и аннулирование — снимают отметку."""
    sheet = db.get(PayoutSheet, op.payout_sheet_id) if op.payout_sheet_id else None
    if sheet is None:
        return
    if paid:
        sheet.status, sheet.operation_id, sheet.paid_on = "paid", op.id, op.op_date
        sheet.confirmed_by_statement = op.import_key is not None  # операция из банковской выписки
    elif sheet.operation_id == op.id:
        sheet.status, sheet.operation_id, sheet.paid_on, sheet.confirmed_by_statement = "sent", None, None, False


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
    _mark_sheet(db, op, paid=True)
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
    _mark_sheet(db, op, paid=False)
    _event(db, op, user, "unposted")


def cancel(db: Session, op: Operation, user: User) -> None:
    if op.status == "cancelled":
        raise JournalError("Операция уже аннулирована")
    op.status, op.posted_by, op.posted_at = "cancelled", None, None
    _mark_sheet(db, op, paid=False)
    _event(db, op, user, "cancelled")


def restore(db: Session, op: Operation, user: User) -> None:
    if op.status != "cancelled":
        raise JournalError("Восстановить можно только аннулированную операцию")
    op.status = "draft"
    _event(db, op, user, "restored")


TRANSITIONS = {"post": post, "unpost": unpost, "cancel": cancel, "restore": restore}
