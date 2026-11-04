"""Роли и права (docs/TZ.md, раздел 3).

Администратор — признак пользователя, действует во всех организациях.
Остальные роли выдаются на конкретную организацию.
"""

from enum import StrEnum


class Role(StrEnum):
    FINANCE_DIRECTOR = "finance_director"
    FINANCE_MANAGER = "finance_manager"
    CEO = "ceo"
    CFO_HEAD = "cfo_head"
    EXECUTOR_HEAD = "executor_head"


class Perm(StrEnum):
    VIEW = "view"
    EDIT_REFERENCE = "edit_reference"
    DRAFT = "draft"
    POST = "post"
    # бюджет, утверждение плана ЦФО, правка чужих примечаний — финансовый директор и администратор
    SUPERVISE = "supervise"
    # заявки на оплату (свои — подавать, видеть, согласовывать на своём шаге)
    REQUEST = "request"
    MANAGE_USERS = "manage_users"


ROLE_TITLES: dict[Role, str] = {
    Role.FINANCE_DIRECTOR: "Финансовый директор / владелец",
    Role.FINANCE_MANAGER: "Финансовый менеджер",
    Role.CEO: "CEO",
    Role.CFO_HEAD: "Руководитель ЦФО",
    Role.EXECUTOR_HEAD: "Руководитель ЦФУ",
}

ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.FINANCE_DIRECTOR: frozenset({Perm.VIEW, Perm.EDIT_REFERENCE, Perm.DRAFT, Perm.POST, Perm.SUPERVISE,
                                      Perm.REQUEST}),
    # деньги, заявки, календарь, реестр; без справочников, бюджета и пользователей (docs/TZ.md, раздел 24)
    Role.FINANCE_MANAGER: frozenset({Perm.VIEW, Perm.DRAFT, Perm.POST, Perm.REQUEST}),
    Role.CEO: frozenset({Perm.VIEW, Perm.REQUEST}),
    # Руководитель ЦФО ограничен своей веткой ЦФО (memberships.cfo_id, app/api/access.py: cfo_scope)
    Role.CFO_HEAD: frozenset({Perm.VIEW, Perm.DRAFT, Perm.REQUEST}),
    # Руководитель ЦФУ — только заявки своего ЦФУ (memberships.executor_id, access.executor_scope)
    Role.EXECUTOR_HEAD: frozenset({Perm.REQUEST}),
}

ADMIN_PERMS: frozenset[Perm] = frozenset(Perm)
