"""Тесты идут на настоящем PostgreSQL 17.

Адрес сервера берётся из POSTGRES_HOST/POSTGRES_PORT/POSTGRES_USER/POSTGRES_PASSWORD
(по умолчанию — тестовый контейнер из scripts/run_tests.sh на 127.0.0.1:55432).
Для тестов создаётся отдельная база uchet3_test, она пересоздаётся при каждом запуске.
"""

import os

os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "55432")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ["POSTGRES_DB"] = "uchet3_test"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.models import User  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "correct-horse-battery"


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = get_settings().database_url
    return cfg


def _recreate_test_database() -> None:
    url = get_settings().database_url
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin_engine.dispose()


SEED_ACCOUNT_IDS: set[int] = set()


@pytest.fixture(scope="session", autouse=True)
def database():
    _recreate_test_database()
    command.upgrade(alembic_config(), "head")
    with get_engine().connect() as conn:
        SEED_ACCOUNT_IDS.update(conn.execute(text("SELECT id FROM accounts")).scalars())
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE users, organizations, exchange_rates RESTART IDENTITY CASCADE"))
        conn.execute(text("DELETE FROM currencies WHERE code <> 'RUB'"))  # рубль заводит миграция
        # план счетов заводит миграция: удаляем только счета, созданные тестами (сначала субсчета)
        conn.execute(
            text("DELETE FROM accounts WHERE NOT (id = ANY(:ids)) AND parent_id IS NOT NULL"),
            {"ids": list(SEED_ACCOUNT_IDS)},
        )
        conn.execute(text("DELETE FROM accounts WHERE NOT (id = ANY(:ids))"), {"ids": list(SEED_ACCOUNT_IDS)})


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(db):
    def _make(login="ivanov", password=TEST_PASSWORD, **fields) -> User:
        user = User(login=login, password_hash=hash_password(password), **fields)
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def headers_for():
    """Заголовок Authorization для пользователя (токен выдаётся напрямую, без /login)."""
    from app.core.security import create_token

    def _headers(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_token(user.id, user.token_version, 'access')}"}

    return _headers


@pytest.fixture
def make_org(db):
    from app.models import Membership, Organization

    def _make(name="Группа А", kind="group", members=()) -> Organization:
        org = Organization(name=name, kind=kind)
        db.add(org)
        db.flush()
        for user, role in members:
            db.add(Membership(user_id=user.id, organization_id=org.id, role=role))
        db.commit()
        return org

    return _make
