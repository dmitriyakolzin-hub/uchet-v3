"""«Сверху ИТОГ»: откуда взята сумма (колонка метода или своя)

docs/TZ.md, раздел 32, «Детали 24.4».

Revision ID: 0070
Revises: 0069
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0070'
down_revision: str | None = '0069'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('plan_itogs', sa.Column('source', sa.String(length=10), nullable=True))
    op.execute("UPDATE plan_itogs SET source = 'manual' WHERE manual")


def downgrade() -> None:
    op.drop_column('plan_itogs', 'source')
