"""Форма управленческого отчёта организации: строки загруженного справочника (заголовки, статьи, показатели)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("report_form_rows",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("organization_id", sa.BigInteger(), nullable=False),
    sa.Column("position", sa.Integer(), nullable=False),
    sa.Column("code", sa.String(length=30), nullable=True),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("kind", sa.String(length=10), nullable=False),
    sa.Column("article_id", sa.BigInteger(), nullable=True),
    sa.Column("formula", sa.String(length=500), server_default="", nullable=False),
    sa.Column("invert", sa.Boolean(), server_default="false", nullable=False),
    sa.Column("note", sa.String(length=100), server_default="", nullable=False),
    sa.CheckConstraint("kind IN ('header', 'article', 'indicator')", name=op.f("ck_report_form_rows_kind")),
    sa.ForeignKeyConstraint(["article_id"], ["articles.id"], name=op.f("fk_report_form_rows_article_id_articles"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_report_form_rows_organization_id_organizations"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_report_form_rows"))
    )
    op.create_index(op.f("ix_report_form_rows_organization_id"), "report_form_rows", ["organization_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_report_form_rows_organization_id"), table_name="report_form_rows")
    op.drop_table("report_form_rows")
