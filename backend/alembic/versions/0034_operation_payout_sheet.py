"""Операция — выплата по ведомости: ссылка на ведомость

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("payout_sheet_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_operations_payout_sheet_id", "operations", ["payout_sheet_id"])
    op.create_foreign_key("fk_operations_payout_sheet_id_payout_sheets", "operations", "payout_sheets",
                          ["payout_sheet_id"], ["id"], ondelete="SET NULL")
    # уже оплаченные ведомости: их операции оплаты получают ссылку
    op.execute("UPDATE operations o SET payout_sheet_id = s.id FROM payout_sheets s WHERE s.operation_id = o.id")


def downgrade() -> None:
    op.drop_constraint("fk_operations_payout_sheet_id_payout_sheets", "operations", type_="foreignkey")
    op.drop_index("ix_operations_payout_sheet_id", table_name="operations")
    op.drop_column("operations", "payout_sheet_id")
