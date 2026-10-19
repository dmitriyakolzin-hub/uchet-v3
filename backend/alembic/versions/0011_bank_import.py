"""Ключ платежа из банковской выписки у операции (защита от повторной загрузки)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('operations', sa.Column('import_key', sa.String(length=300), nullable=True))
    op.create_index('uq_operations_organization_import_key', 'operations', ['organization_id', 'import_key'], unique=True, postgresql_where=sa.text('import_key IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('uq_operations_organization_import_key', table_name='operations', postgresql_where=sa.text('import_key IS NOT NULL'))
    op.drop_column('operations', 'import_key')
