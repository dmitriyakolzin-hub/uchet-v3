"""Файлы к заявкам на оплату (счета)

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("payment_request_files",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("request_id", sa.BigInteger(), nullable=False),
    sa.Column("filename", sa.String(length=255), nullable=False),
    sa.Column("content_type", sa.String(length=100), nullable=False),
    sa.Column("size", sa.BigInteger(), nullable=False),
    sa.Column("data", sa.LargeBinary(), nullable=False),
    sa.Column("uploaded_by", sa.BigInteger(), nullable=False),
    sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["request_id"], ["payment_requests.id"], name=op.f("fk_payment_request_files_request_id_payment_requests"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], name=op.f("fk_payment_request_files_uploaded_by_users"), ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_request_files"))
    )
    op.create_index(op.f("ix_payment_request_files_request_id"), "payment_request_files", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_request_files_request_id"), table_name="payment_request_files")
    op.drop_table("payment_request_files")
