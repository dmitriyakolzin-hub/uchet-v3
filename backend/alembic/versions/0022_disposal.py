"""Выбытие основных средств: дата, вид и операция в карточке; статьи продажи и списания ОС

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (название, раздел ОПиУ, Дт, Кт)
ARTICLES = [
    ("Доход от продажи основных средств", "other_income", "62.01", "91.01"),
    ("Списание основных средств", "other_expense", "91.02", "01"),
]


def upgrade() -> None:
    op.add_column('fixed_assets', sa.Column('disposed_on', sa.Date(), nullable=True))
    op.add_column('fixed_assets', sa.Column('disposal_kind', sa.String(length=10), nullable=True))
    op.add_column('fixed_assets', sa.Column('disposal_operation_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_fixed_assets_disposal_operation_id_operations'), 'fixed_assets', 'operations', ['disposal_operation_id'], ['id'], ondelete='SET NULL')
    op.create_check_constraint("disposal_kind", "fixed_assets", "disposal_kind IS NULL OR disposal_kind IN ('sale', 'writeoff')")
    op.create_check_constraint("disposal_fields", "fixed_assets", "(disposed_on IS NULL) = (disposal_kind IS NULL)")
    conn = op.get_bind()
    for name, section, debit, credit in ARTICLES:
        conn.execute(sa.text(
            "INSERT INTO articles (name, is_pl, pl_section, debit_account_id, credit_account_id) "
            "VALUES (:name, true, :section, (SELECT id FROM accounts WHERE code = :dt), "
            "(SELECT id FROM accounts WHERE code = :ct)) ON CONFLICT (name) DO NOTHING"),
            {"name": name, "section": section, "dt": debit, "ct": credit})


def downgrade() -> None:
    op.drop_constraint(op.f("ck_fixed_assets_disposal_fields"), "fixed_assets", type_="check")
    op.drop_constraint(op.f("ck_fixed_assets_disposal_kind"), "fixed_assets", type_="check")
    op.drop_constraint(op.f('fk_fixed_assets_disposal_operation_id_operations'), 'fixed_assets', type_='foreignkey')
    op.drop_column('fixed_assets', 'disposal_operation_id')
    op.drop_column('fixed_assets', 'disposal_kind')
    op.drop_column('fixed_assets', 'disposed_on')
