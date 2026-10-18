from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, user_from_token
from app.core.security import create_token, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserOut

router = APIRouter(prefix="/api/auth", tags=["Вход"])


def _issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(user.id, user.token_version, "access"),
        refresh_token=create_token(user.id, user.token_version, "refresh"),
    )


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: DbSession) -> TokenPair:
    user = db.scalar(select(User).where(User.login == body.login.strip().lower()))
    password_ok = verify_password(body.password, user.password_hash if user else None)
    if user is None or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль"
        )
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: DbSession) -> TokenPair:
    user = user_from_token(db, body.refresh_token, "refresh")
    return _issue_tokens(user)


@router.post("/logout-everywhere", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(user: CurrentUser, db: DbSession) -> None:
    """Сделать недействительными все токены пользователя (на всех устройствах)."""
    user.token_version += 1
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
