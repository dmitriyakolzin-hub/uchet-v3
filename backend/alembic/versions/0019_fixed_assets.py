"""Основные средства: карточки, начисления амортизации, аналитика «Основное средство» на 01, 02, 08

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSET_ACCOUNTS = "('01', '02', '08')"
KINDS_OLD = ("kind IN ('bank_account', 'counterparty', 'contract', 'employee', 'nomenclature', "
             "'warehouse', 'cfo', 'cash_flow_item', 'pl_item')")
KINDS_NEW = ("kind IN ('bank_account', 'counterparty', 'contract', 'employee', 'nomenclature', "
             "'warehouse', 'fixed_asset', 'cfo', 'cash_flow_item', 'pl_item')")


def upgrade() -> None:
    op.create_table('depreciation_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_depreciation_runs_month')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_depreciation_runs_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_depreciation_runs_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_depreciation_runs_operation_id_operations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_depreciation_runs_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_depreciation_runs')),
    sa.UniqueConstraint('legal_entity_id', 'year', 'month', name='uq_depreciation_runs_entity_month')
    )
    op.create_index(op.f('ix_depreciation_runs_organization_id'), 'depreciation_runs', ['organization_id'], unique=False)
    op.create_table('fixed_assets',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('inventory_number', sa.String(length=50), server_default='', nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('cost', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('useful_life_months', sa.SmallInteger(), nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('pl_item_id', sa.BigInteger(), nullable=True),
    sa.Column('commissioned_on', sa.Date(), nullable=True),
    sa.Column('commission_operation_id', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('cost >= 0', name=op.f('ck_fixed_assets_cost')),
    sa.CheckConstraint('useful_life_months BETWEEN 1 AND 1200', name=op.f('ck_fixed_assets_useful_life')),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_fixed_assets_cfo_id_cfos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['commission_operation_id'], ['operations.id'], name=op.f('fk_fixed_assets_commission_operation_id_operations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_fixed_assets_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_fixed_assets_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pl_item_id'], ['articles.id'], name=op.f('fk_fixed_assets_pl_item_id_articles'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fixed_assets'))
    )
    op.create_index(op.f('ix_fixed_assets_organization_id'), 'fixed_assets', ['organization_id'], unique=False)
    op.create_index('uq_fixed_assets_organization_inventory', 'fixed_assets', ['organization_id', 'inventory_number'], unique=True, postgresql_where=sa.text("inventory_number <> ''"))
    op.add_column('postings', sa.Column('dt_fixed_asset_id', sa.BigInteger(), nullable=True))
    op.add_column('postings', sa.Column('ct_fixed_asset_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_postings_dt_fixed_asset_id_fixed_assets'), 'postings', 'fixed_assets', ['dt_fixed_asset_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_postings_ct_fixed_asset_id_fixed_assets'), 'postings', 'fixed_assets', ['ct_fixed_asset_id'], ['id'], ondelete='RESTRICT')

    op.drop_constraint(op.f("ck_account_analytics_kind"), "account_analytics", type_="check")
    op.create_check_constraint("kind", "account_analytics", KINDS_NEW)
    # остатки и движение по каждому основному средству: обязательная аналитика на 01, 02, 08
    op.execute(
        f"""
        INSERT INTO account_analytics (account_id, kind, position)
        SELECT a.id, 'fixed_asset', COALESCE((SELECT max(position) FROM account_analytics WHERE account_id = a.id), 0) + 1
        FROM accounts a
        WHERE a.code IN {ASSET_ACCOUNTS}
          AND NOT EXISTS (SELECT 1 FROM account_analytics x WHERE x.account_id = a.id AND x.kind = 'fixed_asset')
        """
    )
    op.execute(
        """
        INSERT INTO articles (name, is_pl, pl_section, debit_account_id, credit_account_id)
        VALUES ('Амортизация основных средств', true, 'admin',
                (SELECT id FROM accounts WHERE code = '26'), (SELECT id FROM accounts WHERE code = '02'))
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM account_analytics WHERE kind = 'fixed_asset'")
    op.drop_constraint(op.f("ck_account_analytics_kind"), "account_analytics", type_="check")
    op.create_check_constraint("kind", "account_analytics", KINDS_OLD)
    op.drop_constraint(op.f('fk_postings_ct_fixed_asset_id_fixed_assets'), 'postings', type_='foreignkey')
    op.drop_constraint(op.f('fk_postings_dt_fixed_asset_id_fixed_assets'), 'postings', type_='foreignkey')
    op.drop_column('postings', 'ct_fixed_asset_id')
    op.drop_column('postings', 'dt_fixed_asset_id')
    op.drop_index('uq_fixed_assets_organization_inventory', table_name='fixed_assets', postgresql_where=sa.text("inventory_number <> ''"))
    op.drop_index(op.f('ix_fixed_assets_organization_id'), table_name='fixed_assets')
    op.drop_table('fixed_assets')
    op.drop_index(op.f('ix_depreciation_runs_organization_id'), table_name='depreciation_runs')
    op.drop_table('depreciation_runs')
