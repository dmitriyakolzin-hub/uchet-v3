"""Плановые операции бюджета в журнале: статус plan, версия бюджета, вид плана и ЦФО ячейки

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("budget_version_id", sa.BigInteger(), nullable=True))
    op.add_column("operations", sa.Column("plan_kind", sa.String(length=2), nullable=True))
    op.add_column("operations", sa.Column("plan_cfo_id", sa.BigInteger(), nullable=True))
    # номер журнала — только у фактических операций
    op.drop_constraint(op.f("uq_operations_organization_number"), "operations", type_="unique")
    op.create_index("uq_operations_organization_number", "operations", ["organization_id", "number"], unique=True,
                    postgresql_where=sa.text("budget_version_id IS NULL"))
    op.create_index(op.f("ix_operations_budget_version_id"), "operations", ["budget_version_id"], unique=False)
    op.create_index("uq_operations_plan_cell", "operations",
                    ["budget_version_id", "plan_kind", "plan_cfo_id", "article_id", "op_date"], unique=True,
                    postgresql_nulls_not_distinct=True, postgresql_where=sa.text("budget_version_id IS NOT NULL"))
    op.create_foreign_key(op.f("fk_operations_plan_cfo_id_cfos"), "operations", "cfos", ["plan_cfo_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_operations_budget_version_id_budget_versions"), "operations", "budget_versions",
                          ["budget_version_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint(op.f("ck_operations_status"), "operations", type_="check")
    op.create_check_constraint(op.f("ck_operations_status"), "operations",
                               "status IN ('draft', 'posted', 'cancelled', 'plan')")
    op.create_check_constraint(op.f("ck_operations_plan_has_version"), "operations",
                               "(status = 'plan') = (budget_version_id IS NOT NULL)")
    op.create_check_constraint(op.f("ck_operations_plan_kind"), "operations",
                               "plan_kind IS NULL OR plan_kind IN ('pl', 'cf')")


def downgrade() -> None:
    op.execute("DELETE FROM operations WHERE budget_version_id IS NOT NULL")
    op.drop_constraint(op.f("ck_operations_plan_kind"), "operations", type_="check")
    op.drop_constraint(op.f("ck_operations_plan_has_version"), "operations", type_="check")
    op.drop_constraint(op.f("ck_operations_status"), "operations", type_="check")
    op.create_check_constraint(op.f("ck_operations_status"), "operations", "status IN ('draft', 'posted', 'cancelled')")
    op.drop_constraint(op.f("fk_operations_budget_version_id_budget_versions"), "operations", type_="foreignkey")
    op.drop_constraint(op.f("fk_operations_plan_cfo_id_cfos"), "operations", type_="foreignkey")
    op.drop_index("uq_operations_plan_cell", table_name="operations")
    op.drop_index(op.f("ix_operations_budget_version_id"), table_name="operations")
    op.drop_index("uq_operations_organization_number", table_name="operations")
    op.create_unique_constraint(op.f("uq_operations_organization_number"), "operations", ["organization_id", "number"])
    op.drop_column("operations", "plan_cfo_id")
    op.drop_column("operations", "plan_kind")
    op.drop_column("operations", "budget_version_id")
