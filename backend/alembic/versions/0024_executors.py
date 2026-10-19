"""ЦФУ (исполнители): справочник отдел → исполнитель, аналитика «ЦФУ» в проводках, ЦФУ в ячейке плана

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_OLD = ("kind IN ('bank_account', 'counterparty', 'contract', 'employee', 'nomenclature', "
             "'warehouse', 'fixed_asset', 'cfo', 'cash_flow_item', 'pl_item')")
KINDS_NEW = ("kind IN ('bank_account', 'counterparty', 'contract', 'employee', 'nomenclature', "
             "'warehouse', 'fixed_asset', 'cfo', 'executor', 'cash_flow_item', 'pl_item')")


def upgrade() -> None:
    op.create_table('executors',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('employee_id', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_executors_employee_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_executors_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_id'], ['executors.id'], name=op.f('fk_executors_parent_id_executors'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_executors'))
    )
    op.create_index(op.f('ix_executors_organization_id'), 'executors', ['organization_id'], unique=False)
    op.create_index(op.f('ix_executors_parent_id'), 'executors', ['parent_id'], unique=False)
    op.add_column('operations', sa.Column('plan_executor_id', sa.BigInteger(), nullable=True))
    op.drop_index(op.f('uq_operations_plan_cell'), table_name='operations', postgresql_where='(budget_version_id IS NOT NULL)', postgresql_nulls_not_distinct=True)
    op.create_index('uq_operations_plan_cell', 'operations', ['budget_version_id', 'plan_kind', 'plan_cfo_id', 'plan_executor_id', 'article_id', 'op_date'], unique=True, postgresql_nulls_not_distinct=True, postgresql_where=sa.text('budget_version_id IS NOT NULL'))
    op.create_foreign_key(op.f('fk_operations_plan_executor_id_executors'), 'operations', 'executors', ['plan_executor_id'], ['id'], ondelete='RESTRICT')
    op.add_column('postings', sa.Column('dt_executor_id', sa.BigInteger(), nullable=True))
    op.add_column('postings', sa.Column('ct_executor_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_postings_ct_executor_id_executors'), 'postings', 'executors', ['ct_executor_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_postings_dt_executor_id_executors'), 'postings', 'executors', ['dt_executor_id'], ['id'], ondelete='RESTRICT')
    op.drop_constraint(op.f("ck_account_analytics_kind"), "account_analytics", type_="check")
    op.create_check_constraint("kind", "account_analytics", KINDS_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM account_analytics WHERE kind = 'executor'")
    op.drop_constraint(op.f("ck_account_analytics_kind"), "account_analytics", type_="check")
    op.create_check_constraint("kind", "account_analytics", KINDS_OLD)
    op.drop_constraint(op.f('fk_postings_dt_executor_id_executors'), 'postings', type_='foreignkey')
    op.drop_constraint(op.f('fk_postings_ct_executor_id_executors'), 'postings', type_='foreignkey')
    op.drop_column('postings', 'ct_executor_id')
    op.drop_column('postings', 'dt_executor_id')
    op.drop_constraint(op.f('fk_operations_plan_executor_id_executors'), 'operations', type_='foreignkey')
    op.drop_index('uq_operations_plan_cell', table_name='operations', postgresql_nulls_not_distinct=True, postgresql_where=sa.text('budget_version_id IS NOT NULL'))
    op.create_index(op.f('uq_operations_plan_cell'), 'operations', ['budget_version_id', 'plan_kind', 'plan_cfo_id', 'article_id', 'op_date'], unique=True, postgresql_where='(budget_version_id IS NOT NULL)', postgresql_nulls_not_distinct=True)
    op.drop_column('operations', 'plan_executor_id')
    op.drop_index(op.f('ix_executors_parent_id'), table_name='executors')
    op.drop_index(op.f('ix_executors_organization_id'), table_name='executors')
    op.drop_table('executors')
