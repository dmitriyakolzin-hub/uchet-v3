"""Роль «Сотрудник»: видит и правит только то, что завёл сам

docs/TZ.md, раздел 30, «Детали 22.2».

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0064'
down_revision: str | None = '0063'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = ("role IN ('finance_director', 'finance_manager', 'ceo', 'cfo_head', 'executor_head', "
             "'accountant', 'engineer', 'aho_operator', 'mol')")
NEW_ROLES = ("role IN ('finance_director', 'finance_manager', 'ceo', 'cfo_head', 'executor_head', "
             "'accountant', 'engineer', 'aho_operator', 'mol', 'employee')")


def upgrade() -> None:
    op.drop_constraint('role', 'memberships', type_='check')
    op.create_check_constraint('role', 'memberships', NEW_ROLES)


def downgrade() -> None:
    op.execute("DELETE FROM memberships WHERE role = 'employee'")
    op.drop_constraint('role', 'memberships', type_='check')
    op.create_check_constraint('role', 'memberships', OLD_ROLES)
