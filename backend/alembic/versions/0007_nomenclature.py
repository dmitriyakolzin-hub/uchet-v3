"""Единицы измерения (с начальным списком по ОКЕИ), номенклатура, склады

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Часто используемые единицы по ОКЕИ: (код, краткое, полное)
UNITS = [
    ("796", "шт", "Штука"),
    ("166", "кг", "Килограмм"),
    ("163", "г", "Грамм"),
    ("168", "т", "Тонна"),
    ("112", "л", "Литр"),
    ("006", "м", "Метр"),
    ("055", "м²", "Квадратный метр"),
    ("113", "м³", "Кубический метр"),
    ("778", "упак", "Упаковка"),
    ("839", "компл", "Комплект"),
    ("715", "пар", "Пара"),
    ("356", "ч", "Час"),
    ("359", "сут", "Сутки"),
    ("362", "мес", "Месяц"),
    ("876", "усл. ед", "Условная единица"),
]


def upgrade() -> None:
    op.create_table('units',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('okei', sa.String(length=4), nullable=False),
    sa.Column('short_name', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("okei ~ '^[0-9]{3,4}$'", name=op.f('ck_units_okei')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_units')),
    sa.UniqueConstraint('okei', name=op.f('uq_units_okei'))
    )
    op.create_table('nomenclature',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('article', sa.String(length=100), server_default='', nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("kind IN ('goods', 'material', 'product', 'service')", name=op.f('ck_nomenclature_kind')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_nomenclature_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_nomenclature_unit_id_units'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_nomenclature'))
    )
    op.create_index(op.f('ix_nomenclature_organization_id'), 'nomenclature', ['organization_id'], unique=False)
    op.create_index('uq_nomenclature_organization_article', 'nomenclature', ['organization_id', 'article'], unique=True, postgresql_where=sa.text("article <> ''"))
    op.create_table('warehouses',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('cfo_id', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['cfo_id'], ['cfos.id'], name=op.f('fk_warehouses_cfo_id_cfos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_warehouses_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_warehouses'))
    )
    op.create_index(op.f('ix_warehouses_organization_id'), 'warehouses', ['organization_id'], unique=False)

    units = sa.table("units", sa.column("okei"), sa.column("short_name"), sa.column("name"))
    op.bulk_insert(units, [{"okei": o, "short_name": sh, "name": n} for o, sh, n in UNITS])

def downgrade() -> None:
    op.drop_index(op.f('ix_warehouses_organization_id'), table_name='warehouses')
    op.drop_table('warehouses')
    op.drop_index('uq_nomenclature_organization_article', table_name='nomenclature', postgresql_where=sa.text("article <> ''"))
    op.drop_index(op.f('ix_nomenclature_organization_id'), table_name='nomenclature')
    op.drop_table('nomenclature')
    op.drop_table('units')
