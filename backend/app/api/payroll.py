"""Зарплата (docs/TZ.md, раздел 14). Оклады видят и меняют те, кто правит справочники (EDIT_REFERENCE)."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import (
    BankAccount,
    Employee,
    EmployeeSalary,
    LegalEntity,
    PayoutLine,
    PayoutSheet,
    PayrollLine,
    PayrollRun,
    User,
)
from app.schemas.reference import SalaryIn, SalaryOut
from app.services import payroll

router = APIRouter(prefix="/api", tags=["Зарплата"])


def _employee(db: Session, user: User, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")
    if employee is None:
        raise not_found
    try:
        get_organization_for(db, user, employee.organization_id, Perm.EDIT_REFERENCE)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return employee


@router.get("/employees/{employee_id}/salaries", response_model=list[SalaryOut])
def list_salaries(employee_id: int, user: CurrentUser, db: DbSession):
    _employee(db, user, employee_id)
    return db.scalars(select(EmployeeSalary).where(EmployeeSalary.employee_id == employee_id)
                      .order_by(EmployeeSalary.valid_from.desc()))


@router.post("/employees/{employee_id}/salaries", response_model=SalaryOut, status_code=201)
def add_salary(employee_id: int, body: SalaryIn, user: CurrentUser, db: DbSession):
    """Новый оклад с даты (повышение, изменение условий)."""
    _employee(db, user, employee_id)
    salary = EmployeeSalary(employee_id=employee_id, **body.model_dump())
    db.add(salary)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Оклад с этой даты уже есть — удалите его или выберите другую дату") from None
    db.refresh(salary)
    return salary


@router.delete("/employee-salaries/{salary_id}", status_code=204)
def delete_salary(salary_id: int, user: CurrentUser, db: DbSession) -> None:
    salary = db.get(EmployeeSalary, salary_id)
    if salary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Оклад не найден")
    try:
        _employee(db, user, salary.employee_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Оклад не найден") from None
        raise
    db.delete(salary)
    db.commit()


# --- Начисление зарплаты за месяц: только финансовый директор и администратор (право POST) ---

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class RunCreate(BaseModel):
    legal_entity_id: int
    year: Annotated[int, Field(ge=2000, le=2100)]
    month: Annotated[int, Field(ge=1, le=12)]


class RunUpdate(BaseModel):
    norm_days: Annotated[int, Field(ge=1, le=31)]


class LineUpdate(BaseModel):
    work_days: Annotated[Decimal, Field(ge=0, le=31, max_digits=5, decimal_places=2)] | None = None
    salary: Money | None = None
    bonus: Money | None = None
    vacation: Money | None = None
    sick: Money | None = None
    other_accrual: Money | None = None
    other_deduction: Money | None = None


class LineOut(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    personnel_number: str
    salary: Decimal
    work_days: Decimal
    salary_amount: Decimal
    bonus: Decimal
    vacation: Decimal
    sick: Decimal
    other_accrual: Decimal
    accrued: Decimal
    ndfl: Decimal
    other_deduction: Decimal
    insurance: Decimal
    net: Decimal
    vacation_note: str = ""
    sick_note: str = ""


class RunOut(BaseModel):
    id: int
    organization_id: int
    legal_entity_id: int
    legal_entity_name: str
    insurance_tariff: str
    year: int
    month: int
    norm_days: int
    status: str
    operation_id: int | None
    totals: dict[str, Decimal] = {}
    lines: list[LineOut] = []


TOTAL_FIELDS = ["salary_amount", "bonus", "vacation", "sick", "other_accrual", "accrued", "ndfl",
                "other_deduction", "insurance", "net"]


def _run_out(db: Session, run: PayrollRun, with_lines: bool = True) -> RunOut:
    db.expire_all()  # суммы — как записаны в базе (15000 → 15000.00)
    entity = db.get(LegalEntity, run.legal_entity_id)
    lines = []
    rows = db.execute(select(PayrollLine, Employee).join(Employee, Employee.id == PayrollLine.employee_id)
                      .where(PayrollLine.run_id == run.id).order_by(Employee.full_name)).all()
    totals = {f: sum((getattr(line, f) for line, _ in rows), Decimal("0.00")) for f in TOTAL_FIELDS}
    if with_lines:
        lines = [LineOut(id=line.id, employee_id=e.id, employee_name=e.full_name, personnel_number=e.personnel_number,
                         vacation_note=line.vacation_note, sick_note=line.sick_note,
                         **{f: getattr(line, f) for f in ["salary", "work_days", *TOTAL_FIELDS]}) for line, e in rows]
    return RunOut(id=run.id, organization_id=run.organization_id, legal_entity_id=run.legal_entity_id,
                  legal_entity_name=entity.short_name, insurance_tariff=entity.insurance_tariff, year=run.year,
                  month=run.month, norm_days=run.norm_days, status=run.status, operation_id=run.operation_id,
                  totals=totals, lines=lines)


def _run(db: Session, user: User, run_id: int) -> PayrollRun:
    run = db.get(PayrollRun, run_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Начисление не найдено")
    if run is None:
        raise not_found
    try:
        get_organization_for(db, user, run.organization_id, Perm.POST)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return run


def _draft(run: PayrollRun) -> None:
    if run.status != "draft":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Начисление проведено — сначала отмените проведение")


@router.get("/organizations/{organization_id}/payroll-runs", response_model=list[RunOut])
def list_runs(organization_id: int, user: CurrentUser, db: DbSession, year: int | None = None):
    get_organization_for(db, user, organization_id, Perm.POST)
    query = select(PayrollRun).where(PayrollRun.organization_id == organization_id)
    if year is not None:
        query = query.where(PayrollRun.year == year)
    runs = db.scalars(query.order_by(PayrollRun.year.desc(), PayrollRun.month.desc(), PayrollRun.legal_entity_id))
    return [_run_out(db, r, with_lines=False) for r in runs]


@router.post("/organizations/{organization_id}/payroll-runs", response_model=RunOut, status_code=201)
def create_run(organization_id: int, body: RunCreate, user: CurrentUser, db: DbSession):
    """Новое начисление за месяц; сразу заполняется сотрудниками юрлица."""
    get_organization_for(db, user, organization_id, Perm.POST)
    entity = db.get(LegalEntity, body.legal_entity_id)
    if entity is None or entity.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Юрлицо не найдено в этой организации")
    run = PayrollRun(organization_id=organization_id, legal_entity_id=entity.id, year=body.year, month=body.month,
                     norm_days=payroll.default_norm(body.year, body.month), created_by=user.id)
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Начисление за этот месяц по юрлицу уже есть") from None
    payroll.fill(db, run)
    db.commit()
    return _run_out(db, run)


@router.get("/payroll-runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, user: CurrentUser, db: DbSession):
    return _run_out(db, _run(db, user, run_id))


@router.patch("/payroll-runs/{run_id}", response_model=RunOut)
def update_run(run_id: int, body: RunUpdate, user: CurrentUser, db: DbSession):
    """Поправить норму дней (переносы выходных); отработанные дни при полной норме — тоже."""
    run = _run(db, user, run_id)
    _draft(run)
    old = Decimal(run.norm_days)
    run.norm_days = body.norm_days
    for line in db.scalars(select(PayrollLine).where(PayrollLine.run_id == run.id, PayrollLine.work_days == old)):
        line.work_days = Decimal(body.norm_days)
    payroll.recalc(db, run)
    db.commit()
    return _run_out(db, run)


@router.post("/payroll-runs/{run_id}/fill", response_model=RunOut)
def fill_run(run_id: int, user: CurrentUser, db: DbSession):
    run = _run(db, user, run_id)
    _draft(run)
    payroll.fill(db, run)
    db.commit()
    return _run_out(db, run)


@router.delete("/payroll-runs/{run_id}", status_code=204)
def delete_run(run_id: int, user: CurrentUser, db: DbSession) -> None:
    run = _run(db, user, run_id)
    _draft(run)
    db.delete(run)
    db.commit()


@router.patch("/payroll-lines/{line_id}", response_model=RunOut)
def update_line(line_id: int, body: LineUpdate, user: CurrentUser, db: DbSession):
    """Изменить дни / суммы строки; НДФЛ, взносы и «к выплате» пересчитываются."""
    line = db.get(PayrollLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    run = _run(db, user, line.run_id)
    _draft(run)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(line, field, value)
            if field in ("vacation", "sick"):
                setattr(line, f"{field}_note", "")  # сумма введена вручную — прежний расчёт не действует
    payroll.recalc(db, run)
    db.commit()
    return _run_out(db, run)


class VacationIn(BaseModel):
    days: Annotated[int, Field(ge=1, le=366)]


class SickIn(BaseModel):
    days: Annotated[int, Field(ge=1, le=366)]
    experience: Literal["lt6m", "lt5", "5to8", "ge8"]  # страховой стаж
    earnings: dict[int, Money] = {}  # заработок по годам, если не из программы (прежние работодатели)


def _draft_line(db: Session, user: User, line_id: int) -> PayrollLine:
    line = db.get(PayrollLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    _draft(_run(db, user, line.run_id))
    return line


@router.post("/payroll-lines/{line_id}/vacation", response_model=RunOut)
def calc_vacation(line_id: int, body: VacationIn, user: CurrentUser, db: DbSession):
    """Рассчитать отпускные по среднему заработку за 12 месяцев."""
    line = _draft_line(db, user, line_id)
    payroll.calc_vacation(db, line, body.days)
    db.commit()
    return _run_out(db, db.get(PayrollRun, line.run_id))


@router.post("/payroll-lines/{line_id}/sick", response_model=RunOut)
def calc_sick(line_id: int, body: SickIn, user: CurrentUser, db: DbSession):
    """Рассчитать больничный за счёт работодателя (первые 3 дня) по заработку за 2 года."""
    line = _draft_line(db, user, line_id)
    payroll.calc_sick(db, line, body.days, body.experience, body.earnings)
    db.commit()
    return _run_out(db, db.get(PayrollRun, line.run_id))


@router.delete("/payroll-lines/{line_id}", response_model=RunOut)
def delete_line(line_id: int, user: CurrentUser, db: DbSession):
    line = db.get(PayrollLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    run = _run(db, user, line.run_id)
    _draft(run)
    db.delete(line)
    db.commit()
    return _run_out(db, run)


@router.post("/payroll-runs/{run_id}/post", response_model=RunOut)
def post_run(run_id: int, user: CurrentUser, db: DbSession):
    run = _run(db, user, run_id)
    try:
        payroll.post(db, run, user)
    except payroll.PayrollError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return _run_out(db, run)


@router.post("/payroll-runs/{run_id}/unpost", response_model=RunOut)
def unpost_run(run_id: int, user: CurrentUser, db: DbSession):
    run = _run(db, user, run_id)
    try:
        payroll.unpost(db, run)
    except payroll.PayrollError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return _run_out(db, run)


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/payroll-runs/{run_id}/lines.xlsx")
def download_lines(run_id: int, user: CurrentUser, db: DbSession):
    run = _run(db, user, run_id)
    return Response(payroll.lines_xlsx(db, run), media_type=XLSX_TYPE, headers={
        "Content-Disposition": f'attachment; filename="payroll_{run.year}_{run.month:02d}.xlsx"'})


@router.post("/payroll-runs/{run_id}/lines")
async def upload_lines(run_id: int, request: Request, user: CurrentUser, db: DbSession):
    """Загрузить дни и суммы из Excel (файл как скачан); строки с ошибками не загружаются."""
    from app.services.excel_import import ExcelError

    run = _run(db, user, run_id)
    _draft(run)
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Файл больше 10 МБ")
    try:
        rows, changed, errors = await run_in_threadpool(payroll.import_lines, db, run, raw)
    except ExcelError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return {"rows": rows, "changed": changed, "errors": [e.__dict__ for e in errors], "run": _run_out(db, run)}


# --- Ведомости на выплату ---


class SheetCreate(BaseModel):
    legal_entity_id: int
    bank_account_id: int
    kind: Literal["advance", "salary"]
    year: Annotated[int, Field(ge=2000, le=2100)]
    month: Annotated[int, Field(ge=1, le=12)]
    pay_date: dt.date
    advance_percent: Annotated[Decimal, Field(gt=0, le=100)] = Decimal(50)


class SheetLineUpdate(BaseModel):
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class PayIn(BaseModel):
    paid_on: dt.date


class SheetLineOut(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    personnel_number: str
    amount: Decimal


class SheetOut(BaseModel):
    id: int
    number: int
    legal_entity_id: int
    legal_entity_name: str
    bank_account_id: int
    bank_account_name: str
    kind: str
    year: int
    month: int
    pay_date: dt.date
    status: str
    operation_id: int | None
    paid_on: dt.date | None
    confirmed_by_statement: bool
    total: Decimal
    lines: list[SheetLineOut] = []


def _sheet_out(db: Session, sheet: PayoutSheet, with_lines: bool = True) -> SheetOut:
    db.expire_all()
    entity = db.get(LegalEntity, sheet.legal_entity_id)
    bank = db.get(BankAccount, sheet.bank_account_id)
    lines = []
    if with_lines:
        rows = db.execute(select(PayoutLine, Employee).join(Employee, Employee.id == PayoutLine.employee_id)
                          .where(PayoutLine.sheet_id == sheet.id).order_by(Employee.full_name)).all()
        lines = [SheetLineOut(id=line.id, employee_id=e.id, employee_name=e.full_name,
                              personnel_number=e.personnel_number, amount=line.amount) for line, e in rows]
    return SheetOut(
        id=sheet.id, number=sheet.number, legal_entity_id=entity.id, legal_entity_name=entity.short_name,
        bank_account_id=bank.id, bank_account_name=f"{bank.name} ··{bank.number[-4:]}", kind=sheet.kind,
        year=sheet.year, month=sheet.month, pay_date=sheet.pay_date, status=sheet.status,
        operation_id=sheet.operation_id, paid_on=sheet.paid_on, confirmed_by_statement=sheet.confirmed_by_statement,
        total=payroll.sheet_total(db, sheet), lines=lines,
    )


def _sheet(db: Session, user: User, sheet_id: int) -> PayoutSheet:
    sheet = db.get(PayoutSheet, sheet_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Ведомость не найдена")
    if sheet is None:
        raise not_found
    try:
        get_organization_for(db, user, sheet.organization_id, Perm.POST)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return sheet


def _sheet_status(sheet: PayoutSheet, *allowed: str) -> None:
    if sheet.status not in allowed:
        titles = {"draft": "черновик", "sent": "отправлена в банк", "paid": "оплачена"}
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Ведомость {titles[sheet.status]} — это действие недоступно")


def _payroll_call(db: Session, func, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
    except payroll.PayrollError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return result


@router.get("/organizations/{organization_id}/payout-sheets", response_model=list[SheetOut])
def list_sheets(organization_id: int, user: CurrentUser, db: DbSession, year: int | None = None):
    get_organization_for(db, user, organization_id, Perm.POST)
    query = select(PayoutSheet).where(PayoutSheet.organization_id == organization_id)
    if year is not None:
        query = query.where(PayoutSheet.year == year)
    return [_sheet_out(db, s, with_lines=False)
            for s in db.scalars(query.order_by(PayoutSheet.year.desc(), PayoutSheet.month.desc(), PayoutSheet.number.desc()))]


@router.post("/organizations/{organization_id}/payout-sheets", response_model=SheetOut, status_code=201)
def create_sheet(organization_id: int, body: SheetCreate, user: CurrentUser, db: DbSession):
    """Новая ведомость; сразу заполняется (аванс — доля оклада, зарплата — из проведённого начисления)."""
    get_organization_for(db, user, organization_id, Perm.POST)
    entity = db.get(LegalEntity, body.legal_entity_id)
    bank = db.get(BankAccount, body.bank_account_id)
    if entity is None or entity.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Юрлицо не найдено в этой организации")
    if bank is None or bank.legal_entity_id != entity.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Расчётный счёт должен принадлежать этому юрлицу")
    number = (db.scalar(select(func.max(PayoutSheet.number)).where(PayoutSheet.organization_id == organization_id)) or 0) + 1
    sheet = PayoutSheet(organization_id=organization_id, legal_entity_id=entity.id, bank_account_id=bank.id,
                        number=number, kind=body.kind, year=body.year, month=body.month, pay_date=body.pay_date,
                        created_by=user.id)
    db.add(sheet)
    db.flush()
    _payroll_call(db, payroll.fill_sheet, db, sheet, body.advance_percent)
    return _sheet_out(db, sheet)


@router.get("/payout-sheets/{sheet_id}", response_model=SheetOut)
def get_sheet(sheet_id: int, user: CurrentUser, db: DbSession):
    return _sheet_out(db, _sheet(db, user, sheet_id))


@router.patch("/payout-lines/{line_id}", response_model=SheetOut)
def update_sheet_line(line_id: int, body: SheetLineUpdate, user: CurrentUser, db: DbSession):
    line = db.get(PayoutLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    sheet = _sheet(db, user, line.sheet_id)
    _sheet_status(sheet, "draft")
    line.amount = body.amount
    db.commit()
    return _sheet_out(db, sheet)


@router.delete("/payout-lines/{line_id}", response_model=SheetOut)
def delete_sheet_line(line_id: int, user: CurrentUser, db: DbSession):
    line = db.get(PayoutLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    sheet = _sheet(db, user, line.sheet_id)
    _sheet_status(sheet, "draft")
    db.delete(line)
    db.commit()
    return _sheet_out(db, sheet)


@router.delete("/payout-sheets/{sheet_id}", status_code=204)
def delete_sheet(sheet_id: int, user: CurrentUser, db: DbSession) -> None:
    sheet = _sheet(db, user, sheet_id)
    _sheet_status(sheet, "draft")
    db.delete(sheet)
    db.commit()


@router.post("/payout-sheets/{sheet_id}/send", response_model=SheetOut)
def send_sheet(sheet_id: int, user: CurrentUser, db: DbSession):
    """Отправлена в банк: суммы закрепляются, ведомость ждёт подтверждения выпиской."""
    sheet = _sheet(db, user, sheet_id)
    _sheet_status(sheet, "draft")
    if payroll.sheet_total(db, sheet) <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="В ведомости нет сумм к выплате")
    sheet.status = "sent"
    db.commit()
    return _sheet_out(db, sheet)


@router.post("/payout-sheets/{sheet_id}/unsend", response_model=SheetOut)
def unsend_sheet(sheet_id: int, user: CurrentUser, db: DbSession):
    sheet = _sheet(db, user, sheet_id)
    _sheet_status(sheet, "sent")
    sheet.status = "draft"
    db.commit()
    return _sheet_out(db, sheet)


@router.post("/payout-sheets/{sheet_id}/pay", response_model=SheetOut)
def pay_sheet(sheet_id: int, body: PayIn, user: CurrentUser, db: DbSession):
    """Подтвердить оплату вручную (без выписки)."""
    sheet = _sheet(db, user, sheet_id)
    _payroll_call(db, payroll.pay_sheet, db, sheet, user, body.paid_on)
    return _sheet_out(db, sheet)


@router.post("/payout-sheets/{sheet_id}/unpay", response_model=SheetOut)
def unpay_sheet(sheet_id: int, user: CurrentUser, db: DbSession):
    sheet = _sheet(db, user, sheet_id)
    _payroll_call(db, payroll.unpay_sheet, db, sheet)
    return _sheet_out(db, sheet)


@router.get("/payout-sheets/{sheet_id}/registry.xlsx")
def download_registry(sheet_id: int, user: CurrentUser, db: DbSession):
    sheet = _sheet(db, user, sheet_id)
    return Response(payroll.registry_xlsx(db, sheet), media_type=XLSX_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="registry_{sheet.number}.xlsx"'})
