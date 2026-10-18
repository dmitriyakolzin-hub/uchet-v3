from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.core.analytics import AnalyticKind

AccountType = Literal["active", "passive", "active_passive"]
Code = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\d{2}(\.\d{2}){0,2}$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


def _unique_kinds(kinds: list[AnalyticKind] | None) -> list[AnalyticKind] | None:
    if kinds is not None and len(kinds) != len(set(kinds)):
        raise ValueError("вид аналитики указан дважды")
    return kinds


class AccountCreate(BaseModel):
    code: Code
    name: Name
    account_type: AccountType | None = None  # для субсчёта по умолчанию — как у родителя
    is_quantitative: bool | None = None
    is_currency: bool | None = None
    analytics: list[AnalyticKind] | None = None  # None — взять у родителя

    _check = field_validator("analytics")(_unique_kinds)


class AccountUpdate(BaseModel):
    name: Name | None = None
    account_type: AccountType | None = None
    is_quantitative: bool | None = None
    is_currency: bool | None = None
    is_active: bool | None = None
    analytics: list[AnalyticKind] | None = None

    _check = field_validator("analytics")(_unique_kinds)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    parent_id: int | None
    account_type: AccountType
    is_quantitative: bool
    is_currency: bool
    is_active: bool
    analytics: list[AnalyticKind]
    has_children: bool = False


class AnalyticKindOut(BaseModel):
    code: AnalyticKind
    title: str
