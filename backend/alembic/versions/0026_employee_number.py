"""Сотрудник: второй номер — внутренний (не повторяется в организации, если указан)

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('employees', sa.Column('internal_number', sa.String(length=50), server_default='', nullable=False))
    op.create_index('uq_employees_organization_internal_number', 'employees', ['organization_id', 'internal_number'], unique=True, postgresql_where=sa.text("internal_number <> ''"))


def downgrade() -> None:
    op.drop_index('uq_employees_organization_internal_number', table_name='employees', postgresql_where=sa.text("internal_number <> ''"))
    op.drop_column('employees', 'internal_number')
