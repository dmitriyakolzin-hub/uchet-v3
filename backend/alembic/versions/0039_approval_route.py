"""Маршрут согласования заявок, роли «Финансовый менеджер» и «Руководитель ЦФУ»

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = "role IN ('finance_director', 'ceo', 'cfo_head')"
NEW_ROLES = "role IN ('finance_director', 'finance_manager', 'ceo', 'cfo_head', 'executor_head')"
OLD_STATUSES = "status IN ('draft', 'pending', 'approved', 'rejected', 'paid')"
NEW_STATUSES = "status IN ('draft', 'pending', 'revision', 'approved', 'rejected', 'paid')"


def upgrade() -> None:
    op.create_table("payment_request_events",
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("request_id", sa.BigInteger(), nullable=False),
    sa.Column("user_id", sa.BigInteger(), nullable=False),
    sa.Column("action", sa.String(length=20), nullable=False),
    sa.Column("step", sa.String(length=20), server_default="", nullable=False),
    sa.Column("comment", sa.String(length=1000), server_default="", nullable=False),
    sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["request_id"], ["payment_requests.id"], name=op.f("fk_payment_request_events_request_id_payment_requests"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_payment_request_events_user_id_users"), ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_request_events"))
    )
    op.create_index(op.f("ix_payment_request_events_request_id"), "payment_request_events", ["request_id"], unique=False)
    op.add_column("memberships", sa.Column("executor_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_memberships_executor_id"), "memberships", ["executor_id"], unique=False)
    op.create_foreign_key(op.f("fk_memberships_executor_id_executors"), "memberships", "executors", ["executor_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint(op.f("ck_memberships_role"), "memberships", type_="check")
    op.create_check_constraint(op.f("ck_memberships_role"), "memberships", NEW_ROLES)
    op.create_check_constraint(op.f("ck_memberships_executor_only_for_executor_head"), "memberships",
                               "executor_id IS NULL OR role = 'executor_head'")

    op.add_column("payment_requests", sa.Column("source", sa.String(length=10), server_default="request", nullable=False))
    op.add_column("payment_requests", sa.Column("initiator", sa.String(length=10), server_default="finance", nullable=False))
    op.add_column("payment_requests", sa.Column("route", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False))
    op.add_column("payment_requests", sa.Column("step", sa.SmallInteger(), server_default="0", nullable=False))
    op.drop_constraint(op.f("ck_payment_requests_status"), "payment_requests", type_="check")
    op.create_check_constraint(op.f("ck_payment_requests_status"), "payment_requests", NEW_STATUSES)
    op.create_check_constraint(op.f("ck_payment_requests_source_kind"), "payment_requests",
                               "source IN ('request', 'periodic', 'supplier')")
    op.create_check_constraint(op.f("ck_payment_requests_initiator"), "payment_requests",
                               "initiator IN ('cfo', 'executor', 'finance')")
    # уже поданные заявки согласовывает финансовый директор (как было до маршрута)
    op.execute("""UPDATE payment_requests SET route = '["fd"]' WHERE status = 'pending'""")


def downgrade() -> None:
    op.execute("UPDATE payment_requests SET status = 'draft' WHERE status = 'revision'")
    op.drop_constraint(op.f("ck_payment_requests_initiator"), "payment_requests", type_="check")
    op.drop_constraint(op.f("ck_payment_requests_source_kind"), "payment_requests", type_="check")
    op.drop_constraint(op.f("ck_payment_requests_status"), "payment_requests", type_="check")
    op.create_check_constraint(op.f("ck_payment_requests_status"), "payment_requests", OLD_STATUSES)
    op.drop_column("payment_requests", "step")
    op.drop_column("payment_requests", "route")
    op.drop_column("payment_requests", "initiator")
    op.drop_column("payment_requests", "source")
    op.execute("DELETE FROM memberships WHERE role IN ('finance_manager', 'executor_head')")
    op.drop_constraint(op.f("ck_memberships_executor_only_for_executor_head"), "memberships", type_="check")
    op.drop_constraint(op.f("ck_memberships_role"), "memberships", type_="check")
    op.create_check_constraint(op.f("ck_memberships_role"), "memberships", OLD_ROLES)
    op.drop_constraint(op.f("fk_memberships_executor_id_executors"), "memberships", type_="foreignkey")
    op.drop_index(op.f("ix_memberships_executor_id"), table_name="memberships")
    op.drop_column("memberships", "executor_id")
    op.drop_index(op.f("ix_payment_request_events_request_id"), table_name="payment_request_events")
    op.drop_table("payment_request_events")
