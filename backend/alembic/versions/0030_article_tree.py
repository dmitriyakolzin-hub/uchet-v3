"""Статьи у каждой организации свои и деревом: организация, родитель, код, порядок, системный ключ

Каждая организация получает свою копию нынешних статей (первая — сами статьи), её данные переводятся на копии.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-28
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# системные статьи стартового набора (app/core/standard_articles.py)
SYSTEM_KEYS = {
    "Поступление от покупателей": "default_in",
    "Оплата поставщикам": "default_out",
    "Выплата заработной платы": "salary_payment",
    "Уплата прочих налогов": "tax_other",
    "Выдача под отчёт": "advance_report",
    "Банковские комиссии": "commission",
    "Покупка основных средств": "asset_purchase",
    "Получение кредитов и займов": "loan_in",
    "Погашение кредитов и займов": "loan_out",
    "Страховые взносы с оплаты труда": "insurance",
    "Перевод между своими счетами": "transfer",
    "Амортизация основных средств": "depreciation",
    "Доход от продажи основных средств": "asset_sale",
    "Списание основных средств": "asset_writeoff",
}
COPY_COLUMNS = ("name", "system_key", "sort_order", "is_cash_flow", "cash_flow_activity", "cash_flow_direction",
                "is_pl", "pl_section", "debit_account_id", "credit_account_id", "is_transfer", "is_active")
# (таблица, колонка, как найти организацию строки)
REFERENCES = [
    ("operations", "article_id", "organization_id = :org"),
    ("employees", "pl_item_id", "organization_id = :org"),
    ("fixed_assets", "pl_item_id", "organization_id = :org"),
    ("payment_requests", "article_id", "organization_id = :org"),
] + [
    ("postings", f"{side}_{column}", "operation_id IN (SELECT id FROM operations WHERE organization_id = :org)")
    for side in ("dt", "ct") for column in ("cash_flow_article_id", "pl_article_id")
]


def _remap_templates(conn, org_id: int, mapping: dict[int, int]) -> None:
    """Образцы регламентных операций (JSONB): статья операции и статьи в аналитике проводок."""
    rows = conn.execute(sa.text("SELECT id, template FROM routine_rules WHERE organization_id = :org"),
                        {"org": org_id}).all()
    for rule_id, template in rows:
        template = dict(template)
        if template.get("article_id") in mapping:
            template["article_id"] = mapping[template["article_id"]]
        for posting in template.get("postings", []):
            for side in ("debit", "credit"):
                for kind in ("cash_flow_item", "pl_item"):
                    if posting.get(side, {}).get(kind) in mapping:
                        posting[side][kind] = mapping[posting[side][kind]]
        conn.execute(sa.text("UPDATE routine_rules SET template = CAST(:t AS jsonb) WHERE id = :id"),
                     {"t": json.dumps(template, ensure_ascii=False), "id": rule_id})


def upgrade() -> None:
    op.add_column("articles", sa.Column("organization_id", sa.BigInteger(), nullable=True))
    op.add_column("articles", sa.Column("parent_id", sa.BigInteger(), nullable=True))
    op.add_column("articles", sa.Column("code", sa.String(length=30), nullable=True))
    op.add_column("articles", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False))
    op.add_column("articles", sa.Column("system_key", sa.String(length=30), nullable=True))
    op.drop_constraint("uq_articles_name", "articles", type_="unique")

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE articles SET sort_order = id"))
    for name, key in SYSTEM_KEYS.items():
        conn.execute(sa.text("UPDATE articles SET system_key = :key WHERE name = :name"), {"key": key, "name": name})

    orgs = list(conn.execute(sa.text("SELECT id FROM organizations ORDER BY id")).scalars())
    if not orgs:
        conn.execute(sa.text("DELETE FROM articles"))  # статьи заведёт каждая новая организация
    else:
        conn.execute(sa.text("UPDATE articles SET organization_id = :org"), {"org": orgs[0]})
        originals = conn.execute(sa.text(f"SELECT id, {', '.join(COPY_COLUMNS)} FROM articles WHERE organization_id = :org"
                                         " ORDER BY id"), {"org": orgs[0]}).mappings().all()
        for org_id in orgs[1:]:
            mapping = {}
            for row in originals:
                values = {c: row[c] for c in COPY_COLUMNS}
                mapping[row["id"]] = conn.execute(sa.text(
                    f"INSERT INTO articles (organization_id, {', '.join(COPY_COLUMNS)}) "
                    f"VALUES (:org, {', '.join(':' + c for c in COPY_COLUMNS)}) RETURNING id"),
                    {"org": org_id, **values}).scalar()
            for old, new in mapping.items():
                for table, column, where in REFERENCES:
                    conn.execute(sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old AND {where}"),
                                 {"new": new, "old": old, "org": org_id})
            _remap_templates(conn, org_id, mapping)

    op.alter_column("articles", "organization_id", nullable=False)
    op.create_foreign_key(op.f("fk_articles_organization_id_organizations"), "articles", "organizations",
                          ["organization_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(op.f("fk_articles_parent_id_articles"), "articles", "articles",
                          ["parent_id"], ["id"], ondelete="RESTRICT")
    op.create_index(op.f("ix_articles_organization_id"), "articles", ["organization_id"], unique=False)
    op.create_index(op.f("ix_articles_parent_id"), "articles", ["parent_id"], unique=False)
    op.create_index("uq_articles_sibling_name", "articles", ["organization_id", "parent_id", "name"], unique=True,
                    postgresql_nulls_not_distinct=True)
    op.create_index("uq_articles_organization_code", "articles", ["organization_id", "code"], unique=True,
                    postgresql_where=sa.text("code IS NOT NULL"))
    op.create_index("uq_articles_organization_system_key", "articles", ["organization_id", "system_key"],
                    unique=True, postgresql_where=sa.text("system_key IS NOT NULL"))


def downgrade() -> None:
    """Обратно к общему справочнику: статьи с одинаковым названием сливаются в одну (самую раннюю)."""
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE articles SET parent_id = NULL"))
    rows = conn.execute(sa.text("SELECT id, name FROM articles ORDER BY id")).all()
    keep: dict[str, int] = {}
    for article_id, name in rows:
        if name not in keep:
            keep[name] = article_id
            continue
        target = keep[name]
        for table, column, _ in REFERENCES:
            conn.execute(sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
                         {"new": target, "old": article_id})
        for org_id in conn.execute(sa.text("SELECT id FROM organizations")).scalars():
            _remap_templates(conn, org_id, {article_id: target})
        conn.execute(sa.text("DELETE FROM articles WHERE id = :id"), {"id": article_id})

    op.drop_index("uq_articles_organization_system_key", table_name="articles")
    op.drop_index("uq_articles_organization_code", table_name="articles")
    op.drop_index("uq_articles_sibling_name", table_name="articles")
    op.drop_index(op.f("ix_articles_parent_id"), table_name="articles")
    op.drop_index(op.f("ix_articles_organization_id"), table_name="articles")
    op.drop_constraint(op.f("fk_articles_parent_id_articles"), "articles", type_="foreignkey")
    op.drop_constraint(op.f("fk_articles_organization_id_organizations"), "articles", type_="foreignkey")
    op.create_unique_constraint("uq_articles_name", "articles", ["name"])
    op.drop_column("articles", "system_key")
    op.drop_column("articles", "sort_order")
    op.drop_column("articles", "code")
    op.drop_column("articles", "parent_id")
    op.drop_column("articles", "organization_id")
    if not conn.execute(sa.text("SELECT 1 FROM articles LIMIT 1")).scalar():
        _seed_common(conn)  # до 0030 стартовый набор был общим


def _seed_common(conn) -> None:
    from app.core.standard_articles import STANDARD_ARTICLES

    accounts = dict(conn.execute(sa.text("SELECT code, id FROM accounts")).all())
    for name, flow, section, debit, credit, key in STANDARD_ARTICLES:
        conn.execute(sa.text(
            "INSERT INTO articles (name, is_cash_flow, cash_flow_activity, cash_flow_direction, is_pl, pl_section,"
            " debit_account_id, credit_account_id, is_transfer) VALUES (:name, :cf, :activity, :direction, :pl,"
            " :section, :debit, :credit, :transfer)"), {
            "name": name, "cf": flow is not None, "activity": flow[0] if flow else None,
            "direction": flow[1] if flow else None, "pl": section is not None, "section": section,
            "debit": accounts.get(debit), "credit": accounts.get(credit), "transfer": key == "transfer"})
