"""Финансовый результат: операции закрытия 26, 44, 90, 91 на 99 за месяц и реформации 99 → 84 за год

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('result_closings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('month', 'year')", name=op.f('ck_result_closings_kind')),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_result_closings_month')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_result_closings_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_result_closings_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_result_closings_operation_id_operations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_result_closings_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_result_closings')),
    sa.UniqueConstraint('legal_entity_id', 'year', 'month', 'kind', name='uq_result_closings_entity_month_kind')
    )
    op.create_index(op.f('ix_result_closings_organization_id'), 'result_closings', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_result_closings_organization_id'), table_name='result_closings')
    op.drop_table('result_closings')
