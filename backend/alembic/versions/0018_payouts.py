"""Ведомости на выплату зарплаты и аванса (черновик → отправлена в банк → оплачена)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('payout_sheets',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('bank_account_id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('pay_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='draft', nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=True),
    sa.Column('paid_on', sa.Date(), nullable=True),
    sa.Column('confirmed_by_statement', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('advance', 'salary')", name=op.f('ck_payout_sheets_kind')),
    sa.CheckConstraint("status IN ('draft', 'sent', 'paid')", name=op.f('ck_payout_sheets_status')),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_payout_sheets_month')),
    sa.ForeignKeyConstraint(['bank_account_id'], ['bank_accounts.id'], name=op.f('fk_payout_sheets_bank_account_id_bank_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_payout_sheets_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_payout_sheets_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_payout_sheets_operation_id_operations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_payout_sheets_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payout_sheets')),
    sa.UniqueConstraint('organization_id', 'number', name='uq_payout_sheets_organization_number')
    )
    op.create_index(op.f('ix_payout_sheets_organization_id'), 'payout_sheets', ['organization_id'], unique=False)
    op.create_table('payout_lines',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('sheet_id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_payout_lines_amount')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_payout_lines_employee_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['sheet_id'], ['payout_sheets.id'], name=op.f('fk_payout_lines_sheet_id_payout_sheets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payout_lines')),
    sa.UniqueConstraint('sheet_id', 'employee_id', name='uq_payout_lines_sheet_employee')
    )
    op.create_index(op.f('ix_payout_lines_sheet_id'), 'payout_lines', ['sheet_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_payout_lines_sheet_id'), table_name='payout_lines')
    op.drop_table('payout_lines')
    op.drop_index(op.f('ix_payout_sheets_organization_id'), table_name='payout_sheets')
    op.drop_table('payout_sheets')
