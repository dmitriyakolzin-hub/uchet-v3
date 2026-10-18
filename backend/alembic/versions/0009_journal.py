"""Журнал операций: операции, проводки (аналитика по сторонам), история

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('operations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('op_date', sa.Date(), nullable=False),
    sa.Column('article_id', sa.BigInteger(), nullable=True),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.String(length=20), server_default='draft', nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('posted_by', sa.BigInteger(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('draft', 'posted', 'cancelled')", name=op.f('ck_operations_status')),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_operations_article_id_articles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_operations_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_operations_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_operations_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['posted_by'], ['users.id'], name=op.f('fk_operations_posted_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_operations_updated_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations')),
    sa.UniqueConstraint('organization_id', 'number', name='uq_operations_organization_number')
    )
    op.create_index('ix_operations_organization_date', 'operations', ['organization_id', 'op_date'], unique=False)
    op.create_table('operation_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('created', 'updated', 'posted', 'unposted', 'cancelled', 'restored')", name=op.f('ck_operation_events_action')),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_operation_events_operation_id_operations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_operation_events_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operation_events'))
    )
    op.create_index(op.f('ix_operation_events_operation_id'), 'operation_events', ['operation_id'], unique=False)
    op.create_table('postings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.SmallInteger(), nullable=False),
    sa.Column('debit_account_id', sa.BigInteger(), nullable=True),
    sa.Column('credit_account_id', sa.BigInteger(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('currency_id', sa.BigInteger(), nullable=False),
    sa.Column('amount_rub', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('amount_rub_manual', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=3), nullable=True),
    sa.Column('dt_bank_account_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_counterparty_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_contract_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_employee_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_nomenclature_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_warehouse_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_cash_flow_article_id', sa.BigInteger(), nullable=True),
    sa.Column('dt_pl_article_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_bank_account_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_counterparty_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_contract_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_employee_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_nomenclature_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_warehouse_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_cash_flow_article_id', sa.BigInteger(), nullable=True),
    sa.Column('ct_pl_article_id', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('amount IS NULL OR amount > 0', name=op.f('ck_postings_amount_positive')),
    sa.CheckConstraint('quantity IS NULL OR quantity > 0', name=op.f('ck_postings_quantity_positive')),
    sa.ForeignKeyConstraint(['credit_account_id'], ['accounts.id'], name=op.f('fk_postings_credit_account_id_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_bank_account_id'], ['bank_accounts.id'], name=op.f('fk_postings_ct_bank_account_id_bank_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_cash_flow_article_id'], ['articles.id'], name=op.f('fk_postings_ct_cash_flow_article_id_articles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_cfo_id'], ['cfos.id'], name=op.f('fk_postings_ct_cfo_id_cfos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_contract_id'], ['contracts.id'], name=op.f('fk_postings_ct_contract_id_contracts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_counterparty_id'], ['counterparties.id'], name=op.f('fk_postings_ct_counterparty_id_counterparties'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_employee_id'], ['employees.id'], name=op.f('fk_postings_ct_employee_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_nomenclature_id'], ['nomenclature.id'], name=op.f('fk_postings_ct_nomenclature_id_nomenclature'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_pl_article_id'], ['articles.id'], name=op.f('fk_postings_ct_pl_article_id_articles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ct_warehouse_id'], ['warehouses.id'], name=op.f('fk_postings_ct_warehouse_id_warehouses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['currency_id'], ['currencies.id'], name=op.f('fk_postings_currency_id_currencies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['debit_account_id'], ['accounts.id'], name=op.f('fk_postings_debit_account_id_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_bank_account_id'], ['bank_accounts.id'], name=op.f('fk_postings_dt_bank_account_id_bank_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_cash_flow_article_id'], ['articles.id'], name=op.f('fk_postings_dt_cash_flow_article_id_articles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_cfo_id'], ['cfos.id'], name=op.f('fk_postings_dt_cfo_id_cfos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_contract_id'], ['contracts.id'], name=op.f('fk_postings_dt_contract_id_contracts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_counterparty_id'], ['counterparties.id'], name=op.f('fk_postings_dt_counterparty_id_counterparties'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_employee_id'], ['employees.id'], name=op.f('fk_postings_dt_employee_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_nomenclature_id'], ['nomenclature.id'], name=op.f('fk_postings_dt_nomenclature_id_nomenclature'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_pl_article_id'], ['articles.id'], name=op.f('fk_postings_dt_pl_article_id_articles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dt_warehouse_id'], ['warehouses.id'], name=op.f('fk_postings_dt_warehouse_id_warehouses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_postings_operation_id_operations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_postings'))
    )
    op.create_index(op.f('ix_postings_credit_account_id'), 'postings', ['credit_account_id'], unique=False)
    op.create_index(op.f('ix_postings_debit_account_id'), 'postings', ['debit_account_id'], unique=False)
    op.create_index(op.f('ix_postings_operation_id'), 'postings', ['operation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_postings_operation_id'), table_name='postings')
    op.drop_index(op.f('ix_postings_debit_account_id'), table_name='postings')
    op.drop_index(op.f('ix_postings_credit_account_id'), table_name='postings')
    op.drop_table('postings')
    op.drop_index(op.f('ix_operation_events_operation_id'), table_name='operation_events')
    op.drop_table('operation_events')
    op.drop_index('ix_operations_organization_date', table_name='operations')
    op.drop_table('operations')
