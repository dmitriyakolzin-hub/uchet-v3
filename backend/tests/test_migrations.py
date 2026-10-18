from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.base import Base
from app.db.session import get_engine
from tests.conftest import alembic_config


def test_models_match_migrations():
    """Модели и миграции не разошлись: любое изменение схемы оформлено миграцией."""
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_ltree_available():
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT 'a.b.c'::ltree <@ 'a'::ltree")).scalar() is True


def test_downgrade_and_upgrade_again():
    cfg = alembic_config()
    get_engine().dispose()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with get_engine().connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == ScriptDirectory.from_config(cfg).get_current_head()


def test_postgres_version_17():
    with get_engine().connect() as conn:
        assert conn.execute(text("SHOW server_version_num")).scalar().startswith("17")
