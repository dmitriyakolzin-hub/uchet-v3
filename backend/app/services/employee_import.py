"""Сотрудники в Excel (docs/TZ.md, раздел 17, «Детали 7.1–7.2»): выгрузка списка и загрузка с созданием
и обновлением сотрудников и окладов. Пустая ячейка значение не стирает."""

import datetime as dt
import re
from dataclasses import dataclass, field

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Cfo, Employee, EmployeeSalary, LegalEntity
from app.services.excel_import import (
    Column,
    RowError,
    RowProblem,
    _date,
    _decimal,
    _digits,
    _help_sheet,
    _read_rows,
    _sheet_with_header,
    _text,
    _to_bytes,
)
from app.services.payroll import salary_on

EMPLOYEE_COLUMNS = [
    Column("ФИО", "full_name", True, 32),
    Column("Табельный №", "personnel_number", False, 14, True, "Не повторяется в организации"),
    Column("Внутренний №", "internal_number", False, 14, True, "Второй номер; не повторяется в организации"),
    Column("ИНН", "inn", False, 14, True, "12 цифр"),
    Column("Должность", "position", False, 24),
    Column("Юрлицо (ИНН или название)", "entity", False, 22, True, "Работодатель: ИНН или краткое название юрлица"),
    Column("ЦФО", "cfo", False, 30, False, "Название ЦФО или путь «Компания / Направление / … / Магазин»"),
    Column("Статья затрат", "pl_item", False, 34, False,
           "Статья ОПиУ для зарплаты, например «Оплата труда — коммерческий персонал»"),
    Column("Дата приёма", "hired_on", False, 13, hint="ДД.ММ.ГГГГ"),
    Column("Дата увольнения", "fired_on", False, 13, hint="ДД.ММ.ГГГГ"),
    Column("Оклад", "salary", False, 14, hint="Месячный оклад, ₽"),
    Column("Оклад с даты", "salary_from", False, 13,
           hint="С какой даты действует оклад; пусто — дата приёма, если её нет — 1-е число текущего месяца"),
]


@dataclass
class EmployeesResult:
    rows: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)


def _cfo_labels(db: Session, organization_id: int) -> dict[int, str]:
    cfos = list(db.scalars(select(Cfo).where(Cfo.organization_id == organization_id)))
    names = {c.id: c.name for c in cfos}
    return {c.id: " / ".join(names[int(i)] for i in str(c.path).split(".")) for c in cfos}


def employees_xlsx(db: Session, organization_id: int, with_rows: bool = True) -> bytes:
    """Список сотрудников в формате загрузки (with_rows=False — пустой шаблон)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Сотрудники"
    _sheet_with_header(ws, EMPLOYEE_COLUMNS)
    if with_rows:
        entities = {e.id: e for e in db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id))}
        labels = _cfo_labels(db, organization_id)
        articles = {a.id: a.name for a in db.scalars(select(Article).where(Article.is_pl))}
        today = dt.date.today()
        # строки — по номеру: шаблон заранее размечает текстовые колонки, append ушёл бы ниже разметки
        employees = db.scalars(select(Employee).where(Employee.organization_id == organization_id)
                               .order_by(Employee.full_name))
        for r, e in enumerate(employees, start=2):
            entity = entities.get(e.legal_entity_id)
            values = [e.full_name, e.personnel_number or None, e.internal_number or None, e.inn, e.position or None,
                      entity.inn if entity else None, labels.get(e.cfo_id), articles.get(e.pl_item_id),
                      e.hired_on, e.fired_on, salary_on(db, e.id, today), None]
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                if c in (9, 10):
                    cell.number_format = "DD.MM.YYYY"
    _help_sheet(wb, EMPLOYEE_COLUMNS, [
        "Загрузка сотрудников: одна строка — один сотрудник.",
        "Сотрудник ищется по табельному №, затем по внутреннему №, без номеров — по ФИО.",
        "Нашёлся — обновляются заполненные ячейки (пустая ячейка значение не стирает); не нашёлся — создаётся.",
        "Оклад добавляется в историю окладов с даты «Оклад с даты».",
    ])
    return _to_bytes(wb)


class _Importer:
    def __init__(self, db: Session, organization_id: int):
        self.db, self.org = db, organization_id
        self.employees = list(db.scalars(select(Employee).where(Employee.organization_id == organization_id)))
        self.entities = list(db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)))
        labels = _cfo_labels(db, organization_id)
        self.cfo_by_label = {v.lower(): k for k, v in labels.items()}
        self.cfo_by_name: dict[str, list[int]] = {}
        for cfo_id, label in labels.items():
            self.cfo_by_name.setdefault(label.split(" / ")[-1].lower(), []).append(cfo_id)
        self.articles = {a.name.strip().lower(): a for a in db.scalars(select(Article).where(Article.is_pl))}

    def find(self, tab: str, internal: str, name: str) -> Employee | None:
        by_tab = [e for e in self.employees if tab and e.personnel_number == tab]
        by_internal = [e for e in self.employees if internal and e.internal_number == internal]
        if by_tab and by_internal and by_tab[0] is not by_internal[0]:
            raise RowProblem(f"Табельный № {tab} и внутренний № {internal} — у разных сотрудников")
        if by_tab or by_internal:
            return (by_tab or by_internal)[0]
        if tab or internal:
            return None
        found = [e for e in self.employees if e.full_name.strip().lower() == name.lower()]
        if len(found) > 1:
            raise RowProblem(f"«{name}»: несколько сотрудников с таким ФИО — укажите табельный или внутренний №")
        return found[0] if found else None

    def entity(self, value) -> int:
        text, digits = _text(value), _digits(value)
        found = [e for e in self.entities if digits and e.inn == digits] or \
            [e for e in self.entities if e.short_name.strip().lower() == text.lower()]
        if len(found) != 1:
            raise RowProblem(f"Юрлицо «{text}» не найдено в организации")
        return found[0].id

    def cfo(self, value) -> int:
        text = _text(value)
        cfo_id = self.cfo_by_label.get(text.lower())
        if cfo_id is None:
            found = self.cfo_by_name.get(text.lower(), [])
            if len(found) != 1:
                raise RowProblem(f"ЦФО «{text}» не найден" if not found
                                 else f"ЦФО «{text}»: несколько с таким названием — укажите путь")
            cfo_id = found[0]
        return cfo_id

    def article(self, value) -> int:
        article = self.articles.get(_text(value).lower())
        if article is None:
            raise RowProblem(f"Статья затрат «{_text(value)}» не найдена среди статей ОПиУ")
        return article.id


def import_employees(db: Session, organization_id: int, raw: bytes) -> EmployeesResult:
    rows = _read_rows(raw, EMPLOYEE_COLUMNS)
    result = EmployeesResult(rows=len(rows))
    imp = _Importer(db, organization_id)
    today = dt.date.today()
    for number, row in rows:
        creating = False
        try:
            with db.begin_nested():
                name = _text(row.get("full_name"))[:255]
                if not name:
                    raise RowProblem("Не указано ФИО")
                tab, internal = _text(row.get("personnel_number"))[:50], _text(row.get("internal_number"))[:50]
                employee = imp.find(tab, internal, name)
                creating = employee is None
                if creating:
                    employee = Employee(organization_id=organization_id, full_name=name)
                values: dict = {"full_name": name}
                if tab:
                    values["personnel_number"] = tab
                if internal:
                    values["internal_number"] = internal
                inn = _digits(row.get("inn"))
                if inn:
                    if not re.fullmatch(r"\d{12}", inn):
                        raise RowProblem("«ИНН»: у сотрудника 12 цифр")
                    values["inn"] = inn
                if _text(row.get("position")):
                    values["position"] = _text(row.get("position"))[:255]
                if _text(row.get("entity")):
                    values["legal_entity_id"] = imp.entity(row.get("entity"))
                if _text(row.get("cfo")):
                    values["cfo_id"] = imp.cfo(row.get("cfo"))
                if _text(row.get("pl_item")):
                    values["pl_item_id"] = imp.article(row.get("pl_item"))
                for key in ("hired_on", "fired_on"):
                    if _text(row.get(key)):
                        values[key] = _date(row.get(key))
                for key, value in values.items():
                    setattr(employee, key, value)
                if employee.hired_on and employee.fired_on and employee.fired_on < employee.hired_on:
                    raise RowProblem("Дата увольнения раньше даты приёма")
                # номера не должны совпасть с другими сотрудниками
                for attr, title in (("personnel_number", "Табельный №"), ("internal_number", "Внутренний №")):
                    value = getattr(employee, attr)
                    if value and any(e is not employee and getattr(e, attr) == value for e in imp.employees):
                        raise RowProblem(f"«{title}» {value} уже у другого сотрудника")
                if creating:
                    db.add(employee)
                db.flush()
                if _text(row.get("salary")):
                    amount = _decimal(row.get("salary"), "Оклад", 2)
                    if amount < 0:
                        raise RowProblem("«Оклад» не может быть отрицательным")
                    since = (_date(row.get("salary_from")) if _text(row.get("salary_from"))
                             else employee.hired_on or today.replace(day=1))
                    salary = db.scalar(select(EmployeeSalary).where(EmployeeSalary.employee_id == employee.id,
                                                                    EmployeeSalary.valid_from == since))
                    if salary is None:
                        db.add(EmployeeSalary(employee_id=employee.id, valid_from=since, amount=amount,
                                              note="Загрузка из Excel"))
                    else:
                        salary.amount = amount
                    db.flush()
        except RowProblem as exc:
            result.errors.append(RowError(number, str(exc)))
            continue
        if creating:
            imp.employees.append(employee)
            result.created.append(name)
        else:
            result.updated.append(name)
    return result
