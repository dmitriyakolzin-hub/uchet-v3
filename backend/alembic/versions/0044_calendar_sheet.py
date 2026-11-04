"""Календарь по форме владельца: ручные остатки

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0044'
down_revision: str | None = '0043'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('calendar_balances',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('bank_account_id', sa.BigInteger(), nullable=True),
    sa.Column('cash_cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('entered_by', sa.BigInteger(), nullable=False),
    sa.Column('entered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(bank_account_id IS NULL) <> (cash_cfo_id IS NULL)', name=op.f('ck_calendar_balances_source')),
    sa.ForeignKeyConstraint(['bank_account_id'], ['bank_accounts.id'], name=op.f('fk_calendar_balances_bank_account_id_bank_accounts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['cash_cfo_id'], ['cfos.id'], name=op.f('fk_calendar_balances_cash_cfo_id_cfos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['entered_by'], ['users.id'], name=op.f('fk_calendar_balances_entered_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_calendar_balances_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_balances')),
    sa.UniqueConstraint('bank_account_id', 'cash_cfo_id', 'day', name='uq_calendar_balances_source_day', postgresql_nulls_not_distinct=True)
    )
    op.create_index(op.f('ix_calendar_balances_organization_id'), 'calendar_balances', ['organization_id'], unique=False)
    op.add_column('payment_requests', sa.Column('adjusted', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('payment_requests', 'adjusted')
    op.drop_index(op.f('ix_calendar_balances_organization_id'), table_name='calendar_balances')
    op.drop_table('calendar_balances')
