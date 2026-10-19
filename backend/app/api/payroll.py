"""Зарплата (docs/TZ.md, раздел 14). Оклады видят и меняют те, кто правит справочники (EDIT_REFERENCE)."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Employee, EmployeeSalary, User
from app.schemas.reference import SalaryIn, SalaryOut

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
