"""Управление пользователями — только администратор."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.permissions import ROLE_PERMS, ROLE_TITLES, Role
from app.core.security import hash_password
from app.models import Membership, Organization, User
from app.schemas.user import (
    MembershipIn,
    MembershipOut,
    MembershipsUpdate,
    RoleOut,
    UserCreate,
    UserOut,
    UserUpdate,
)

router = APIRouter(prefix="/api", tags=["Пользователи"])


def memberships_of(db: Session, user_id: int) -> list[MembershipOut]:
    rows = db.execute(
        select(Membership.organization_id, Organization.name, Membership.role)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user_id)
        .order_by(Organization.name)
    )
    return [
        MembershipOut(
            organization_id=org_id,
            organization_name=name,
            role=Role(role),
            permissions=sorted(ROLE_PERMS[Role(role)]),
        )
        for org_id, name, role in rows
    ]


def user_out(db: Session, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.memberships = memberships_of(db, user.id)
    return out


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return user


def _set_memberships(db: Session, user_id: int, items: list[MembershipIn]) -> None:
    org_ids = {m.organization_id for m in items}
    if org_ids:
        found = set(db.scalars(select(Organization.id).where(Organization.id.in_(org_ids))))
        if missing := org_ids - found:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Организации не найдены: {', '.join(map(str, sorted(missing)))}",
            )
    db.query(Membership).filter(Membership.user_id == user_id).delete()
    db.add_all(
        Membership(user_id=user_id, organization_id=m.organization_id, role=m.role.value)
        for m in items
    )


def _other_active_admins(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_admin, User.is_active, User.id != user_id)
    )


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: CurrentUser) -> list[RoleOut]:
    return [
        RoleOut(code=role, title=ROLE_TITLES[role], permissions=sorted(ROLE_PERMS[role]))
        for role in Role
    ]


@router.get("/users", response_model=list[UserOut])
def list_users(_: AdminUser, db: DbSession) -> list[UserOut]:
    return [user_out(db, u) for u in db.scalars(select(User).order_by(User.login))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, _: AdminUser, db: DbSession) -> UserOut:
    user = User(
        login=body.login,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Пользователь с таким логином уже есть"
        ) from None
    _set_memberships(db, user.id, body.memberships)
    db.commit()
    return user_out(db, user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, _: AdminUser, db: DbSession) -> UserOut:
    return user_out(db, _get_user(db, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: AdminUser, db: DbSession) -> UserOut:
    user = _get_user(db, user_id)
    losing_admin = (body.is_admin is False or body.is_active is False) and user.is_admin
    if losing_admin and user.id == admin.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Нельзя отключить себя или снять с себя права администратора",
        )
    if losing_admin and user.is_active and _other_active_admins(db, user.id) == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Нельзя оставить систему без активного администратора"
        )

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None:
        if user.is_active and not body.is_active:
            user.token_version += 1  # отключённый пользователь выходит отовсюду
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        user.token_version += 1
    db.commit()
    return user_out(db, user)


@router.put("/users/{user_id}/memberships", response_model=UserOut)
def update_memberships(
    user_id: int, body: MembershipsUpdate, _: AdminUser, db: DbSession
) -> UserOut:
    user = _get_user(db, user_id)
    _set_memberships(db, user.id, body.memberships)
    db.commit()
    return user_out(db, user)
