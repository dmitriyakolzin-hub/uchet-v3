"""«Сверху ИТОГ»: утверждённые цифры статей и история

docs/TZ.md, раздел 31, «Детали 23.4».

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0068'
down_revision: str | None = '0067'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('plan_itog_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('article_id', sa.BigInteger(), nullable=True),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('action', sa.String(length=10), nullable=False),
    sa.Column('old_amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('new_amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('details', sa.String(length=500), server_default='', nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('edit', 'reset', 'fix')", name=op.f('ck_plan_itog_events_action')),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_plan_itog_events_article_id_articles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_plan_itog_events_cfo_id_cfos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_plan_itog_events_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['version_id'], ['budget_versions.id'], name=op.f('fk_plan_itog_events_version_id_budget_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_itog_events'))
    )
    op.create_index(op.f('ix_plan_itog_events_version_id'), 'plan_itog_events', ['version_id'], unique=False)
    op.create_table('plan_itogs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('article_id', sa.BigInteger(), nullable=False),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('manual', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('fixed_by', sa.BigInteger(), nullable=True),
    sa.Column('fixed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_plan_itogs_month')),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_plan_itogs_article_id_articles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_plan_itogs_cfo_id_cfos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['fixed_by'], ['users.id'], name=op.f('fk_plan_itogs_fixed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_plan_itogs_updated_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['version_id'], ['budget_versions.id'], name=op.f('fk_plan_itogs_version_id_budget_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_itogs'))
    )
    op.create_index(op.f('ix_plan_itogs_version_id'), 'plan_itogs', ['version_id'], unique=False)
    op.create_index('uq_plan_itogs_cell', 'plan_itogs', ['version_id', 'cfo_id', 'article_id', 'month'], unique=True, postgresql_nulls_not_distinct=True)


def downgrade() -> None:
    op.drop_index('uq_plan_itogs_cell', table_name='plan_itogs', postgresql_nulls_not_distinct=True)
    op.drop_index(op.f('ix_plan_itogs_version_id'), table_name='plan_itogs')
    op.drop_table('plan_itogs')
    op.drop_index(op.f('ix_plan_itog_events_version_id'), table_name='plan_itog_events')
    op.drop_table('plan_itog_events')
