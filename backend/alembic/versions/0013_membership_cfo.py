"""Руководитель ЦФО: ветка дерева ЦФО в роли (memberships.cfo_id)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memberships", sa.Column("cfo_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_memberships_cfo_id_cfos"), "memberships", "cfos", ["cfo_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_index(op.f("ix_memberships_cfo_id"), "memberships", ["cfo_id"])
    op.create_check_constraint(op.f("ck_memberships_cfo_only_for_cfo_head"), "memberships",
                               "cfo_id IS NULL OR role = 'cfo_head'")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_memberships_cfo_only_for_cfo_head"), "memberships", type_="check")
    op.drop_index(op.f("ix_memberships_cfo_id"), table_name="memberships")
    op.drop_constraint(op.f("fk_memberships_cfo_id_cfos"), "memberships", type_="foreignkey")
    op.drop_column("memberships", "cfo_id")
