import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Bic = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{9}$")]
Account20 = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{20}$")]
Kpp = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{9}$")]
Inn = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(\d{10}|\d{12})$")]
CounterpartyKind = Literal["legal", "entrepreneur", "person"]
ContractKind = Literal["customer", "supplier", "other"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def check_inn_for_kind(kind: str | None, inn: str | None) -> None:
    """ИНН: организация — 10 цифр, ИП — 12, физлицо — 12 или не указан."""
    if kind == "legal" and (inn is None or len(inn) != 10):
        raise ValueError("у организации ИНН — 10 цифр")
    if kind == "entrepreneur" and (inn is None or len(inn) != 12):
        raise ValueError("у ИП ИНН — 12 цифр")
    if kind == "person" and inn is not None and len(inn) != 12:
        raise ValueError("у физлица ИНН — 12 цифр")


# --- Банки (общий справочник) ---


class BankCreate(BaseModel):
    bic: Bic
    name: Name
    corr_account: Account20 | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""


class BankUpdate(BaseModel):
    name: Name | None = None
    corr_account: Account20 | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    is_active: bool | None = None


class BankOut(_Out):
    id: int
    bic: str
    name: str
    corr_account: str | None
    city: str
    is_active: bool


# --- Расчётные счета ---


class BankAccountCreate(BaseModel):
    legal_entity_id: int
    bank_id: int
    currency_id: int
    number: Account20
    name: Name


class BankAccountUpdate(BaseModel):
    bank_id: int | None = None
    currency_id: int | None = None
    number: Account20 | None = None
    name: Name | None = None
    is_active: bool | None = None


class BankAccountOut(_Out):
    id: int
    organization_id: int
    legal_entity_id: int
    bank_id: int
    currency_id: int
    number: str
    name: str
    is_active: bool


# --- Зарплатные проекты ---


class SalaryProjectCreate(BaseModel):
    legal_entity_id: int
    bank_id: int
    name: Name


class SalaryProjectUpdate(BaseModel):
    bank_id: int | None = None
    name: Name | None = None
    is_active: bool | None = None


class SalaryProjectOut(_Out):
    id: int
    organization_id: int
    legal_entity_id: int
    bank_id: int
    name: str
    is_active: bool


# --- Контрагенты ---


class CounterpartyCreate(BaseModel):
    name: Name
    full_name: LongText = ""
    kind: CounterpartyKind
    inn: Inn | None = None
    kpp: Kpp | None = None
    is_customer: bool = False
    is_supplier: bool = False

    @model_validator(mode="after")
    def _inn_matches_kind(self):
        check_inn_for_kind(self.kind, self.inn)
        return self


class CounterpartyUpdate(BaseModel):
    name: Name | None = None
    full_name: LongText | None = None
    kind: CounterpartyKind | None = None
    inn: Inn | None = None
    kpp: Kpp | None = None
    is_customer: bool | None = None
    is_supplier: bool | None = None
    is_active: bool | None = None


class CounterpartyOut(_Out):
    id: int
    organization_id: int
    name: str
    full_name: str
    kind: CounterpartyKind
    inn: str | None
    kpp: str | None
    is_customer: bool
    is_supplier: bool
    is_active: bool


# --- Договоры ---


class ContractCreate(BaseModel):
    counterparty_id: int
    legal_entity_id: int
    currency_id: int
    kind: ContractKind
    number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    contract_date: dt.date | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    project_id: int | None = None  # инвестпроект


class ContractUpdate(BaseModel):
    counterparty_id: int | None = None
    legal_entity_id: int | None = None
    currency_id: int | None = None
    kind: ContractKind | None = None
    number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    contract_date: dt.date | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None
    is_active: bool | None = None
    project_id: int | None = None


class ContractOut(_Out):
    id: int
    organization_id: int
    counterparty_id: int
    legal_entity_id: int
    currency_id: int
    kind: ContractKind
    number: str
    contract_date: dt.date | None
    name: str
    project_id: int | None = None
    is_active: bool


# Поля, которые можно очистить (передать null) при изменении
NULLABLE_FIELDS = {"corr_account", "inn", "kpp", "contract_date"}


# --- ЦФО ---


class CfoCreate(BaseModel):
    name: Name
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] = ""  # для загрузок из Excel
    parent_id: int | None = None  # без родителя — ЦФО уровня «Компания»
    legal_entity_id: int | None = None


class CfoUpdate(BaseModel):
    name: Name | None = None
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    parent_id: int | None = None
    legal_entity_id: int | None = None
    is_active: bool | None = None


class CfoOut(_Out):
    id: int
    organization_id: int
    name: str
    code: str = ""
    level: int
    parent_id: int | None
    path: str
    legal_entity_id: int | None
    has_cash: bool = False  # «есть касса» (вкладка «Деньги»)
    is_active: bool


# --- Сотрудники ---

EmployeeInn = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{12}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class EmployeeCreate(BaseModel):
    full_name: Name
    personnel_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] = ""
    internal_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] = ""
    inn: EmployeeInn | None = None
    position: ShortText = ""
    cfo_id: int | None = None
    hired_on: dt.date | None = None
    fired_on: dt.date | None = None
    legal_entity_id: int | None = None  # работодатель
    pl_item_id: int | None = None  # статья затрат ОПиУ для начисленной зарплаты


class EmployeeUpdate(BaseModel):
    full_name: Name | None = None
    personnel_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    internal_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    inn: EmployeeInn | None = None
    position: ShortText | None = None
    cfo_id: int | None = None
    hired_on: dt.date | None = None
    fired_on: dt.date | None = None
    legal_entity_id: int | None = None
    pl_item_id: int | None = None
    is_active: bool | None = None


class EmployeeOut(_Out):
    id: int
    organization_id: int
    full_name: str
    personnel_number: str
    internal_number: str = ""
    inn: str | None
    position: str
    cfo_id: int | None
    hired_on: dt.date | None
    fired_on: dt.date | None
    legal_entity_id: int | None
    pl_item_id: int | None
    is_active: bool


class SalaryIn(BaseModel):
    valid_from: dt.date
    amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
    note: ShortText = ""


class SalaryOut(_Out):
    id: int
    employee_id: int
    valid_from: dt.date
    amount: Decimal
    note: str


# --- Единицы измерения (общий справочник) ---

Okei = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{3,4}$")]
ShortName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
UnitName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class UnitCreate(BaseModel):
    okei: Okei
    short_name: ShortName
    name: UnitName


class UnitUpdate(BaseModel):
    short_name: ShortName | None = None
    name: UnitName | None = None
    is_active: bool | None = None


class UnitOut(_Out):
    id: int
    okei: str
    short_name: str
    name: str
    is_active: bool


# --- Номенклатура и склады ---

NomenclatureKind = Literal["goods", "material", "product", "service"]
Article = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class NomenclatureCreate(BaseModel):
    name: Name
    article: Article = ""
    unit_id: int
    kind: NomenclatureKind


class NomenclatureUpdate(BaseModel):
    name: Name | None = None
    article: Article | None = None
    unit_id: int | None = None
    kind: NomenclatureKind | None = None
    is_active: bool | None = None


class NomenclatureOut(_Out):
    id: int
    organization_id: int
    name: str
    article: str
    unit_id: int
    kind: NomenclatureKind
    is_active: bool


class WarehouseCreate(BaseModel):
    name: Name
    cfo_id: int | None = None


class WarehouseUpdate(BaseModel):
    name: Name | None = None
    cfo_id: int | None = None
    is_active: bool | None = None


class WarehouseOut(_Out):
    id: int
    organization_id: int
    name: str
    cfo_id: int | None
    is_active: bool


# --- Основные средства (карточка; остатки — из журнала) ---

Cost = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
UsefulLife = Annotated[int, Field(ge=1, le=1200)]
InventoryNumber = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]


class FixedAssetCreate(BaseModel):
    name: Name
    inventory_number: InventoryNumber = ""
    legal_entity_id: int
    cost: Cost
    useful_life_months: UsefulLife
    cfo_id: int | None = None
    pl_item_id: int | None = None  # статья амортизации; не указана — «Амортизация основных средств»
    project_id: int | None = None  # инвестпроект


class FixedAssetUpdate(BaseModel):
    name: Name | None = None
    inventory_number: InventoryNumber | None = None
    legal_entity_id: int | None = None
    cost: Cost | None = None
    useful_life_months: UsefulLife | None = None
    cfo_id: int | None = None
    pl_item_id: int | None = None
    project_id: int | None = None
    is_active: bool | None = None


class FixedAssetOut(_Out):
    id: int
    organization_id: int
    name: str
    inventory_number: str
    legal_entity_id: int
    cost: Decimal
    useful_life_months: int
    cfo_id: int | None
    pl_item_id: int | None
    commissioned_on: dt.date | None
    commission_operation_id: int | None
    disposed_on: dt.date | None = None
    disposal_kind: str | None = None
    disposal_operation_id: int | None = None
    project_id: int | None = None
    is_active: bool


# --- ЦФУ (исполнители): отдел → исполнитель ---


class ExecutorCreate(BaseModel):
    name: Name
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] = ""  # для загрузок из Excel
    parent_id: int | None = None  # отдел; не указан — это отдел
    employee_id: int | None = None


class ExecutorUpdate(BaseModel):
    name: Name | None = None
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    parent_id: int | None = None
    employee_id: int | None = None
    is_active: bool | None = None


class ExecutorOut(_Out):
    id: int
    organization_id: int
    name: str
    code: str = ""
    parent_id: int | None
    employee_id: int | None
    is_active: bool


# --- Инвестпроекты ---

ProjectStatus = Literal["planned", "active", "done"]
Limit = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Lag = Annotated[int, Field(ge=0, le=24)]


class ProjectCreate(BaseModel):
    name: Name
    cfo_id: int
    executor_id: int | None = None
    start_on: dt.date | None = None
    finish_on: dt.date | None = None
    limit_amount: Limit = Decimal("0")
    status: ProjectStatus = "planned"
    payment_lag_months: Lag = 0


class ProjectUpdate(BaseModel):
    name: Name | None = None
    cfo_id: int | None = None
    executor_id: int | None = None
    start_on: dt.date | None = None
    finish_on: dt.date | None = None
    limit_amount: Limit | None = None
    status: ProjectStatus | None = None
    payment_lag_months: Lag | None = None
    is_active: bool | None = None


class ProjectOut(_Out):
    id: int
    organization_id: int
    name: str
    cfo_id: int
    executor_id: int | None
    start_on: dt.date | None
    finish_on: dt.date | None
    limit_amount: Decimal
    status: ProjectStatus
    payment_lag_months: int
    is_active: bool

