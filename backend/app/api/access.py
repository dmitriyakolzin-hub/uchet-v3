"""Проверка прав пользователя в организации. Используется в каждом эндпоинте с данными организаций."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ADMIN_PERMS, ROLE_PERMS, Perm, Role
from app.models import Membership, Organization, User


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

    Если у пользователя нет даже просмотра — отвечаем «не найдена», чтобы не раскрывать,
    что такая организация существует.
    """
    organization = db.get(Organization, organization_id)
    perms = org_permissions(db, user, organization_id) if organization else frozenset()
    if organization is None or Perm.VIEW not in perms:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Организация не найдена")
    if perm not in perms:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return organization


def visible_organization_ids(db: Session, user: User) -> list[int] | None:
    """id организаций, доступных пользователю; None — доступны все (администратор)."""
    if user.is_admin:
        return None
    return list(db.scalars(select(Membership.organization_id).where(Membership.user_id == user.id)))
