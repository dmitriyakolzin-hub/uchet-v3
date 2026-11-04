"""Реестр накладных поставщиков, платежи внутри группы

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('supplier_invoices',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('counterparty_id', sa.BigInteger(), nullable=False),
    sa.Column('contract_id', sa.BigInteger(), nullable=True),
    sa.Column('kind', sa.String(length=10), server_default='invoice', nullable=False),
    sa.Column('number', sa.String(length=50), nullable=False),
    sa.Column('doc_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('invoice', 'advance')", name=op.f('ck_supplier_invoices_kind')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_supplier_invoices_amount')),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], name=op.f('fk_supplier_invoices_contract_id_contracts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['counterparty_id'], ['counterparties.id'], name=op.f('fk_supplier_invoices_counterparty_id_counterparties'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_supplier_invoices_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_supplier_invoices_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_supplier_invoices_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_invoices')),
    sa.UniqueConstraint('legal_entity_id', 'counterparty_id', 'kind', 'number', 'doc_date', name='uq_supplier_invoices_document')
    )
    op.create_index(op.f('ix_supplier_invoices_counterparty_id'), 'supplier_invoices', ['counterparty_id'], unique=False)
    op.create_index(op.f('ix_supplier_invoices_organization_id'), 'supplier_invoices', ['organization_id'], unique=False)
    op.create_table('supplier_payments',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('invoice_id', sa.BigInteger(), nullable=False),
    sa.Column('request_id', sa.BigInteger(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_supplier_payments_amount')),
    sa.ForeignKeyConstraint(['invoice_id'], ['supplier_invoices.id'], name=op.f('fk_supplier_payments_invoice_id_supplier_invoices'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['request_id'], ['payment_requests.id'], name=op.f('fk_supplier_payments_request_id_payment_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_payments')),
    sa.UniqueConstraint('invoice_id', 'request_id', name='uq_supplier_payments_invoice_request')
    )
    op.create_index(op.f('ix_supplier_payments_invoice_id'), 'supplier_payments', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_supplier_payments_request_id'), 'supplier_payments', ['request_id'], unique=False)
    op.add_column('contracts', sa.Column('deferral_days', sa.SmallInteger(), nullable=True))
    op.add_column('contracts', sa.Column('debt_limit', sa.Numeric(precision=18, scale=2), nullable=True))
    op.create_check_constraint(op.f('ck_contracts_deferral_days'), 'contracts',
                               'deferral_days IS NULL OR deferral_days BETWEEN 0 AND 365')
    op.create_check_constraint(op.f('ck_contracts_debt_limit'), 'contracts', 'debt_limit IS NULL OR debt_limit > 0')
    op.add_column('payment_requests', sa.Column('linked_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_payment_requests_linked_id'), 'payment_requests', ['linked_id'], unique=False)
    op.create_foreign_key(op.f('fk_payment_requests_linked_id_payment_requests'), 'payment_requests', 'payment_requests', ['linked_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_payment_requests_linked_id_payment_requests'), 'payment_requests', type_='foreignkey')
    op.drop_index(op.f('ix_payment_requests_linked_id'), table_name='payment_requests')
    op.drop_column('payment_requests', 'linked_id')
    op.drop_constraint(op.f('ck_contracts_debt_limit'), 'contracts', type_='check')
    op.drop_constraint(op.f('ck_contracts_deferral_days'), 'contracts', type_='check')
    op.drop_column('contracts', 'debt_limit')
    op.drop_column('contracts', 'deferral_days')
    op.drop_index(op.f('ix_supplier_payments_request_id'), table_name='supplier_payments')
    op.drop_index(op.f('ix_supplier_payments_invoice_id'), table_name='supplier_payments')
    op.drop_table('supplier_payments')
    op.drop_index(op.f('ix_supplier_invoices_organization_id'), table_name='supplier_invoices')
    op.drop_index(op.f('ix_supplier_invoices_counterparty_id'), table_name='supplier_invoices')
    op.drop_table('supplier_invoices')
