"""Нормативы «на каждую точку торговли»

docs/TZ.md, раздел 32, «Детали 24.6».

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0071'
down_revision: str | None = '0070'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('plan_norms', sa.Column('per_point', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('plan_norms', 'per_point')
