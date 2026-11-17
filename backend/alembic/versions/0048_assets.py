"""Движение имущества: роли, единицы имущества, история, файлы, категории; сотрудник ↔ пользователь

Существующие карточки основных средств получают единицы имущества (docs/TZ.md, раздел 28, «Детали 19.1»).

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = "role IN ('finance_director', 'finance_manager', 'ceo', 'cfo_head', 'executor_head')"
NEW_ROLES = ("role IN ('finance_director', 'finance_manager', 'ceo', 'cfo_head', 'executor_head', "
             "'accountant', 'engineer', 'aho_operator', 'mol')")
CATEGORIES = ["Оборудование", "Мебель", "Компьютерная техника", "Инструмент", "Инвентарь", "Транспорт", "Прочее"]


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False)


def upgrade() -> None:
    op.create_table(
        "asset_categories",
        sa.Column("name", sa.String(length=255), nullable=False),
        _id(),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=op.f("fk_asset_categories_organization_id_organizations"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_categories")),
        sa.UniqueConstraint("organization_id", "name", name=op.f("uq_asset_categories_organization_id")),
    )
    op.create_index(op.f("ix_asset_categories_organization_id"), "asset_categories", ["organization_id"])
    op.create_table(
        "asset_items",
        _id(),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("ownership", sa.String(length=10), server_default="own", nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=True),
        sa.Column("serial_number", sa.String(length=100), server_default="", nullable=False),
        sa.Column("cfo_id", sa.BigInteger(), nullable=True),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("employee_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="stock", nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("upgraded_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("lease_remaining", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("acquired_on", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=2000), server_default="", nullable=False),
        sa.Column("fixed_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ownership IN ('own', 'leasing', 'rent')", name=op.f("ck_asset_items_ownership")),
        sa.CheckConstraint("status IN ('stock', 'in_use', 'repair', 'disposed')", name=op.f("ck_asset_items_status")),
        sa.CheckConstraint("purchase_price IS NULL OR purchase_price >= 0",
                           name=op.f("ck_asset_items_purchase_price")),
        sa.ForeignKeyConstraint(["category_id"], ["asset_categories.id"],
                                name=op.f("fk_asset_items_category_id_asset_categories"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_asset_items_cfo_id_cfos"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_asset_items_created_by_users"),
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], name=op.f("fk_asset_items_employee_id_employees"),
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fixed_asset_id"], ["fixed_assets.id"],
                                name=op.f("fk_asset_items_fixed_asset_id_fixed_assets"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"],
                                name=op.f("fk_asset_items_legal_entity_id_legal_entities"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=op.f("fk_asset_items_organization_id_organizations"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], name=op.f("fk_asset_items_warehouse_id_warehouses"),
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_items")),
        sa.UniqueConstraint("fixed_asset_id", name=op.f("uq_asset_items_fixed_asset_id")),
        sa.UniqueConstraint("organization_id", "number", name=op.f("uq_asset_items_organization_id")),
    )
    op.create_index(op.f("ix_asset_items_cfo_id"), "asset_items", ["cfo_id"])
    op.create_index(op.f("ix_asset_items_employee_id"), "asset_items", ["employee_id"])
    op.create_index(op.f("ix_asset_items_organization_id"), "asset_items", ["organization_id"])
    op.create_table(
        "asset_events",
        _id(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("details", sa.Text(), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["asset_items.id"], name=op.f("fk_asset_events_item_id_asset_items"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_asset_events_user_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_events")),
    )
    op.create_index(op.f("ix_asset_events_item_id"), "asset_events", ["item_id"])
    op.create_table(
        "asset_item_files",
        _id(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["asset_items.id"], name=op.f("fk_asset_item_files_item_id_asset_items"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], name=op.f("fk_asset_item_files_uploaded_by_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_item_files")),
    )
    op.create_index(op.f("ix_asset_item_files_item_id"), "asset_item_files", ["item_id"])
    op.add_column("employees", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.create_index("uq_employees_organization_user", "employees", ["organization_id", "user_id"], unique=True,
                    postgresql_where=sa.text("user_id IS NOT NULL"))
    op.create_foreign_key(op.f("fk_employees_user_id_users"), "employees", "users", ["user_id"], ["id"],
                          ondelete="SET NULL")
    op.drop_constraint(op.f("ck_memberships_role"), "memberships", type_="check")
    op.create_check_constraint(op.f("ck_memberships_role"), "memberships", NEW_ROLES)

    # категории — каждой организации
    for name in CATEGORIES:
        op.execute(sa.text("INSERT INTO asset_categories (organization_id, name, is_active) "
                           "SELECT id, :name, true FROM organizations").bindparams(name=name))
    # карточки основных средств → единицы имущества; свой инвентарный номер — если он не повторяется
    op.execute("""
        INSERT INTO asset_items (organization_id, number, name, category_id, ownership, legal_entity_id, cfo_id,
                                 status, purchase_price, acquired_on, fixed_asset_id)
        SELECT fa.organization_id,
               CASE WHEN fa.inventory_number <> '' AND count(*) OVER (
                         PARTITION BY fa.organization_id, fa.inventory_number) = 1
                    THEN fa.inventory_number
                    ELSE 'ИН-' || lpad((row_number() OVER (PARTITION BY fa.organization_id ORDER BY fa.id))::text,
                                      6, '0') END,
               fa.name,
               (SELECT c.id FROM asset_categories c WHERE c.organization_id = fa.organization_id
                  AND c.name = 'Оборудование'),
               'own', fa.legal_entity_id, fa.cfo_id,
               CASE WHEN fa.disposed_on IS NOT NULL THEN 'disposed'
                    WHEN fa.commissioned_on IS NOT NULL THEN 'in_use' ELSE 'stock' END,
               fa.cost, fa.commissioned_on, fa.id
        FROM fixed_assets fa
    """)
    op.execute("INSERT INTO asset_events (item_id, action, details) "
               "SELECT id, 'created', 'перенесено из карточки основного средства' FROM asset_items")


def downgrade() -> None:
    op.execute("DELETE FROM memberships WHERE role IN ('accountant', 'engineer', 'aho_operator', 'mol')")
    op.drop_constraint(op.f("ck_memberships_role"), "memberships", type_="check")
    op.create_check_constraint(op.f("ck_memberships_role"), "memberships", OLD_ROLES)
    op.drop_constraint(op.f("fk_employees_user_id_users"), "employees", type_="foreignkey")
    op.drop_index("uq_employees_organization_user", table_name="employees",
                  postgresql_where=sa.text("user_id IS NOT NULL"))
    op.drop_column("employees", "user_id")
    op.drop_table("asset_item_files")
    op.drop_table("asset_events")
    op.drop_table("asset_items")
    op.drop_table("asset_categories")
