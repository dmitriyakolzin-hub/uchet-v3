"""Плановые технические операции в календаре

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0045'
down_revision: str | None = '0044'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('calendar_transfers',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('from_bank_account_id', sa.BigInteger(), nullable=True),
    sa.Column('from_cash_cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('to_bank_account_id', sa.BigInteger(), nullable=True),
    sa.Column('to_cash_cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('description', sa.String(length=500), server_default='', nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(from_bank_account_id IS NULL) <> (from_cash_cfo_id IS NULL)', name=op.f('ck_calendar_transfers_from_source')),
    sa.CheckConstraint('(to_bank_account_id IS NULL) <> (to_cash_cfo_id IS NULL)', name=op.f('ck_calendar_transfers_to_source')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_calendar_transfers_amount')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_calendar_transfers_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['from_bank_account_id'], ['bank_accounts.id'], name=op.f('fk_calendar_transfers_from_bank_account_id_bank_accounts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_cash_cfo_id'], ['cfos.id'], name=op.f('fk_calendar_transfers_from_cash_cfo_id_cfos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_calendar_transfers_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_calendar_transfers_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['to_bank_account_id'], ['bank_accounts.id'], name=op.f('fk_calendar_transfers_to_bank_account_id_bank_accounts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_cash_cfo_id'], ['cfos.id'], name=op.f('fk_calendar_transfers_to_cash_cfo_id_cfos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_transfers'))
    )
    op.create_index(op.f('ix_calendar_transfers_organization_id'), 'calendar_transfers', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_calendar_transfers_organization_id'), table_name='calendar_transfers')
    op.drop_table('calendar_transfers')
