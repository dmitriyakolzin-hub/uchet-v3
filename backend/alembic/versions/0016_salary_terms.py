"""Условия оплаты сотрудников: работодатель, статья затрат, история окладов

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('employee_salaries',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('note', sa.String(length=255), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount >= 0', name=op.f('ck_employee_salaries_amount')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_salaries_employee_id_employees'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_salaries')),
    sa.UniqueConstraint('employee_id', 'valid_from', name='uq_employee_salaries_employee_date')
    )
    op.create_index(op.f('ix_employee_salaries_employee_id'), 'employee_salaries', ['employee_id'], unique=False)
    op.add_column('employees', sa.Column('legal_entity_id', sa.BigInteger(), nullable=True))
    op.add_column('employees', sa.Column('pl_item_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_employees_legal_entity_id_legal_entities'), 'employees', 'legal_entities', ['legal_entity_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_employees_pl_item_id_articles'), 'employees', 'articles', ['pl_item_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_employees_pl_item_id_articles'), 'employees', type_='foreignkey')
    op.drop_constraint(op.f('fk_employees_legal_entity_id_legal_entities'), 'employees', type_='foreignkey')
    op.drop_column('employees', 'pl_item_id')
    op.drop_column('employees', 'legal_entity_id')
    op.drop_index(op.f('ix_employee_salaries_employee_id'), table_name='employee_salaries')
    op.drop_table('employee_salaries')
