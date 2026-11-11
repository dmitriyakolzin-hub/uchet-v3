"""Периодические платежи без договора: юрлицо, контрагент, название, периодичность

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contract_payments", sa.Column("organization_id", sa.BigInteger(), nullable=True))
    op.add_column("contract_payments", sa.Column("legal_entity_id", sa.BigInteger(), nullable=True))
    op.add_column("contract_payments", sa.Column("counterparty_id", sa.BigInteger(), nullable=True))
    op.add_column("contract_payments", sa.Column("name", sa.String(length=255), server_default="", nullable=False))
    op.add_column("contract_payments", sa.Column("frequency", sa.String(length=10), server_default="month", nullable=False))
    op.execute("""
        UPDATE contract_payments p SET organization_id = c.organization_id, legal_entity_id = c.legal_entity_id,
               counterparty_id = c.counterparty_id
        FROM contracts c WHERE c.id = p.contract_id
    """)
    op.alter_column("contract_payments", "organization_id", nullable=False)
    op.alter_column("contract_payments", "legal_entity_id", nullable=False)
    op.alter_column("contract_payments", "contract_id", nullable=True)
    op.create_index(op.f("ix_contract_payments_organization_id"), "contract_payments", ["organization_id"])
    op.create_foreign_key(op.f("fk_contract_payments_organization_id_organizations"), "contract_payments",
                          "organizations", ["organization_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(op.f("fk_contract_payments_legal_entity_id_legal_entities"), "contract_payments",
                          "legal_entities", ["legal_entity_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_contract_payments_counterparty_id_counterparties"), "contract_payments",
                          "counterparties", ["counterparty_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint(op.f("ck_contract_payments_frequency"), "contract_payments",
                               "frequency IN ('month', 'quarter', 'year')")


def downgrade() -> None:
    op.execute("DELETE FROM contract_payments WHERE contract_id IS NULL")
    op.drop_constraint(op.f("ck_contract_payments_frequency"), "contract_payments", type_="check")
    op.drop_constraint(op.f("fk_contract_payments_counterparty_id_counterparties"), "contract_payments", type_="foreignkey")
    op.drop_constraint(op.f("fk_contract_payments_legal_entity_id_legal_entities"), "contract_payments", type_="foreignkey")
    op.drop_constraint(op.f("fk_contract_payments_organization_id_organizations"), "contract_payments", type_="foreignkey")
    op.drop_index(op.f("ix_contract_payments_organization_id"), table_name="contract_payments")
    op.alter_column("contract_payments", "contract_id", nullable=False)
    op.drop_column("contract_payments", "frequency")
    op.drop_column("contract_payments", "name")
    op.drop_column("contract_payments", "counterparty_id")
    op.drop_column("contract_payments", "legal_entity_id")
    op.drop_column("contract_payments", "organization_id")
