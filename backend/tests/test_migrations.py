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


def test_articles_split_between_organizations():
    """0030: каждая организация получает свою копию статей, её данные переводятся на копии."""
    cfg = alembic_config()
    get_engine().dispose()
    command.downgrade(cfg, "0029")
    try:
        with get_engine().begin() as conn:
            rent = conn.execute(text("SELECT id FROM articles WHERE name = 'Аренда'")).scalar()
            orgs = [conn.execute(text("INSERT INTO organizations (name, kind) VALUES (:n, 'group') RETURNING id"),
                                 {"n": n}).scalar() for n in ("А", "Б")]
            for org in orgs:
                entity = conn.execute(text(
                    "INSERT INTO legal_entities (organization_id, full_name, short_name, inn) "
                    "VALUES (:o, 'ООО', :s, :inn) RETURNING id"), {"o": org, "s": f"Ю{org}", "inn": f"770123456{org}"}).scalar()
                conn.execute(text("INSERT INTO employees (organization_id, full_name, pl_item_id) VALUES (:o, 'Иванов', :a)"),
                             {"o": org, "a": rent})
                conn.execute(text(
                    "INSERT INTO routine_rules (organization_id, name, legal_entity_id, day, valid_from, template) "
                    "VALUES (:o, 'Аренда', :e, 5, '2026-01-01', CAST(:t AS jsonb))"),
                    {"o": org, "e": entity, "t": f'{{"article_id": {rent}, "postings": [{{"debit": {{"pl_item": {rent}}}, '
                                                  f'"credit": {{}}}}]}}'})
        command.upgrade(cfg, "head")
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT e.organization_id, a.organization_id, a.name, r.template FROM employees e "
                "JOIN articles a ON a.id = e.pl_item_id JOIN routine_rules r ON r.organization_id = e.organization_id "
                "ORDER BY e.organization_id")).all()
            assert [(o, ao, n) for o, ao, n, _ in rows] == [(orgs[0], orgs[0], "Аренда"), (orgs[1], orgs[1], "Аренда")]
            second = conn.execute(text("SELECT id FROM articles WHERE organization_id = :o AND name = 'Аренда'"),
                                  {"o": orgs[1]}).scalar()
            assert rows[1][3]["article_id"] == second and rows[1][3]["postings"][0]["debit"]["pl_item"] == second
            assert rows[0][3]["article_id"] == rent
            counts = conn.execute(text("SELECT organization_id, count(*) FROM articles GROUP BY 1 ORDER BY 1")).all()
            assert [c for _, c in counts] == [46, 46]
            assert conn.execute(text("SELECT count(*) FROM articles WHERE system_key IS NOT NULL")).scalar() == 28
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("TRUNCATE organizations RESTART IDENTITY CASCADE"))
        command.upgrade(cfg, "head")
