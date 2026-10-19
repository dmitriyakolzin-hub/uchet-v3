"""Счёт 99 «Прибыли и убытки»: обязательная аналитика «Статья ОПиУ» (налог на прибыль в ОПиУ)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ОПиУ строится по аналитике «Статья ОПиУ»; без неё начисление налога (Дт 99 — Кт 68.04) не видно
    op.execute(
        """
        INSERT INTO account_analytics (account_id, kind, position)
        SELECT a.id, 'pl_item', COALESCE((SELECT max(position) FROM account_analytics WHERE account_id = a.id), 0) + 1
        FROM accounts a
        WHERE a.code = '99'
          AND NOT EXISTS (SELECT 1 FROM account_analytics x WHERE x.account_id = a.id AND x.kind = 'pl_item')
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM account_analytics WHERE kind = 'pl_item' AND account_id = (SELECT id FROM accounts WHERE code = '99')"
    )
