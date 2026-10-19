"""Роли и права (docs/TZ.md, раздел 3).

Администратор — признак пользователя, действует во всех организациях.
Остальные роли выдаются на конкретную организацию.
"""

from enum import StrEnum


class Role(StrEnum):
    FINANCE_DIRECTOR = "finance_director"
    CEO = "ceo"
    CFO_HEAD = "cfo_head"


class Perm(StrEnum):
    VIEW = "view"
    EDIT_REFERENCE = "edit_reference"
    DRAFT = "draft"
    POST = "post"
    MANAGE_USERS = "manage_users"


ROLE_TITLES: dict[Role, str] = {
    Role.FINANCE_DIRECTOR: "Финансовый директор / владелец",
    Role.CEO: "CEO",
    Role.CFO_HEAD: "Руководитель ЦФО",
}

ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.FINANCE_DIRECTOR: frozenset({Perm.VIEW, Perm.EDIT_REFERENCE, Perm.DRAFT, Perm.POST}),
    Role.CEO: frozenset({Perm.VIEW}),
    # Руководитель ЦФО ограничен своей веткой ЦФО (memberships.cfo_id, app/api/access.py: cfo_scope)
    Role.CFO_HEAD: frozenset({Perm.VIEW, Perm.DRAFT}),
}

ADMIN_PERMS: frozenset[Perm] = frozenset(Perm)
