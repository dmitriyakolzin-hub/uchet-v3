"""Периодические платежи по договорам

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("contract_payments",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("contract_id", sa.BigInteger(), nullable=False),
    sa.Column("valid_from", sa.Date(), nullable=False),
    sa.Column("valid_to", sa.Date(), nullable=True),
    sa.Column("day", sa.SmallInteger(), nullable=False),
    sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column("bank_account_id", sa.BigInteger(), nullable=True),
    sa.Column("article_id", sa.BigInteger(), nullable=True),
    sa.Column("cfo_id", sa.BigInteger(), nullable=True),
    sa.Column("executor_id", sa.BigInteger(), nullable=True),
    sa.Column("description", sa.String(length=500), server_default="", nullable=False),
    sa.Column("created_by", sa.BigInteger(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.CheckConstraint("amount > 0", name=op.f("ck_contract_payments_amount")),
    sa.CheckConstraint("day BETWEEN 1 AND 31", name=op.f("ck_contract_payments_day")),
    sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_contract_payments_period")),
    sa.ForeignKeyConstraint(["article_id"], ["articles.id"], name=op.f("fk_contract_payments_article_id_articles"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], name=op.f("fk_contract_payments_bank_account_id_bank_accounts"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_contract_payments_cfo_id_cfos"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], name=op.f("fk_contract_payments_contract_id_contracts"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_contract_payments_created_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["executor_id"], ["executors.id"], name=op.f("fk_contract_payments_executor_id_executors"), ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_contract_payments"))
    )
    op.create_index(op.f("ix_contract_payments_contract_id"), "contract_payments", ["contract_id"], unique=False)
    op.add_column("payment_requests", sa.Column("periodic_id", sa.BigInteger(), nullable=True))
    op.add_column("payment_requests", sa.Column("period", sa.Date(), nullable=True))
    op.create_unique_constraint("uq_payment_requests_periodic_period", "payment_requests", ["periodic_id", "period"])
    op.create_foreign_key(op.f("fk_payment_requests_periodic_id_contract_payments"), "payment_requests", "contract_payments", ["periodic_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_payment_requests_periodic_id_contract_payments"), "payment_requests", type_="foreignkey")
    op.drop_constraint("uq_payment_requests_periodic_period", "payment_requests", type_="unique")
    op.drop_column("payment_requests", "period")
    op.drop_column("payment_requests", "periodic_id")
    op.drop_index(op.f("ix_contract_payments_contract_id"), table_name="contract_payments")
    op.drop_table("contract_payments")
