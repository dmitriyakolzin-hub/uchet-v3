"""Нормативы: вид «Нормативный» (сумма в месяц к окладам)

docs/TZ.md, раздел 32, «Детали 24.2».

Revision ID: 0069
Revises: 0068
Create Date: 2026-11-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0069'
down_revision: str | None = '0068'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint('kind', 'plan_norms', type_='check')
    op.drop_constraint('fact_fields', 'plan_norms', type_='check')
    op.create_check_constraint('kind', 'plan_norms', "kind IN ('calc', 'fact', 'norm')")
    op.create_check_constraint('fact_fields', 'plan_norms', "kind NOT IN ('fact', 'norm') OR amount IS NOT NULL")


def downgrade() -> None:
    op.execute("DELETE FROM plan_norms WHERE kind = 'norm'")
    op.drop_constraint('kind', 'plan_norms', type_='check')
    op.drop_constraint('fact_fields', 'plan_norms', type_='check')
    op.create_check_constraint('kind', 'plan_norms', "kind IN ('calc', 'fact')")
    op.create_check_constraint('fact_fields', 'plan_norms', "kind <> 'fact' OR amount IS NOT NULL")
