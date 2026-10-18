"""Настройки приложения. Читаются только из переменных окружения (файл .env на сервере).

Секретов по умолчанию нет: если JWT_SECRET не задан или слабый, приложение не запускается.
"""

from functools import lru_cache

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Значения-заглушки, которые часто оставляют по ошибке. С ними запуск запрещён.
_WEAK_SECRETS = {
    "change-me",
    "changeme",
    "secret",
    "your-secret-key",
    "your-secret-key-change-in-production",
}

JWT_SECRET_MIN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=True)

    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = Field(min_length=1)

    JWT_SECRET: str
    ACCESS_TOKEN_MINUTES: int = Field(default=15, ge=1, le=24 * 60)
    REFRESH_TOKEN_DAYS: int = Field(default=7, ge=1, le=90)

    @field_validator("JWT_SECRET")
    @classmethod
    def _check_jwt_secret(cls, value: str) -> str:
        value = value.strip()
        if value.lower() in _WEAK_SECRETS:
            raise ValueError("JWT_SECRET — значение-заглушка, задайте случайный секрет")
        if len(value) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(f"JWT_SECRET короче {JWT_SECRET_MIN_LENGTH} символов")
        return value

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )


class SettingsError(RuntimeError):
    pass


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            name = ".".join(str(part) for part in err["loc"])
            reason = "не задано" if err["type"] == "missing" else err["msg"]
            problems.append(f"  - {name}: {reason}")
        raise SettingsError(
            "Приложение не запущено: ошибка в настройках (.env).\n" + "\n".join(problems)
        ) from None
