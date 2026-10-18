"""Справочники части 1.3б: банки (общие) и справочники организации."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.api.org_resources import OrgResource, Ref, register
from app.models import Bank, BankAccount, Contract, Counterparty, Currency, LegalEntity, SalaryProject
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


# --- Справочники организации ---

LEGAL_ENTITY = Ref(LegalEntity, "Юрлицо")
BANK = Ref(Bank, "Банк", org_scoped=False)
CURRENCY = Ref(Currency, "Валюта", org_scoped=False)


def _check_counterparty(obj: Counterparty) -> None:
    s.check_inn_for_kind(obj.kind, obj.inn)


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
):
    register(router, resource)
