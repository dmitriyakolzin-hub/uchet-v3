"""Организации, юрлица, роли пользователей

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('group', 'client')", name=op.f('ck_organizations_kind')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('name', name=op.f('uq_organizations_name'))
    )
    op.create_table('legal_entities',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('full_name', sa.String(length=500), nullable=False),
    sa.Column('short_name', sa.String(length=255), nullable=False),
    sa.Column('inn', sa.String(length=12), nullable=False),
    sa.Column('kpp', sa.String(length=9), nullable=True),
    sa.Column('ogrn', sa.String(length=15), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("inn ~ '^([0-9]{10}|[0-9]{12})$'", name=op.f('ck_legal_entities_inn')),
    sa.CheckConstraint("kpp IS NULL OR kpp ~ '^[0-9]{9}$'", name=op.f('ck_legal_entities_kpp')),
    sa.CheckConstraint("ogrn IS NULL OR ogrn ~ '^([0-9]{13}|[0-9]{15})$'", name=op.f('ck_legal_entities_ogrn')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_legal_entities_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_legal_entities')),
    sa.UniqueConstraint('organization_id', 'inn', name='uq_legal_entities_organization_inn')
    )
    op.create_index(op.f('ix_legal_entities_organization_id'), 'legal_entities', ['organization_id'], unique=False)
    op.create_table('memberships',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.String(length=30), nullable=False),
    sa.CheckConstraint("role IN ('finance_director', 'ceo', 'cfo_head')", name=op.f('ck_memberships_role')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_memberships_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'organization_id', name=op.f('pk_memberships'))
    )
    op.create_index(op.f('ix_memberships_organization_id'), 'memberships', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_memberships_organization_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_legal_entities_organization_id'), table_name='legal_entities')
    op.drop_table('legal_entities')
    op.drop_table('organizations')
