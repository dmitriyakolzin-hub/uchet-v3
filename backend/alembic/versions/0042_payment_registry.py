"""Реестр на оплату: реквизиты контрагента, отметка «отправлено в банк»

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("counterparties", sa.Column("account_number", sa.String(length=20), nullable=True))
    op.add_column("counterparties", sa.Column("bank_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_counterparties_bank_id_banks"), "counterparties", "banks", ["bank_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint(op.f("ck_counterparties_account_number"), "counterparties",
                               "account_number IS NULL OR account_number ~ '^[0-9]{20}$'")
    op.add_column("payment_requests", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_requests", sa.Column("sent_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_payment_requests_sent_by_users"), "payment_requests", "users", ["sent_by"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_payment_requests_sent_by_users"), "payment_requests", type_="foreignkey")
    op.drop_column("payment_requests", "sent_by")
    op.drop_column("payment_requests", "sent_at")
    op.drop_constraint(op.f("fk_counterparties_bank_id_banks"), "counterparties", type_="foreignkey")
    op.drop_constraint(op.f("ck_counterparties_account_number"), "counterparties", type_="check")
    op.drop_column("counterparties", "bank_id")
    op.drop_column("counterparties", "account_number")
