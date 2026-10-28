"""ЦФО и ЦФУ: код для загрузок из Excel (не повторяется в организации, если указан)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("cfos", "executors"):
        op.add_column(table, sa.Column("code", sa.String(length=50), server_default="", nullable=False))
        op.create_index(f"uq_{table}_organization_code", table, ["organization_id", "code"], unique=True,
                        postgresql_where=sa.text("code <> ''"))


def downgrade() -> None:
    for table in ("cfos", "executors"):
        op.drop_index(f"uq_{table}_organization_code", table_name=table, postgresql_where=sa.text("code <> ''"))
        op.drop_column(table, "code")
