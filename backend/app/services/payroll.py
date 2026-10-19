"""Зарплата (docs/TZ.md, раздел 14)."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmployeeSalary


def salary_on(db: Session, employee_id: int, on_date: dt.date) -> Decimal | None:
    """Оклад на дату: последний с датой начала не позже этой даты."""
    return db.scalar(
        select(EmployeeSalary.amount)
        .where(EmployeeSalary.employee_id == employee_id, EmployeeSalary.valid_from <= on_date)
        .order_by(EmployeeSalary.valid_from.desc())
        .limit(1)
    )
