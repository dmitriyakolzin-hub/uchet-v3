"""Банки, расчётные счета, зарплатные проекты, контрагенты, договоры

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('banks',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('bic', sa.String(length=9), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('corr_account', sa.String(length=20), nullable=True),
    sa.Column('city', sa.String(length=100), server_default='', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("bic ~ '^[0-9]{9}$'", name=op.f('ck_banks_bic')),
    sa.CheckConstraint("corr_account IS NULL OR corr_account ~ '^[0-9]{20}$'", name=op.f('ck_banks_corr_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_banks')),
    sa.UniqueConstraint('bic', name=op.f('uq_banks_bic'))
    )
    op.create_table('counterparties',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=500), server_default='', nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('inn', sa.String(length=12), nullable=True),
    sa.Column('kpp', sa.String(length=9), nullable=True),
    sa.Column('is_customer', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_supplier', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("(kind = 'legal' AND inn ~ '^[0-9]{10}$') OR (kind = 'entrepreneur' AND inn ~ '^[0-9]{12}$') OR (kind = 'person' AND (inn IS NULL OR inn ~ '^[0-9]{12}$'))", name=op.f('ck_counterparties_inn')),
    sa.CheckConstraint("kind IN ('legal', 'entrepreneur', 'person')", name=op.f('ck_counterparties_kind')),
    sa.CheckConstraint("kpp IS NULL OR kpp ~ '^[0-9]{9}$'", name=op.f('ck_counterparties_kpp')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_counterparties_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_counterparties'))
    )
    op.create_index(op.f('ix_counterparties_organization_id'), 'counterparties', ['organization_id'], unique=False)
    op.create_index('uq_counterparties_organization_inn', 'counterparties', ['organization_id', 'inn'], unique=True, postgresql_where=sa.text('inn IS NOT NULL'))
    op.create_table('bank_accounts',
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('bank_id', sa.BigInteger(), nullable=False),
    sa.Column('currency_id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("number ~ '^[0-9]{20}$'", name=op.f('ck_bank_accounts_number')),
    sa.ForeignKeyConstraint(['bank_id'], ['banks.id'], name=op.f('fk_bank_accounts_bank_id_banks'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['currency_id'], ['currencies.id'], name=op.f('fk_bank_accounts_currency_id_currencies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_bank_accounts_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_bank_accounts_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_accounts')),
    sa.UniqueConstraint('legal_entity_id', 'number', name='uq_bank_accounts_entity_number')
    )
    op.create_index(op.f('ix_bank_accounts_organization_id'), 'bank_accounts', ['organization_id'], unique=False)
    op.create_table('contracts',
    sa.Column('counterparty_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('currency_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('number', sa.String(length=100), nullable=False),
    sa.Column('contract_date', sa.Date(), nullable=True),
    sa.Column('name', sa.String(length=255), server_default='', nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("kind IN ('customer', 'supplier', 'other')", name=op.f('ck_contracts_kind')),
    sa.ForeignKeyConstraint(['counterparty_id'], ['counterparties.id'], name=op.f('fk_contracts_counterparty_id_counterparties'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['currency_id'], ['currencies.id'], name=op.f('fk_contracts_currency_id_currencies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_contracts_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_contracts_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contracts'))
    )
    op.create_index(op.f('ix_contracts_counterparty_id'), 'contracts', ['counterparty_id'], unique=False)
    op.create_index(op.f('ix_contracts_organization_id'), 'contracts', ['organization_id'], unique=False)
    op.create_table('salary_projects',
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('bank_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['bank_id'], ['banks.id'], name=op.f('fk_salary_projects_bank_id_banks'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_salary_projects_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_salary_projects_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_salary_projects'))
    )
    op.create_index(op.f('ix_salary_projects_organization_id'), 'salary_projects', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_salary_projects_organization_id'), table_name='salary_projects')
    op.drop_table('salary_projects')
    op.drop_index(op.f('ix_contracts_organization_id'), table_name='contracts')
    op.drop_index(op.f('ix_contracts_counterparty_id'), table_name='contracts')
    op.drop_table('contracts')
    op.drop_index(op.f('ix_bank_accounts_organization_id'), table_name='bank_accounts')
    op.drop_table('bank_accounts')
    op.drop_index('uq_counterparties_organization_inn', table_name='counterparties', postgresql_where=sa.text('inn IS NOT NULL'))
    op.drop_index(op.f('ix_counterparties_organization_id'), table_name='counterparties')
    op.drop_table('counterparties')
    op.drop_table('banks')
