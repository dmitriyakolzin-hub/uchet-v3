"""Методы планирования статей и нормативы

docs/TZ.md, раздел 31, «Детали 23.1».

Revision ID: 0067
Revises: 0066
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0067'
down_revision: str | None = '0066'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('plan_norms',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('article_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('base_article_id', sa.BigInteger(), nullable=True),
    sa.Column('base_form_code', sa.String(length=30), nullable=True),
    sa.Column('percent', sa.Numeric(precision=9, scale=4), nullable=True),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('comment', sa.String(length=500), server_default='', nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind <> 'calc' OR (percent IS NOT NULL AND (base_article_id IS NOT NULL OR base_form_code IS NOT NULL))", name=op.f('ck_plan_norms_calc_fields')),
    sa.CheckConstraint("kind <> 'fact' OR amount IS NOT NULL", name=op.f('ck_plan_norms_fact_fields')),
    sa.CheckConstraint("kind IN ('calc', 'fact')", name=op.f('ck_plan_norms_kind')),
    sa.CheckConstraint('EXTRACT(DAY FROM valid_from) = 1', name=op.f('ck_plan_norms_valid_from_month')),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_plan_norms_article_id_articles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['base_article_id'], ['articles.id'], name=op.f('fk_plan_norms_base_article_id_articles'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_plan_norms_cfo_id_cfos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_plan_norms_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_plan_norms_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_norms'))
    )
    op.create_index(op.f('ix_plan_norms_article_id'), 'plan_norms', ['article_id'], unique=False)
    op.create_index(op.f('ix_plan_norms_organization_id'), 'plan_norms', ['organization_id'], unique=False)
    op.create_index('uq_plan_norms_article_kind_cfo_month', 'plan_norms', ['article_id', 'kind', 'cfo_id', 'valid_from'], unique=True, postgresql_nulls_not_distinct=True)
    op.add_column('articles', sa.Column('plan_method', sa.String(length=10), nullable=True))
    op.add_column('articles', sa.Column('plan_avg_months', sa.Integer(), nullable=True))
    op.create_check_constraint('plan_method', 'articles',
                               "plan_method IN ('norm', 'avg', 'fact', 'calc', 'cfo', 'executor', 'top')")
    op.create_check_constraint('plan_avg_months', 'articles', 'plan_avg_months IN (1, 3, 6, 12)')


def downgrade() -> None:
    op.drop_constraint('plan_avg_months', 'articles', type_='check')
    op.drop_constraint('plan_method', 'articles', type_='check')
    op.drop_column('articles', 'plan_avg_months')
    op.drop_column('articles', 'plan_method')
    op.drop_index('uq_plan_norms_article_kind_cfo_month', table_name='plan_norms', postgresql_nulls_not_distinct=True)
    op.drop_index(op.f('ix_plan_norms_organization_id'), table_name='plan_norms')
    op.drop_index(op.f('ix_plan_norms_article_id'), table_name='plan_norms')
    op.drop_table('plan_norms')
