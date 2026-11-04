"""Цели вышестоящих ЦФО (планирование сверху вниз)

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("plan_targets",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("version_id", sa.BigInteger(), nullable=False),
    sa.Column("cfo_id", sa.BigInteger(), nullable=False),
    sa.Column("article_id", sa.BigInteger(), nullable=False),
    sa.Column("month", sa.SmallInteger(), nullable=False),
    sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column("created_by", sa.BigInteger(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.CheckConstraint("amount > 0", name=op.f("ck_plan_targets_amount")),
    sa.CheckConstraint("month BETWEEN 1 AND 12", name=op.f("ck_plan_targets_month")),
    sa.ForeignKeyConstraint(["article_id"], ["articles.id"], name=op.f("fk_plan_targets_article_id_articles"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_plan_targets_cfo_id_cfos"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_plan_targets_created_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["version_id"], ["budget_versions.id"], name=op.f("fk_plan_targets_version_id_budget_versions"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_targets")),
    sa.UniqueConstraint("version_id", "cfo_id", "article_id", "month", name="uq_plan_targets_cell")
    )
    op.create_index(op.f("ix_plan_targets_version_id"), "plan_targets", ["version_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_plan_targets_version_id"), table_name="plan_targets")
    op.drop_table("plan_targets")
