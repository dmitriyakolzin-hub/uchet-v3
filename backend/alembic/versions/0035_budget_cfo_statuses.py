"""Состояние плана ЦФО в версии бюджета: «план сдан» и «утверждён»

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("budget_cfo_statuses",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("version_id", sa.BigInteger(), nullable=False),
    sa.Column("cfo_id", sa.BigInteger(), nullable=False),
    sa.Column("submitted_by", sa.BigInteger(), nullable=True),
    sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("approved_by", sa.BigInteger(), nullable=True),
    sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(["approved_by"], ["users.id"], name=op.f("fk_budget_cfo_statuses_approved_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_budget_cfo_statuses_cfo_id_cfos"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], name=op.f("fk_budget_cfo_statuses_submitted_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["version_id"], ["budget_versions.id"], name=op.f("fk_budget_cfo_statuses_version_id_budget_versions"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_cfo_statuses")),
    sa.UniqueConstraint("version_id", "cfo_id", name="uq_budget_cfo_statuses_version_cfo")
    )
    op.create_index(op.f("ix_budget_cfo_statuses_version_id"), "budget_cfo_statuses", ["version_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_budget_cfo_statuses_version_id"), table_name="budget_cfo_statuses")
    op.drop_table("budget_cfo_statuses")
