"""Подотчётное лицо в заявке на оплату; признак «операцию оплаты создала заявка»

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payment_requests", sa.Column("employee_id", sa.BigInteger(), nullable=True))
    op.add_column("payment_requests",
                  sa.Column("operation_created", sa.Boolean(), server_default="false", nullable=False))
    op.create_foreign_key(op.f("fk_payment_requests_employee_id_employees"), "payment_requests", "employees",
                          ["employee_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_payment_requests_employee_id_employees"), "payment_requests", type_="foreignkey")
    op.drop_column("payment_requests", "operation_created")
    op.drop_column("payment_requests", "employee_id")
