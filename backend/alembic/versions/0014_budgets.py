"""Бюджеты (год / квартал / месяц) и их версии: черновик → утверждена → архив

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('budgets',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('period', sa.SmallInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind = 'year' AND period = 1) OR (kind = 'quarter' AND period BETWEEN 1 AND 4) OR (kind = 'month' AND period BETWEEN 1 AND 12)", name=op.f('ck_budgets_period')),
    sa.CheckConstraint("kind IN ('year', 'quarter', 'month')", name=op.f('ck_budgets_kind')),
    sa.CheckConstraint('year BETWEEN 2000 AND 2100', name=op.f('ck_budgets_year')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_budgets_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_budgets')),
    sa.UniqueConstraint('organization_id', 'kind', 'year', 'period', name='uq_budgets_organization_period')
    )
    op.create_index(op.f('ix_budgets_organization_id'), 'budgets', ['organization_id'], unique=False)
    op.create_table('budget_versions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('budget_id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.SmallInteger(), nullable=False),
    sa.Column('name', sa.String(length=255), server_default='', nullable=False),
    sa.Column('status', sa.String(length=10), server_default='draft', nullable=False),
    sa.Column('based_on_id', sa.BigInteger(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('approved_by', sa.BigInteger(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('draft', 'approved', 'archived')", name=op.f('ck_budget_versions_status')),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_budget_versions_approved_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['based_on_id'], ['budget_versions.id'], name=op.f('fk_budget_versions_based_on_id_budget_versions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['budget_id'], ['budgets.id'], name=op.f('fk_budget_versions_budget_id_budgets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_budget_versions_created_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_budget_versions')),
    sa.UniqueConstraint('budget_id', 'number', name='uq_budget_versions_budget_number')
    )
    op.create_index(op.f('ix_budget_versions_budget_id'), 'budget_versions', ['budget_id'], unique=False)
    op.create_index('uq_budget_versions_one_approved', 'budget_versions', ['budget_id'], unique=True, postgresql_where=sa.text("status = 'approved'"))


def downgrade() -> None:
    op.drop_index('uq_budget_versions_one_approved', table_name='budget_versions', postgresql_where=sa.text("status = 'approved'"))
    op.drop_index(op.f('ix_budget_versions_budget_id'), table_name='budget_versions')
    op.drop_table('budget_versions')
    op.drop_index(op.f('ix_budgets_organization_id'), table_name='budgets')
    op.drop_table('budgets')
