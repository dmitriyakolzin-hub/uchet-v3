"""Примечания к цифрам отчётов и операциям, метки примечаний

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("note_labels",
    sa.Column("name", sa.String(length=100), nullable=False),
    sa.Column("color", sa.String(length=7), server_default="#f79009", nullable=False),
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("organization_id", sa.BigInteger(), nullable=False),
    sa.CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_note_labels_color")),
    sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_note_labels_organization_id_organizations"), ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_note_labels"))
    )
    op.create_index(op.f("ix_note_labels_organization_id"), "note_labels", ["organization_id"], unique=False)
    op.create_table("notes",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("organization_id", sa.BigInteger(), nullable=False),
    sa.Column("report", sa.String(length=30), nullable=False),
    sa.Column("variant", sa.String(length=30), server_default="", nullable=False),
    sa.Column("row_key", sa.String(length=100), server_default="", nullable=False),
    sa.Column("row_title", sa.String(length=500), server_default="", nullable=False),
    sa.Column("column_key", sa.String(length=50), server_default="", nullable=False),
    sa.Column("column_title", sa.String(length=100), server_default="", nullable=False),
    sa.Column("date_from", sa.Date(), nullable=True),
    sa.Column("date_to", sa.Date(), nullable=True),
    sa.Column("legal_entity_id", sa.BigInteger(), nullable=True),
    sa.Column("cfo_id", sa.BigInteger(), nullable=True),
    sa.Column("executor_id", sa.BigInteger(), nullable=True),
    sa.Column("operation_id", sa.BigInteger(), nullable=True),
    sa.Column("label_id", sa.BigInteger(), nullable=True),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=10), server_default="open", nullable=False),
    sa.Column("created_by", sa.BigInteger(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("resolved_by", sa.BigInteger(), nullable=True),
    sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(operation_id IS NULL) <> (report = 'operation')", name=op.f("ck_notes_operation_note")),
    sa.CheckConstraint("status IN ('open', 'resolved')", name=op.f("ck_notes_status")),
    sa.ForeignKeyConstraint(["cfo_id"], ["cfos.id"], name=op.f("fk_notes_cfo_id_cfos"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_notes_created_by_users"), ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["executor_id"], ["executors.id"], name=op.f("fk_notes_executor_id_executors"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["label_id"], ["note_labels.id"], name=op.f("fk_notes_label_id_note_labels"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"], name=op.f("fk_notes_legal_entity_id_legal_entities"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], name=op.f("fk_notes_operation_id_operations"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_notes_organization_id_organizations"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], name=op.f("fk_notes_resolved_by_users"), ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_notes"))
    )
    op.create_index(op.f("ix_notes_operation_id"), "notes", ["operation_id"], unique=False)
    op.create_index("ix_notes_organization_report", "notes", ["organization_id", "report"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notes_organization_report", table_name="notes")
    op.drop_index(op.f("ix_notes_operation_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_index(op.f("ix_note_labels_organization_id"), table_name="note_labels")
    op.drop_table("note_labels")
