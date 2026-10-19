"""Зарплата (docs/TZ.md, раздел 14): история окладов сотрудников."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Identity, Numeric, String, UniqueConstraint, func
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
