"""ЦФО (дерево из 5 уровней на ltree) и сотрудники

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None



class Ltree(sa.types.UserDefinedType):
    """Тип ltree (миграция не зависит от кода приложения)."""

    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return "LTREE"


def upgrade() -> None:
    op.create_table('cfos',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('path', Ltree(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('(level = 1) = (parent_id IS NULL)', name=op.f('ck_cfos_root_has_no_parent')),
    sa.CheckConstraint('level = 1 OR legal_entity_id IS NULL', name=op.f('ck_cfos_legal_entity_only_for_company')),
    sa.CheckConstraint('level BETWEEN 1 AND 5', name=op.f('ck_cfos_level')),
    sa.CheckConstraint('nlevel(path) = level', name=op.f('ck_cfos_path_depth')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_cfos_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_cfos_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_id'], ['cfos.id'], name=op.f('fk_cfos_parent_id_cfos'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cfos'))
    )
    op.create_index(op.f('ix_cfos_organization_id'), 'cfos', ['organization_id'], unique=False)
    op.create_index(op.f('ix_cfos_parent_id'), 'cfos', ['parent_id'], unique=False)
    op.create_index('ix_cfos_path', 'cfos', ['path'], unique=False, postgresql_using='gist')
    op.create_table('employees',
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('personnel_number', sa.String(length=50), server_default='', nullable=False),
    sa.Column('inn', sa.String(length=12), nullable=True),
    sa.Column('position', sa.String(length=255), server_default='', nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('hired_on', sa.Date(), nullable=True),
    sa.Column('fired_on', sa.Date(), nullable=True),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("inn IS NULL OR inn ~ '^[0-9]{12}$'", name=op.f('ck_employees_inn')),
    sa.CheckConstraint('fired_on IS NULL OR hired_on IS NULL OR fired_on >= hired_on', name=op.f('ck_employees_dates')),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_employees_cfo_id_cfos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employees_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employees'))
    )
    op.create_index(op.f('ix_employees_cfo_id'), 'employees', ['cfo_id'], unique=False)
    op.create_index(op.f('ix_employees_organization_id'), 'employees', ['organization_id'], unique=False)
    op.create_index('uq_employees_organization_personnel_number', 'employees', ['organization_id', 'personnel_number'], unique=True, postgresql_where=sa.text("personnel_number <> ''"))


def downgrade() -> None:
    op.drop_index('uq_employees_organization_personnel_number', table_name='employees', postgresql_where=sa.text("personnel_number <> ''"))
    op.drop_index(op.f('ix_employees_organization_id'), table_name='employees')
    op.drop_index(op.f('ix_employees_cfo_id'), table_name='employees')
    op.drop_table('employees')
    op.drop_index('ix_cfos_path', table_name='cfos', postgresql_using='gist')
    op.drop_index(op.f('ix_cfos_parent_id'), table_name='cfos')
    op.drop_index(op.f('ix_cfos_organization_id'), table_name='cfos')
    op.drop_table('cfos')
