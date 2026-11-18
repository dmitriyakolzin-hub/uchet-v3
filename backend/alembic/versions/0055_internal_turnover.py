"""Бюджет ЦФО: раздел ОПиУ «Внутренние обороты» и статьи «Внутренняя выручка» / «Внутренняя закупка»

docs/TZ.md, раздел 29, «Детали 20.1».

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0055'
down_revision: str | None = '0054'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "pl_section IN ('revenue', 'cost', 'selling', 'admin', 'other_income', 'other_expense', 'income_tax')"
NEW = ("pl_section IN ('revenue', 'internal', 'cost', 'selling', 'admin', 'other_income', 'other_expense', "
       "'income_tax')")
ARTICLES = (("Внутренняя выручка", "internal_revenue"), ("Внутренняя закупка", "internal_purchase"))


def upgrade() -> None:
    op.drop_constraint('pl_section', 'articles', type_='check')
    op.create_check_constraint('pl_section', 'articles', NEW)
    conn = op.get_bind()
    accounts = dict(conn.execute(sa.text("SELECT code, id FROM accounts WHERE code IN ('90.01', '90.02')")).all())
    for name, key in ARTICLES:
        # каждой организации — две системные статьи (имя среди статей верхнего уровня уникально)
        conn.execute(sa.text(
            "INSERT INTO articles (organization_id, name, sort_order, system_key, is_cash_flow, is_pl, pl_section,"
            " debit_account_id, credit_account_id, is_transfer, is_active)"
            " SELECT o.id, CAST(:name AS varchar), COALESCE((SELECT max(sort_order) FROM articles a WHERE a.organization_id = o.id), 0) + 1,"
            " CAST(:key AS varchar), false, true, 'internal', :debit, :credit, false, true FROM organizations o"
            " WHERE NOT EXISTS (SELECT 1 FROM articles a WHERE a.organization_id = o.id"
            "   AND (a.system_key = CAST(:key AS varchar) OR (a.parent_id IS NULL AND a.name = CAST(:name AS varchar))))"),
            {"name": name, "key": key, "debit": accounts.get("90.02"), "credit": accounts.get("90.01")})


def downgrade() -> None:
    op.execute("DELETE FROM articles WHERE pl_section = 'internal'")
    op.drop_constraint('pl_section', 'articles', type_='check')
    op.create_check_constraint('pl_section', 'articles', OLD)
