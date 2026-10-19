"""Начисление зарплаты: строки с готовыми суммами из файла (не пересчитываются)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('payroll_lines', sa.Column('manual_totals', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('payroll_lines', 'manual_totals')
