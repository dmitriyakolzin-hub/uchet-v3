"""Проверка прав пользователя в организации. Используется в каждом эндпоинте с данными организаций."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ADMIN_PERMS, ROLE_PERMS, Perm, Role
from app.models import Cfo, Executor, Membership, Organization, User


def org_permissions(db: Session, user: User, organization_id: int) -> frozenset[Perm]:
    if user.is_admin:
        return ADMIN_PERMS
    role = db.scalar(
        select(Membership.role).where(
            Membership.user_id == user.id, Membership.organization_id == organization_id
        )
    )
    return ROLE_PERMS[Role(role)] if role else frozenset()


def get_organization_for(db: Session, user: User, organization_id: int, perm: Perm) -> Organization:
    """Организация, если у пользователя есть право perm.

    Если у пользователя нет в организации никакой роли — отвечаем «не найдена», чтобы не раскрывать,
    что такая организация существует. Руководитель ЦФУ (только заявки) на остальное получает 403.
    """
    organization = db.get(Organization, organization_id)
    perms = org_permissions(db, user, organization_id) if organization else frozenset()
    if organization is None or not perms:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Организация не найдена")
    if perm not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return organization


def visible_organization_ids(db: Session, user: User) -> list[int] | None:
    """id организаций, доступных пользователю; None — доступны все (администратор)."""
    if user.is_admin:
        return None
    return list(db.scalars(select(Membership.organization_id).where(Membership.user_id == user.id)))


def get_organization_any(db: Session, user: User, organization_id: int, *perms: Perm) -> Organization:
    """Как get_organization_for, но достаточно любого из прав (например, VIEW или REQUEST)."""
    have = org_permissions(db, user, organization_id)
    perm = next((p for p in perms if p in have), perms[0])  # нет ни одного — 404 или 403 по первому
    return get_organization_for(db, user, organization_id, perm)


def executor_scope(db: Session, user: User, organization_id: int) -> set[int] | None:
    """ЦФУ руководителя ЦФУ (отдел — вместе с исполнителями); None — пользователь не руководитель ЦФУ."""
    if user.is_admin:
        return None
    membership = db.scalar(select(Membership).where(
        Membership.user_id == user.id, Membership.organization_id == organization_id))
    if membership is None or membership.role != Role.EXECUTOR_HEAD:
        return None
    if membership.executor_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Руководителю ЦФУ не назначен ЦФУ — обратитесь к администратору")
    children = db.scalars(select(Executor.id).where(Executor.parent_id == membership.executor_id))
    return {membership.executor_id, *children}


def role_in(db: Session, user: User, organization_id: int) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == organization_id))


# --- Ветка ЦФО руководителя ЦФО (docs/TZ.md, раздел 13) ---


def cfo_scope(db: Session, user: User, organization_id: int) -> int | None:
    """Корень ветки ЦФО, которой ограничен пользователь в организации; None — ограничения нет.
    Руководителю ЦФО без назначенного ЦФО данные недоступны."""
    if user.is_admin:
        return None
    membership = db.scalar(select(Membership).where(
        Membership.user_id == user.id, Membership.organization_id == organization_id))
    if membership is None or membership.role != Role.CFO_HEAD:
        return None
    if membership.cfo_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Руководителю ЦФО не назначен ЦФО — обратитесь к администратору")
    return membership.cfo_id


def branch_ids(db: Session, root_id: int) -> set[int]:
    """ЦФО вместе со всеми подчинёнными."""
    root = select(Cfo.path).where(Cfo.id == root_id).scalar_subquery()
    return set(db.scalars(select(Cfo.id).where(Cfo.path.op("<@")(root))))


def forbid_scoped(db: Session, user: User, organization_id: int) -> None:
    """Раздел только для пользователей без ограничения веткой ЦФО (расчётные счета, остатки, выписки)."""
    if cfo_scope(db, user, organization_id) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Раздел недоступен руководителю ЦФО")
