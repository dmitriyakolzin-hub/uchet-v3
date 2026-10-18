"""Пароли (argon2) и токены входа (JWT).

Токен принимается, только если:
  - подписан нашим JWT_SECRET алгоритмом HS256 (другие алгоритмы, в т.ч. "none", отклоняются);
  - не просрочен и содержит все обязательные поля;
  - имеет нужный тип (access для запросов, refresh только для обновления);
  - номер версии совпадает с token_version пользователя (смена пароля или отключение
    пользователя делают все его старые токены недействительными).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

_password_hash = PasswordHash.recommended()
# Хеш для сравнения, когда пользователь не найден: время ответа не выдаёт, существует ли логин.
_DUMMY_HASH = _password_hash.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False
    return _password_hash.verify(password, password_hash)


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    token_type: TokenType
    version: int


def create_token(user_id: int, token_version: int, token_type: TokenType) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    if token_type == "access":
        lifetime = timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)
    else:
        lifetime = timedelta(days=settings.REFRESH_TOKEN_DAYS)
    claims = {
        "sub": str(user_id),
        "type": token_type,
        "ver": token_version,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_type: TokenType) -> TokenPayload:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "type", "ver", "iat", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from None

    if claims["type"] != expected_type:
        raise InvalidToken("неверный тип токена")
    try:
        user_id = int(claims["sub"])
        version = int(claims["ver"])
    except (TypeError, ValueError):
        raise InvalidToken("неверное содержимое токена") from None
    return TokenPayload(user_id=user_id, token_type=expected_type, version=version)
