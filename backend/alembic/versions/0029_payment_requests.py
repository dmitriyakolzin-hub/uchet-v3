"""Платёжный календарь: заявки на оплату и ожидаемые поступления (docs/TZ.md, раздел 18)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("payment_requests",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("organization_id", sa.BigInteger(), nullable=False),
    sa.Column("number", sa.BigInteger(), nullable=False),
    sa.Column("direction", sa.String(length=3), nullable=False),
    sa.Column("pay_date", sa.Date(), nullable=False),
    sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
    sa.Column("bank_account_id", sa.BigInteger(), nullable=True),
    sa.Column("cash_cfo_id", sa.BigInteger(), nullable=True),
    sa.Column("counterparty_id", sa.BigInteger(), nullable=True),
    sa.Column("contract_id", sa.BigInteger(), nullable=True),
    sa.Column("article_id", sa.BigInteger(), nullable=True),
    sa.Column("cfo_id", sa.BigInteger(), nullable=True),
    sa.Column("executor_id", sa.BigInteger(), nullable=True),
    sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column("description", sa.String(length=2000), server_default="", nullable=False),
    sa.Column("status", sa.String(length=10), server_default="draft", nullable=False),
    sa.Column("reject_reason", sa.String(length=1000), server_default="", nullable=False),
    sa.Column("created_by", sa.BigInteger(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("approved_by", sa.BigInteger(), nullable=True),
    sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("paid_on", sa.Date(), nullable=True),
    sa.Column("operation_id", sa.BigInteger(), nullable=True),
    sa.Column("confirmed_by_statement", sa.Boolean(), server_default="false", nullable=False),
    sa.CheckConstraint("(status = 'paid') = (paid_on IS NOT NULL)", name=op.f("ck_payment_requests_paid")),
    sa.CheckConstraint("direction IN ('out', 'in')", name=op.f("ck_payment_requests_direction")),
    sa.CheckConstraint("status IN ('draft', 'pending', 'approved', 'rejected', 'paid')",
                       name=op.f("ck_payment_requests_status")),
    sa.CheckConstraint("amount > 0", name=op.f("ck_payment_requests_amount")),
    sa.CheckConstraint("bank_account_id IS NULL OR cash_cfo_id IS NULL", name=op.f("ck_payment_requests_source")),
    sa.ForeignKeyConstraint(["approved_by"], ["users.id"], name=op.f("fk_payment_requests_approved_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["article_id"], ["articles.id"], name=op.f("fk_payment_requests_article_id_articles"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], name=op.f("fk_payment_requests_bank_account_id_bank_accounts"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["cash_cfo_id"], ["cfos.id"], name=op.f("fk_payment_requests_cash_cfo_id_cfos"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_payment_requests_cfo_id_cfos"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], name=op.f("fk_payment_requests_contract_id_contracts"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["counterparty_id"], ["counterparties.id"], name=op.f("fk_payment_requests_counterparty_id_counterparties"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_payment_requests_created_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["executor_id"], ["executors.id"], name=op.f("fk_payment_requests_executor_id_executors"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"], name=op.f("fk_payment_requests_legal_entity_id_legal_entities"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], name=op.f("fk_payment_requests_operation_id_operations"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_payment_requests_organization_id_organizations"), ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_requests")),
    sa.UniqueConstraint("organization_id", "number", name="uq_payment_requests_organization_number")
    )
    op.create_index(op.f("ix_payment_requests_organization_id"), "payment_requests", ["organization_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_requests_organization_id"), table_name="payment_requests")
    op.drop_table("payment_requests")
