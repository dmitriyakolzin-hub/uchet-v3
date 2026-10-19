"""Зарплата (docs/TZ.md, раздел 14)."""

import calendar
import datetime as dt
import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import payroll_rates as rates
from app.core.money import round_money
from app.models import (
    Account,
    Article,
    Employee,
    EmployeeSalary,
    LegalEntity,
    Operation,
    PayrollLine,
    PayrollRun,
    User,
)
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import journal
from app.services.excel_import import Column, RowError, RowProblem, _decimal, _read_rows, _text
from app.services.journal import JournalError


def salary_on(db: Session, employee_id: int, on_date: dt.date) -> Decimal | None:
    """Оклад на дату: последний с датой начала не позже этой даты."""
    return db.scalar(
        select(EmployeeSalary.amount)
        .where(EmployeeSalary.employee_id == employee_id, EmployeeSalary.valid_from <= on_date)
        .order_by(EmployeeSalary.valid_from.desc())
        .limit(1)
    )


# --- Начисление зарплаты за месяц ---

INSURANCE_ARTICLE = "Страховые взносы с оплаты труда"
MONTHS_GENITIVE = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
                   "октябрь", "ноябрь", "декабрь"]
ZERO = Decimal("0.00")


class PayrollError(ValueError):
    """Начисление нельзя выполнить; текст — для пользователя."""


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def default_norm(year: int, month: int) -> int:
    return rates.working_days(*month_bounds(year, month))


def _employee_days(employee: Employee, run: PayrollRun) -> Decimal:
    start, end = month_bounds(run.year, run.month)
    first = max(start, employee.hired_on) if employee.hired_on else start
    last = min(end, employee.fired_on) if employee.fired_on else end
    if (first, last) == (start, end):
        return Decimal(run.norm_days)
    return Decimal(rates.working_days(first, last)) if first <= last else Decimal(0)


def fill(db: Session, run: PayrollRun) -> int:
    """Добавить сотрудников юрлица, работавших в месяце; оклад — на последний день месяца."""
    start, end = month_bounds(run.year, run.month)
    present = set(db.scalars(select(PayrollLine.employee_id).where(PayrollLine.run_id == run.id)))
    employees = db.scalars(select(Employee).where(
        Employee.organization_id == run.organization_id, Employee.legal_entity_id == run.legal_entity_id,
        Employee.is_active, (Employee.hired_on.is_(None)) | (Employee.hired_on <= end),
        (Employee.fired_on.is_(None)) | (Employee.fired_on >= start),
    ).order_by(Employee.full_name))
    added = 0
    for employee in employees:
        if employee.id in present:
            continue
        db.add(PayrollLine(run_id=run.id, employee_id=employee.id, salary=salary_on(db, employee.id, end) or ZERO,
                           work_days=_employee_days(employee, run)))
        added += 1
    db.flush()
    recalc(db, run)
    return added


def _ytd_before(db: Session, run: PayrollRun, employee_id: int) -> tuple[Decimal, Decimal]:
    """Доход и НДФЛ сотрудника по проведённым начислениям этого юрлица с начала года до месяца run."""
    income, tax = db.execute(
        select(func.coalesce(func.sum(PayrollLine.accrued), 0), func.coalesce(func.sum(PayrollLine.ndfl), 0))
        .join(PayrollRun, PayrollRun.id == PayrollLine.run_id)
        .where(PayrollRun.legal_entity_id == run.legal_entity_id, PayrollRun.year == run.year,
               PayrollRun.month < run.month, PayrollRun.status == "posted", PayrollLine.employee_id == employee_id)
    ).one()
    return Decimal(income), Decimal(tax)


def recalc(db: Session, run: PayrollRun) -> None:
    tariff = db.get(LegalEntity, run.legal_entity_id).insurance_tariff
    for line in db.scalars(select(PayrollLine).where(PayrollLine.run_id == run.id)):
        line.salary_amount = round_money(line.salary * line.work_days / run.norm_days)
        line.accrued = line.salary_amount + line.bonus + line.vacation + line.sick + line.other_accrual
        income_before, tax_before = _ytd_before(db, run, line.employee_id)
        line.ndfl = max(ZERO, rates.ndfl_total(income_before + line.accrued) - tax_before) + ZERO
        line.insurance = rates.insurance(tariff, run.year, line.accrued, income_before)
        line.net = line.accrued - line.ndfl - line.other_deduction
    db.flush()


def _posting(debit, credit, amount, debit_an=None, credit_an=None) -> PostingIn:
    return PostingIn(debit_account_id=debit, credit_account_id=credit, amount=amount,
                     debit=debit_an or AnalyticsIn(), credit=credit_an or AnalyticsIn())


def post(db: Session, run: PayrollRun, user: User) -> Operation:
    """Провести: одна операция журнала на последний день месяца (проводки по каждому сотруднику)."""
    if run.status != "draft":
        raise PayrollError("Начисление уже проведено")
    lines = list(db.scalars(select(PayrollLine).where(PayrollLine.run_id == run.id)))
    if not lines:
        raise PayrollError("В начислении нет сотрудников — нажмите «Заполнить»")
    recalc(db, run)
    accounts = {a.code: a.id for a in db.scalars(select(Account))}
    insurance_article = db.scalar(select(Article).where(Article.name == INSURANCE_ARTICLE))
    if insurance_article is None:
        raise PayrollError(f"Нет статьи «{INSURANCE_ARTICLE}» — восстановите её в справочнике статей")
    problems, postings = [], []
    for line in lines:
        employee = db.get(Employee, line.employee_id)
        article = db.get(Article, employee.pl_item_id) if employee.pl_item_id else None
        if employee.cfo_id is None or article is None or article.debit_account_id is None:
            problems.append(employee.full_name)
            continue
        cost = article.debit_account_id
        cost_an = AnalyticsIn(cfo=employee.cfo_id, pl_item=article.id)
        staff = AnalyticsIn(employee=employee.id)
        if line.accrued > 0:
            postings.append(_posting(cost, accounts["70"], line.accrued, cost_an, staff))
        if line.ndfl > 0:
            postings.append(_posting(accounts["70"], accounts["68.01"], line.ndfl, staff))
        if line.insurance > 0:
            postings.append(_posting(cost, accounts["69"], line.insurance,
                                     AnalyticsIn(cfo=employee.cfo_id, pl_item=insurance_article.id)))
        if line.other_deduction > 0:
            postings.append(_posting(accounts["70"], accounts["73"], line.other_deduction, staff, staff))
    if problems:
        raise PayrollError("У сотрудников не заполнены ЦФО или статья затрат (с шаблоном проводки): "
                           + ", ".join(problems))
    if not postings:
        raise PayrollError("Начислять нечего — все суммы нулевые")
    _, end = month_bounds(run.year, run.month)
    body = OperationIn(legal_entity_id=run.legal_entity_id, op_date=end,
                       description=f"Начисление зарплаты за {MONTHS_GENITIVE[run.month - 1]} {run.year}",
                       postings=postings)
    op = Operation(organization_id=run.organization_id, created_by=user.id, status="draft")
    try:
        journal.save_draft(db, op, body, user)
        op.number = journal.next_number(db, run.organization_id)
        db.add(op)
        db.flush()
        journal._event(db, op, user, "created")
        journal.post(db, op, user)
    except JournalError as exc:
        raise PayrollError(f"Проводки не прошли проверку журнала: {exc}") from None
    run.status, run.operation_id = "posted", op.id
    db.flush()
    return op


def unpost(db: Session, run: PayrollRun) -> None:
    """Отменить проведение: операция журнала удаляется, начисление снова черновик."""
    if run.status != "posted":
        raise PayrollError("Начисление не проведено")
    later = db.scalar(select(PayrollRun.id).where(
        PayrollRun.legal_entity_id == run.legal_entity_id, PayrollRun.year == run.year,
        PayrollRun.month > run.month, PayrollRun.status == "posted").limit(1))
    if later:
        raise PayrollError("Сначала отмените проведение начислений следующих месяцев — от них зависит НДФЛ")
    if run.operation_id:
        op = db.get(Operation, run.operation_id)
        if op is not None:
            db.delete(op)
    run.status, run.operation_id = "draft", None
    db.flush()


# --- Начисление в Excel: скачать строки, поправить дни и суммы, загрузить ---

LINE_COLUMNS = [
    ("personnel_number", "Таб. №"), ("employee", "Сотрудник"), ("work_days", "Отработано дней"),
    ("bonus", "Премия"), ("vacation", "Отпускные"), ("sick", "Больничные"),
    ("other_accrual", "Прочие начисления"), ("other_deduction", "Прочие удержания"),
]


def lines_xlsx(db: Session, run: PayrollRun) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = f"Зарплата {run.month:02d}.{run.year}"
    ws.append([title for _, title in LINE_COLUMNS])
    for i in range(1, len(LINE_COLUMNS) + 1):
        ws.cell(row=1, column=i).font = Font(bold=True)
        ws.cell(row=1, column=i).fill = PatternFill("solid", fgColor="E8EEF8")
        ws.column_dimensions[chr(64 + i)].width = 30 if i == 2 else 16
    rows = db.execute(select(PayrollLine, Employee).join(Employee, Employee.id == PayrollLine.employee_id)
                      .where(PayrollLine.run_id == run.id).order_by(Employee.full_name)).all()
    for line, employee in rows:
        ws.append([employee.personnel_number or None, employee.full_name, line.work_days, line.bonus, line.vacation,
                   line.sick, line.other_accrual, line.other_deduction])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def import_lines(db: Session, run: PayrollRun, raw: bytes) -> tuple[int, int, list]:
    """Строка файла: сотрудник (таб. № или ФИО) + дни и суммы; пустая сумма — 0. Сотрудник юрлица,
    которого нет в начислении, добавляется. Возвращает (строк, изменено, ошибки)."""
    columns = [Column(title, key, key == "employee") for key, title in LINE_COLUMNS]
    rows = _read_rows(raw, columns)
    employees = list(db.scalars(select(Employee).where(Employee.organization_id == run.organization_id,
                                                       Employee.legal_entity_id == run.legal_entity_id)))
    lines = {line.employee_id: line for line in db.scalars(select(PayrollLine).where(PayrollLine.run_id == run.id))}
    changed, errors = 0, []
    for number, row in rows:
        try:
            tab, name = _text(row.get("personnel_number")), _text(row.get("employee"))
            found = [e for e in employees if tab and e.personnel_number == tab] or \
                [e for e in employees if e.full_name.strip().lower() == name.lower()]
            if len(found) != 1:
                raise RowProblem(f"Сотрудник «{name or tab}» юрлица не найден" if not found
                                 else f"«{name}»: несколько сотрудников — укажите табельный №")
            employee = found[0]
            values = {}
            for key, title in LINE_COLUMNS[2:]:
                value = row.get(key)
                if key == "work_days":
                    if _text(value) == "":
                        continue
                    days = _decimal(value, title, 2) if _text(value) != "0" else ZERO
                    if days > 31:
                        raise RowProblem("«Отработано дней»: не больше 31")
                    values[key] = days
                else:
                    values[key] = ZERO if _text(value) in ("", "0") else _decimal(value, title, 2)
            line = lines.get(employee.id)
            if line is None:
                line = PayrollLine(run_id=run.id, employee_id=employee.id,
                                   salary=salary_on(db, employee.id, month_bounds(run.year, run.month)[1]) or ZERO,
                                   work_days=_employee_days(employee, run))
                db.add(line)
                lines[employee.id] = line
            for key, value in values.items():
                setattr(line, key, value)
            changed += 1
        except RowProblem as exc:
            errors.append(RowError(number, str(exc)))
    db.flush()
    recalc(db, run)
    return len(rows), changed, errors
