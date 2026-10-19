"""Закрытие периода: дата закрытия, история закрытий; регламентные операции и их выполнение

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('period_closings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('action', sa.String(length=10), nullable=False),
    sa.Column('closed_through', sa.Date(), nullable=True),
    sa.Column('previous', sa.Date(), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('close', 'reopen')", name=op.f('ck_period_closings_action')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_period_closings_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_period_closings_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_period_closings'))
    )
    op.create_index(op.f('ix_period_closings_organization_id'), 'period_closings', ['organization_id'], unique=False)
    op.create_table('routine_rules',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('day', sa.SmallInteger(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('post', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('template', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('day BETWEEN 1 AND 31', name=op.f('ck_routine_rules_day')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name=op.f('ck_routine_rules_period')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_routine_rules_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_routine_rules_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_routine_rules'))
    )
    op.create_index(op.f('ix_routine_rules_organization_id'), 'routine_rules', ['organization_id'], unique=False)
    op.create_table('routine_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('rule_id', sa.BigInteger(), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_routine_runs_month')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_routine_runs_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_routine_runs_operation_id_operations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_id'], ['routine_rules.id'], name=op.f('fk_routine_runs_rule_id_routine_rules'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_routine_runs')),
    sa.UniqueConstraint('rule_id', 'year', 'month', name='uq_routine_runs_rule_month')
    )
    op.create_index(op.f('ix_routine_runs_rule_id'), 'routine_runs', ['rule_id'], unique=False)
    op.add_column('organizations', sa.Column('closed_through', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('organizations', 'closed_through')
    op.drop_index(op.f('ix_routine_runs_rule_id'), table_name='routine_runs')
    op.drop_table('routine_runs')
    op.drop_index(op.f('ix_routine_rules_organization_id'), table_name='routine_rules')
    op.drop_table('routine_rules')
    op.drop_index(op.f('ix_period_closings_organization_id'), table_name='period_closings')
    op.drop_table('period_closings')
