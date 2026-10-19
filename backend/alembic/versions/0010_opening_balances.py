"""Начальные остатки (дата у организации, счёт 00) и перевод между своими счетами

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('articles', sa.Column('is_transfer', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('operations', sa.Column('is_opening', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('organizations', sa.Column('opening_balance_date', sa.Date(), nullable=True))

    # Вспомогательный счёт для ввода начальных остатков (как «000» в 1С)
    op.execute(
        "INSERT INTO accounts (code, name, account_type) VALUES ('00', 'Ввод остатков', 'active_passive')"
    )
    # Статья для переводов между своими счетами: Дт 51 — Кт 51
    op.execute(
        "INSERT INTO articles (name, is_cash_flow, cash_flow_activity, cash_flow_direction, is_transfer, "
        "debit_account_id, credit_account_id) VALUES ('Перевод между своими счетами', true, 'operating', "
        "'outflow', true, (SELECT id FROM accounts WHERE code = '51'), (SELECT id FROM accounts WHERE code = '51'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM articles WHERE name = 'Перевод между своими счетами'")
    op.execute("DELETE FROM accounts WHERE code = '00'")
    op.drop_column('organizations', 'opening_balance_date')
    op.drop_column('operations', 'is_opening')
    op.drop_column('articles', 'is_transfer')
