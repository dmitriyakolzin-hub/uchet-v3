import datetime as dt
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

Code = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z]{3}$"),
]
NumericCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{3}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# Курс: положительный, до 12 знаков до запятой и 6 после. Строка или число — без float внутри.
Rate = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
Nominal = Annotated[int, Field(ge=1, le=1_000_000)]


class CurrencyCreate(BaseModel):
    code: Code
    numeric_code: NumericCode
    name: Name


class CurrencyUpdate(BaseModel):
    name: Name | None = None
    is_active: bool | None = None


class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    numeric_code: str
    name: str
    is_active: bool
    is_base: bool = False


class RateIn(BaseModel):
    rate_date: dt.date
    rate: Rate
    nominal: Nominal = 1


class RateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    currency_id: int
    rate_date: dt.date
    rate: Decimal
    nominal: int


class EffectiveRateOut(BaseModel):
    currency: str
    on_date: dt.date
    rate_date: dt.date | None
    rate: Decimal
    nominal: int
