"""Общие зависимости эндпоинтов: сессия БД и проверка токена.

Каждый защищённый эндпоинт обязан принимать current_user (или admin_user) — так права
проверяются на сервере, а не только в интерфейсе.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import InvalidToken, TokenType, decode_token
from app.db.session import get_db
from app.models import User

DbSession = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется вход в систему",
        headers={"WWW-Authenticate": "Bearer"},
    )


def user_from_token(db: Session, token: str, expected_type: TokenType) -> User:
    try:
        payload = decode_token(token, expected_type)
    except InvalidToken:
        raise _unauthorized() from None
    user = db.get(User, payload.user_id)
    if user is None or not user.is_active or user.token_version != payload.version:
        raise _unauthorized()
    return user


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    return user_from_token(db, credentials.credentials, "access")


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_admin_user(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


def director_org_ids(db: Session, user: User) -> set[int]:
    """Организации, где пользователь — финансовый директор (docs/TZ.md, раздел 30, «Детали 22.1»)."""
    from sqlalchemy import select

    from app.models import Membership

    return set(db.scalars(select(Membership.organization_id).where(Membership.user_id == user.id,
                                                                   Membership.role == "finance_director")))


def get_manager_user(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> User:
    """Администратор или финансовый директор хотя бы одной организации: пользователи (в своих организациях),
    общие справочники (план счетов, валюты, банки, единицы), новые организации."""
    if user.is_admin or director_org_ids(db, user):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")


ManagerUser = Annotated[User, Depends(get_manager_user)]
