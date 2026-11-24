"""Управление пользователями: администратор — все; финансовый директор (docs/TZ.md, раздел 30, «Детали 22.1») —
пользователи своих организаций и ещё без ролей, роли только в своих организациях; администраторов не трогает
и признак «администратор» не ставит."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, ManagerUser, director_org_ids
from app.core.permissions import ROLE_PERMS, ROLE_TITLES, Role
from app.core.security import hash_password
from app.models import Cfo, Executor, Membership, Organization, User
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
        select(Membership.organization_id, Organization.name, Membership.role, Membership.cfo_id, Cfo.name,
               Membership.executor_id, Executor.name)
        .join(Organization, Organization.id == Membership.organization_id)
        .outerjoin(Cfo, Cfo.id == Membership.cfo_id)
        .outerjoin(Executor, Executor.id == Membership.executor_id)
        .where(Membership.user_id == user_id)
        .order_by(Organization.name)
    )
    return [
        MembershipOut(
            organization_id=org_id,
            organization_name=name,
            role=Role(role),
            cfo_id=cfo_id,
            cfo_name=cfo_name,
            executor_id=executor_id,
            executor_name=executor_name,
            permissions=sorted(ROLE_PERMS[Role(role)]),
        )
        for org_id, name, role, cfo_id, cfo_name, executor_id, executor_name in rows
    ]


def user_out(db: Session, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.memberships = memberships_of(db, user.id)
    return out


def _visible(db: Session, manager: User, user: User) -> bool:
    """Финансовый директор видит пользователей своих организаций и ещё без ролей (не администраторов)."""
    if manager.is_admin:
        return True
    if user.is_admin:
        return False
    orgs = set(db.scalars(select(Membership.organization_id).where(Membership.user_id == user.id)))
    return not orgs or bool(orgs & director_org_ids(db, manager))


def _get_user(db: Session, user_id: int, manager: User | None = None) -> User:
    user = db.get(User, user_id)
    if user is None or (manager is not None and not _visible(db, manager, user)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return user


def _scoped(db: Session, manager: User, user_id: int | None, items: list[MembershipIn]) -> list[MembershipIn]:
    """Финансовый директор задаёт роли только в своих организациях; роли в чужих сохраняются как были."""
    if manager.is_admin:
        return items
    own = director_org_ids(db, manager)
    if any(m.organization_id not in own for m in items):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Роли можно давать только в организациях, где вы финансовый директор")
    if user_id is None:
        return items
    kept = [MembershipIn(organization_id=m.organization_id, role=Role(m.role), cfo_id=m.cfo_id,
                         executor_id=m.executor_id)
            for m in db.scalars(select(Membership).where(Membership.user_id == user_id,
                                                         Membership.organization_id.not_in(own)))]
    return kept + items


def _set_memberships(db: Session, user_id: int, items: list[MembershipIn]) -> None:
    org_ids = {m.organization_id for m in items}
    if org_ids:
        found = set(db.scalars(select(Organization.id).where(Organization.id.in_(org_ids))))
        if missing := org_ids - found:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Организации не найдены: {', '.join(map(str, sorted(missing)))}",
            )
    for m in items:
        if m.cfo_id is not None:
            cfo = db.get(Cfo, m.cfo_id)
            if cfo is None or cfo.organization_id != m.organization_id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    detail="ЦФО руководителя не найден в этой организации")
        if m.executor_id is not None:
            executor = db.get(Executor, m.executor_id)
            if executor is None or executor.organization_id != m.organization_id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    detail="ЦФУ руководителя не найден в этой организации")
    db.query(Membership).filter(Membership.user_id == user_id).delete()
    db.add_all(
        Membership(user_id=user_id, organization_id=m.organization_id, role=m.role.value, cfo_id=m.cfo_id,
                   executor_id=m.executor_id)
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
def list_users(manager: ManagerUser, db: DbSession) -> list[UserOut]:
    return [user_out(db, u) for u in db.scalars(select(User).order_by(User.login)) if _visible(db, manager, u)]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, manager: ManagerUser, db: DbSession) -> UserOut:
    if body.is_admin and not manager.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Администратора назначает администратор")
    memberships = _scoped(db, manager, None, body.memberships)
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
    _set_memberships(db, user.id, memberships)
    db.commit()
    return user_out(db, user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, manager: ManagerUser, db: DbSession) -> UserOut:
    return user_out(db, _get_user(db, user_id, manager))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: ManagerUser, db: DbSession) -> UserOut:
    user = _get_user(db, user_id, admin)
    if body.is_admin is not None and body.is_admin != user.is_admin and not admin.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Администратора назначает администратор")
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
    user_id: int, body: MembershipsUpdate, manager: ManagerUser, db: DbSession
) -> UserOut:
    user = _get_user(db, user_id, manager)
    _set_memberships(db, user.id, _scoped(db, manager, user.id, body.memberships))
    db.commit()
    return user_out(db, user)
