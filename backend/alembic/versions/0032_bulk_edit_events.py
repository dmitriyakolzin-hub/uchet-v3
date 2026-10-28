"""История операции: массовое изменение (действие bulk_edited) и описание, что изменилось

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "action IN ('created', 'updated', 'posted', 'unposted', 'cancelled', 'restored')"
NEW = "action IN ('created', 'updated', 'posted', 'unposted', 'cancelled', 'restored', 'bulk_edited')"


def upgrade() -> None:
    op.add_column("operation_events", sa.Column("details", sa.Text(), server_default="", nullable=False))
    op.drop_constraint(op.f("ck_operation_events_action"), "operation_events", type_="check")
    op.create_check_constraint(op.f("ck_operation_events_action"), "operation_events", NEW)


def downgrade() -> None:
    op.execute("UPDATE operation_events SET action = 'updated' WHERE action = 'bulk_edited'")
    op.drop_constraint(op.f("ck_operation_events_action"), "operation_events", type_="check")
    op.create_check_constraint(op.f("ck_operation_events_action"), "operation_events", OLD)
    op.drop_column("operation_events", "details")
