import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Суммы — только Decimal (строка или число в JSON), до 2 знаков; количество — до 3 знаков
Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]


class AnalyticsIn(BaseModel):
    """Аналитика одной стороны проводки: вид аналитики → id записи справочника."""

    model_config = ConfigDict(extra="forbid")

    bank_account: int | None = None
    counterparty: int | None = None
    contract: int | None = None
    employee: int | None = None
    nomenclature: int | None = None
    warehouse: int | None = None
    fixed_asset: int | None = None
    cfo: int | None = None
    executor: int | None = None  # ЦФУ — необязательно, там, где ведётся ЦФО
    cash_flow_item: int | None = None
    pl_item: int | None = None


class PostingIn(BaseModel):
    debit_account_id: int | None = None
    credit_account_id: int | None = None
    amount: Money | None = None
    currency_id: int | None = None  # не указана — рубль
    amount_rub: Money | None = None  # указана — введена вручную, не пересчитывается по курсу
    quantity: Quantity | None = None
    debit: AnalyticsIn = AnalyticsIn()
    credit: AnalyticsIn = AnalyticsIn()


class OperationIn(BaseModel):
    legal_entity_id: int
    op_date: dt.date
    is_opening: bool = False  # ввод начальных остатков; задаётся при создании и не меняется
    article_id: int | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    postings: list[PostingIn] = Field(default_factory=list, max_length=100)


class PostingOut(BaseModel):
    id: int
    line_no: int
    debit_account_id: int | None
    credit_account_id: int | None
    amount: Decimal | None
    currency_id: int
    amount_rub: Decimal | None
    amount_rub_manual: bool
    quantity: Decimal | None
    debit: dict[str, int]
    credit: dict[str, int]


class OperationOut(BaseModel):
    id: int
    organization_id: int
    legal_entity_id: int
    number: int
    op_date: dt.date
    is_opening: bool
    article_id: int | None
    description: str
    status: Literal["draft", "posted", "cancelled"]
    total_rub: Decimal
    postings: list[PostingOut]
    created_by: int
    created_at: dt.datetime
    updated_by: int | None
    updated_at: dt.datetime | None
    posted_by: int | None
    posted_at: dt.datetime | None


class OperationList(BaseModel):
    items: list[OperationOut]
    total: int


class EventOut(BaseModel):
    action: str
    at: dt.datetime
    user_id: int
    user_name: str


BulkAction = Literal["post", "unpost", "cancel", "restore", "delete"]


class BulkIn(BaseModel):
    action: BulkAction
    ids: list[int] = Field(min_length=1, max_length=1000)


class BulkResult(BaseModel):
    id: int
    ok: bool
    error: str | None = None


class BulkOut(BaseModel):
    done: int
    failed: int
    results: list[BulkResult]
