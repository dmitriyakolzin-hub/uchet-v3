"""Инвестиционный бюджет: инвестпроекты, план инвестиций (plan_kind inv), проект в ОС и договорах, статьи вложений

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARTICLES = ["Инвестиции: оборудование", "Инвестиции: ремонт и строительство", "Инвестиции: прочие вложения"]


def upgrade() -> None:
    op.create_table('investment_projects',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=False),
    sa.Column('executor_id', sa.BigInteger(), nullable=True),
    sa.Column('start_on', sa.Date(), nullable=True),
    sa.Column('finish_on', sa.Date(), nullable=True),
    sa.Column('limit_amount', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=10), server_default='planned', nullable=False),
    sa.Column('payment_lag_months', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("status IN ('planned', 'active', 'done')", name=op.f('ck_investment_projects_status')),
    sa.CheckConstraint('finish_on IS NULL OR start_on IS NULL OR finish_on >= start_on', name=op.f('ck_investment_projects_dates')),
    sa.CheckConstraint('limit_amount >= 0', name=op.f('ck_investment_projects_limit_amount')),
    sa.CheckConstraint('payment_lag_months BETWEEN 0 AND 24', name=op.f('ck_investment_projects_payment_lag')),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_investment_projects_cfo_id_cfos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['executor_id'], ['executors.id'], name=op.f('fk_investment_projects_executor_id_executors'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_investment_projects_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_investment_projects'))
    )
    op.create_index(op.f('ix_investment_projects_organization_id'), 'investment_projects', ['organization_id'], unique=False)
    op.add_column('contracts', sa.Column('project_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_contracts_project_id_investment_projects'), 'contracts', 'investment_projects', ['project_id'], ['id'], ondelete='RESTRICT')
    op.add_column('fixed_assets', sa.Column('project_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_fixed_assets_project_id_investment_projects'), 'fixed_assets', 'investment_projects', ['project_id'], ['id'], ondelete='RESTRICT')
    op.add_column('operations', sa.Column('plan_project_id', sa.BigInteger(), nullable=True))
    op.alter_column('operations', 'plan_kind',
               existing_type=sa.VARCHAR(length=2),
               type_=sa.String(length=3),
               existing_nullable=True)
    op.drop_index(op.f('uq_operations_plan_cell'), table_name='operations', postgresql_where='(budget_version_id IS NOT NULL)', postgresql_nulls_not_distinct=True)
    op.create_index('uq_operations_plan_cell', 'operations', ['budget_version_id', 'plan_kind', 'plan_cfo_id', 'plan_executor_id', 'plan_project_id', 'article_id', 'op_date'], unique=True, postgresql_nulls_not_distinct=True, postgresql_where=sa.text('budget_version_id IS NOT NULL'))
    op.create_foreign_key(op.f('fk_operations_plan_project_id_investment_projects'), 'operations', 'investment_projects', ['plan_project_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f("ck_operations_plan_kind"), "operations", type_="check")
    op.create_check_constraint("plan_kind", "operations", "plan_kind IS NULL OR plan_kind IN ('pl', 'cf', 'inv')")
    conn = op.get_bind()
    for name in ARTICLES:
        conn.execute(sa.text(
            "INSERT INTO articles (name, is_cash_flow, cash_flow_activity, cash_flow_direction, debit_account_id, "
            "credit_account_id) VALUES (:name, true, 'investing', 'outflow', (SELECT id FROM accounts WHERE code = '08'), "
            "(SELECT id FROM accounts WHERE code = '60.01')) ON CONFLICT (name) DO NOTHING"), {"name": name})


def downgrade() -> None:
    op.execute("DELETE FROM operations WHERE plan_kind = 'inv'")
    op.drop_constraint(op.f("ck_operations_plan_kind"), "operations", type_="check")
    op.create_check_constraint("plan_kind", "operations", "plan_kind IS NULL OR plan_kind IN ('pl', 'cf')")
    op.drop_constraint(op.f('fk_operations_plan_project_id_investment_projects'), 'operations', type_='foreignkey')
    op.drop_index('uq_operations_plan_cell', table_name='operations', postgresql_nulls_not_distinct=True, postgresql_where=sa.text('budget_version_id IS NOT NULL'))
    op.create_index(op.f('uq_operations_plan_cell'), 'operations', ['budget_version_id', 'plan_kind', 'plan_cfo_id', 'plan_executor_id', 'article_id', 'op_date'], unique=True, postgresql_where='(budget_version_id IS NOT NULL)', postgresql_nulls_not_distinct=True)
    op.alter_column('operations', 'plan_kind',
               existing_type=sa.String(length=3),
               type_=sa.VARCHAR(length=2),
               existing_nullable=True)
    op.drop_column('operations', 'plan_project_id')
    op.drop_constraint(op.f('fk_fixed_assets_project_id_investment_projects'), 'fixed_assets', type_='foreignkey')
    op.drop_column('fixed_assets', 'project_id')
    op.drop_constraint(op.f('fk_contracts_project_id_investment_projects'), 'contracts', type_='foreignkey')
    op.drop_column('contracts', 'project_id')
    op.drop_index(op.f('ix_investment_projects_organization_id'), table_name='investment_projects')
    op.drop_table('investment_projects')
