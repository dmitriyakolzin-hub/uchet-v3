"""Зарплата (docs/TZ.md, раздел 14): история окладов, начисление зарплаты за месяц."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmployeeSalary(Base):
    """Месячный оклад сотрудника с даты; на дату действует последний оклад с valid_from ≤ даты."""

    __tablename__ = "employee_salaries"
    __table_args__ = (
        UniqueConstraint("employee_id", "valid_from", name="uq_employee_salaries_employee_date"),
        CheckConstraint("amount >= 0", name="amount"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    note: Mapped[str] = mapped_column(String(255), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PayrollRun(Base):
    """Начисление зарплаты: юрлицо + месяц. При проведении создаётся одна операция журнала."""

    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "year", "month", name="uq_payroll_runs_entity_month"),
        CheckConstraint("status IN ('draft', 'posted')", name="status"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
        CheckConstraint("norm_days BETWEEN 1 AND 31", name="norm_days"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    norm_days: Mapped[int] = mapped_column(SmallInteger)  # рабочих дней в месяце
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


MONEY = Numeric(18, 2)


class PayrollLine(Base):
    """Строка начисления: сотрудник, дни, начисления, удержания, взносы (рассчитываются на сервере)."""

    __tablename__ = "payroll_lines"
    __table_args__ = (
        UniqueConstraint("run_id", "employee_id", name="uq_payroll_lines_run_employee"),
        CheckConstraint("work_days >= 0", name="work_days"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("payroll_runs.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    salary: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")  # оклад месяца
    work_days: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    # вводятся вручную
    bonus: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    vacation: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    sick: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    other_accrual: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    other_deduction: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    # рассчитываются
    salary_amount: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    accrued: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    ndfl: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    insurance: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    net: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    # суммы (начислено, НДФЛ, взносы) загружены из файла готовыми — не пересчитываются
    manual_totals: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # расчёт по среднему заработку: как получены отпускные и больничные (текст для пользователя)
    vacation_note: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    sick_note: Mapped[str] = mapped_column(String(1000), default="", server_default="")


class PayoutSheet(Base):
    """Ведомость на выплату (аванс или зарплата): черновик → отправлена в банк → оплачена."""

    __tablename__ = "payout_sheets"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_payout_sheets_organization_number"),
        CheckConstraint("kind IN ('advance', 'salary')", name="kind"),
        CheckConstraint("status IN ('draft', 'sent', 'paid')", name="status"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id", ondelete="RESTRICT"))
    number: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(10))
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    pay_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    paid_on: Mapped[date | None] = mapped_column(Date)
    confirmed_by_statement: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PayoutLine(Base):
    __tablename__ = "payout_lines"
    __table_args__ = (
        UniqueConstraint("sheet_id", "employee_id", name="uq_payout_lines_sheet_employee"),
        CheckConstraint("amount > 0", name="amount"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("payout_sheets.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
