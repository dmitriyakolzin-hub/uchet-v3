"""Справочники части 1.3б: банки (общие) и справочники организации."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.api.org_resources import OrgResource, Ref, register
from app.models import (
    Article,
    Bank,
    BankAccount,
    Cfo,
    Contract,
    Counterparty,
    Currency,
    Employee,
    LegalEntity,
    Nomenclature,
    SalaryProject,
    Unit,
    Warehouse,
)
from app.models.cfo import CFO_LEVELS
from app.schemas import reference as s

router = APIRouter(prefix="/api", tags=["Справочники"])


# --- Банки: общий справочник, изменяет администратор ---


@router.get("/banks", response_model=list[s.BankOut])
def list_banks(_: CurrentUser, db: DbSession) -> list[Bank]:
    return list(db.scalars(select(Bank).order_by(Bank.name)))


@router.post("/banks", response_model=s.BankOut, status_code=201)
def create_bank(body: s.BankCreate, _: AdminUser, db: DbSession) -> Bank:
    bank = Bank(**body.model_dump())
    db.add(bank)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Банк с таким БИК уже есть") from None
    return bank


@router.patch("/banks/{bank_id}", response_model=s.BankOut)
def update_bank(bank_id: int, body: s.BankUpdate, _: AdminUser, db: DbSession) -> Bank:
    bank = db.get(Bank, bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Банк не найден")
    for name, value in body.model_dump(exclude_unset=True).items():
        if value is not None or name == "corr_account":
            setattr(bank, name, value)
    db.commit()
    return bank


# --- Единицы измерения: общий справочник, изменяет администратор ---


@router.get("/units", response_model=list[s.UnitOut])
def list_units(_: CurrentUser, db: DbSession) -> list[Unit]:
    return list(db.scalars(select(Unit).order_by(Unit.name)))


@router.post("/units", response_model=s.UnitOut, status_code=201)
def create_unit(body: s.UnitCreate, _: AdminUser, db: DbSession) -> Unit:
    unit = Unit(**body.model_dump())
    db.add(unit)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Единица с таким кодом ОКЕИ уже есть") from None
    return unit


@router.patch("/units/{unit_id}", response_model=s.UnitOut)
def update_unit(unit_id: int, body: s.UnitUpdate, _: AdminUser, db: DbSession) -> Unit:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Единица измерения не найдена")
    for name, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(unit, name, value)
    db.commit()
    return unit


# --- Справочники организации ---

LEGAL_ENTITY = Ref(LegalEntity, "Юрлицо")
BANK = Ref(Bank, "Банк", org_scoped=False)
CURRENCY = Ref(Currency, "Валюта", org_scoped=False)


def _check_counterparty(_db, obj: Counterparty, _creating: bool) -> None:
    s.check_inn_for_kind(obj.kind, obj.inn)


def _check_cfo(db: Session, cfo: Cfo, creating: bool) -> None:
    """Уровни строго по порядку: Компания → Направление → Филиал → Магазин → Подразделение учёта."""
    parent = db.get(Cfo, cfo.parent_id) if cfo.parent_id else None
    if creating:
        cfo.level = parent.level + 1 if parent else 1
        if cfo.level > 5:
            raise ValueError(f"Ниже уровня «{CFO_LEVELS[5]}» уровней нет")
    elif cfo.level == 1 and parent is not None:
        raise ValueError("ЦФО уровня «Компания» не может входить в другой ЦФО")
    elif cfo.level > 1 and parent is None:
        raise ValueError(f"У ЦФО уровня «{CFO_LEVELS[cfo.level]}» должен быть вышестоящий ЦФО")
    elif parent is not None and parent.level != cfo.level - 1:
        raise ValueError(
            f"ЦФО уровня «{CFO_LEVELS[cfo.level]}» может входить только в ЦФО уровня "
            f"«{CFO_LEVELS[cfo.level - 1]}»"
        )
    if cfo.legal_entity_id is not None and cfo.level != 1:
        raise ValueError("Юрлицо указывается только у ЦФО уровня «Компания»")
    if not cfo.is_active and not creating:
        active_children = db.scalar(
            select(Cfo.id).where(Cfo.parent_id == cfo.id, Cfo.is_active).limit(1)
        )
        if active_children:
            raise ValueError("Сначала переведите в архив подчинённые ЦФО")

    if creating:
        # id нужен заранее: из него строится путь в дереве
        cfo.id = db.scalar(text("SELECT nextval(pg_get_serial_sequence('cfos', 'id'))"))
    new_path = f"{parent.path}.{cfo.id}" if parent else str(cfo.id)
    old_path = cfo.path
    cfo.path = new_path
    if not creating and old_path != new_path:
        # ЦФО перенесён в другую ветку — переносим и всех подчинённых
        db.execute(
            text(
                "UPDATE cfos SET path = CAST(:new AS ltree) || subpath(path, nlevel(CAST(:old AS ltree))) "
                "WHERE path <@ CAST(:old AS ltree) AND id <> :id"
            ),
            {"new": new_path, "old": old_path, "id": cfo.id},
        )


def _check_employee(db, employee: Employee, _creating: bool) -> None:
    if employee.pl_item_id is not None:
        article = db.get(Article, employee.pl_item_id)
        if article is None or not article.is_pl:
            raise ValueError("Статья затрат — только статья ОПиУ")
    if employee.hired_on and employee.fired_on and employee.fired_on < employee.hired_on:
        raise ValueError("Дата увольнения раньше даты приёма")


for resource in (
    OrgResource(
        path="bank-accounts", model=BankAccount, title="Расчётный счёт",
        create_schema=s.BankAccountCreate, update_schema=s.BankAccountUpdate, out_schema=s.BankAccountOut,
        conflict_message="У этого юрлица уже есть счёт с таким номером",
        order_by=lambda m: [m.name],
        refs={"legal_entity_id": LEGAL_ENTITY, "bank_id": BANK, "currency_id": CURRENCY},
    ),
    OrgResource(
        path="salary-projects", model=SalaryProject, title="Зарплатный проект",
        create_schema=s.SalaryProjectCreate, update_schema=s.SalaryProjectUpdate,
        out_schema=s.SalaryProjectOut, conflict_message="Такой зарплатный проект уже есть",
        order_by=lambda m: [m.name],
        refs={"legal_entity_id": LEGAL_ENTITY, "bank_id": BANK},
    ),
    OrgResource(
        path="counterparties", model=Counterparty, title="Контрагент",
        create_schema=s.CounterpartyCreate, update_schema=s.CounterpartyUpdate,
        out_schema=s.CounterpartyOut, conflict_message="Контрагент с таким ИНН уже есть",
        order_by=lambda m: [m.name], nullable_fields={"inn", "kpp"}, check=_check_counterparty,
    ),
    OrgResource(
        path="contracts", model=Contract, title="Договор",
        create_schema=s.ContractCreate, update_schema=s.ContractUpdate, out_schema=s.ContractOut,
        conflict_message="Такой договор уже есть",
        order_by=lambda m: [m.contract_date.desc().nulls_last(), m.number],
        refs={"counterparty_id": Ref(Counterparty, "Контрагент"), "legal_entity_id": LEGAL_ENTITY,
              "currency_id": CURRENCY},
        nullable_fields={"contract_date"},
    ),
    OrgResource(
        path="cfos", model=Cfo, title="ЦФО",
        create_schema=s.CfoCreate, update_schema=s.CfoUpdate, out_schema=s.CfoOut,
        conflict_message="Такой ЦФО уже есть",
        order_by=lambda m: [m.path],
        refs={"parent_id": Ref(Cfo, "Вышестоящий ЦФО"), "legal_entity_id": LEGAL_ENTITY},
        nullable_fields={"parent_id", "legal_entity_id"}, check=_check_cfo,
    ),
    OrgResource(
        path="employees", model=Employee, title="Сотрудник",
        create_schema=s.EmployeeCreate, update_schema=s.EmployeeUpdate, out_schema=s.EmployeeOut,
        conflict_message="Сотрудник с таким табельным номером уже есть",
        order_by=lambda m: [m.full_name],
        refs={"cfo_id": Ref(Cfo, "ЦФО"), "legal_entity_id": LEGAL_ENTITY,
              "pl_item_id": Ref(Article, "Статья затрат", org_scoped=False)},
        nullable_fields={"inn", "cfo_id", "hired_on", "fired_on", "legal_entity_id", "pl_item_id"},
        check=_check_employee,
    ),
    OrgResource(
        path="nomenclature", model=Nomenclature, title="Номенклатура",
        create_schema=s.NomenclatureCreate, update_schema=s.NomenclatureUpdate,
        out_schema=s.NomenclatureOut, conflict_message="Номенклатура с таким артикулом уже есть",
        order_by=lambda m: [m.name],
        refs={"unit_id": Ref(Unit, "Единица измерения", org_scoped=False)},
    ),
    OrgResource(
        path="warehouses", model=Warehouse, title="Склад",
        create_schema=s.WarehouseCreate, update_schema=s.WarehouseUpdate, out_schema=s.WarehouseOut,
        conflict_message="Такой склад уже есть",
        order_by=lambda m: [m.name],
        refs={"cfo_id": Ref(Cfo, "ЦФО")},
        nullable_fields={"cfo_id"},
    ),
):
    register(router, resource)
