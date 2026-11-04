"""История правок плана после утверждения

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("plan_changes",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("version_id", sa.BigInteger(), nullable=False),
    sa.Column("kind", sa.String(length=3), nullable=False),
    sa.Column("cfo_id", sa.BigInteger(), nullable=True),
    sa.Column("executor_id", sa.BigInteger(), nullable=True),
    sa.Column("article_id", sa.BigInteger(), nullable=False),
    sa.Column("month", sa.SmallInteger(), nullable=False),
    sa.Column("old_amount", sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column("new_amount", sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column("user_id", sa.BigInteger(), nullable=False),
    sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["article_id"], ["articles.id"], name=op.f("fk_plan_changes_article_id_articles"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_plan_changes_cfo_id_cfos"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["executor_id"], ["executors.id"], name=op.f("fk_plan_changes_executor_id_executors"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_plan_changes_user_id_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["version_id"], ["budget_versions.id"], name=op.f("fk_plan_changes_version_id_budget_versions"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_changes"))
    )
    op.create_index(op.f("ix_plan_changes_version_id"), "plan_changes", ["version_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_plan_changes_version_id"), table_name="plan_changes")
    op.drop_table("plan_changes")
