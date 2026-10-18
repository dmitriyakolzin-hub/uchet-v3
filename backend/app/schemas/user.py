from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator

from app.core.permissions import Perm, Role
from app.core.security import MIN_PASSWORD_LENGTH

Login = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[a-z0-9._-]{2,100}$"),
]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)]


class MembershipIn(BaseModel):
    organization_id: int
    role: Role


class MembershipOut(BaseModel):
    organization_id: int
    organization_name: str
    role: Role
    permissions: list[Perm]


def _no_duplicate_organizations(items: list[MembershipIn]) -> list[MembershipIn]:
    ids = [m.organization_id for m in items]
    if len(ids) != len(set(ids)):
        raise ValueError("в одной организации у пользователя может быть только одна роль")
    return items


class UserCreate(BaseModel):
    login: Login
    full_name: FullName = ""
    password: Password
    is_admin: bool = False
    memberships: list[MembershipIn] = []

    _check_memberships = field_validator("memberships")(_no_duplicate_organizations)


class UserUpdate(BaseModel):
    full_name: FullName | None = None
    is_admin: bool | None = None
    is_active: bool | None = None
    password: Password | None = None


class MembershipsUpdate(BaseModel):
    memberships: list[MembershipIn]

    _check_memberships = field_validator("memberships")(_no_duplicate_organizations)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    full_name: str
    is_admin: bool
    is_active: bool
    memberships: list[MembershipOut] = []


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: Password


class RoleOut(BaseModel):
    code: Role
    title: str
    permissions: list[Perm]
