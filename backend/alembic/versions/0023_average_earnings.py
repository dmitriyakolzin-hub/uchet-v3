"""Начисление зарплаты: расчёт отпускных и больничных по среднему заработку (пояснения в строке)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('payroll_lines', sa.Column('vacation_note', sa.String(length=1000), server_default='', nullable=False))
    op.add_column('payroll_lines', sa.Column('sick_note', sa.String(length=1000), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('payroll_lines', 'sick_note')
    op.drop_column('payroll_lines', 'vacation_note')
