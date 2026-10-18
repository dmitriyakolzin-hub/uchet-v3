from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.permissions import Perm

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Inn = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(\d{10}|\d{12})$")]
Kpp = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{9}$")]
Ogrn = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(\d{13}|\d{15})$")]
Kind = Literal["group", "client"]


class OrganizationCreate(BaseModel):
    name: Name
    kind: Kind
    note: str = Field(default="", max_length=2000)


class OrganizationUpdate(BaseModel):
    name: Name | None = None
    kind: Kind | None = None
    note: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: Kind
    note: str
    is_active: bool
    permissions: list[Perm] = []


class LegalEntityCreate(BaseModel):
    full_name: LongName
    short_name: Name
    inn: Inn
    kpp: Kpp | None = None
    ogrn: Ogrn | None = None


class LegalEntityUpdate(BaseModel):
    full_name: LongName | None = None
    short_name: Name | None = None
    inn: Inn | None = None
    kpp: Kpp | None = None
    ogrn: Ogrn | None = None
    is_active: bool | None = None


class LegalEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    full_name: str
    short_name: str
    inn: str
    kpp: str | None
    ogrn: str | None
    is_active: bool
