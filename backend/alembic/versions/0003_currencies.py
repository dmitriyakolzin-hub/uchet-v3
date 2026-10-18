"""Валюты и курсы; базовая валюта RUB

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('currencies',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('code', sa.String(length=3), nullable=False),
    sa.Column('numeric_code', sa.String(length=3), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("code ~ '^[A-Z]{3}$'", name=op.f('ck_currencies_code')),
    sa.CheckConstraint("numeric_code ~ '^[0-9]{3}$'", name=op.f('ck_currencies_numeric_code')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_currencies')),
    sa.UniqueConstraint('code', name=op.f('uq_currencies_code')),
    sa.UniqueConstraint('numeric_code', name=op.f('uq_currencies_numeric_code'))
    )
    op.create_table('exchange_rates',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('currency_id', sa.BigInteger(), nullable=False),
    sa.Column('rate_date', sa.Date(), nullable=False),
    sa.Column('rate', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('nominal', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('nominal > 0', name=op.f('ck_exchange_rates_nominal_positive')),
    sa.CheckConstraint('rate > 0', name=op.f('ck_exchange_rates_rate_positive')),
    sa.ForeignKeyConstraint(['currency_id'], ['currencies.id'], name=op.f('fk_exchange_rates_currency_id_currencies'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exchange_rates')),
    sa.UniqueConstraint('currency_id', 'rate_date', name='uq_exchange_rates_currency_date')
    )
    op.create_index(op.f('ix_exchange_rates_currency_id'), 'exchange_rates', ['currency_id'], unique=False)

    # Базовая валюта — рубль, курс у него всегда 1
    op.execute(
        "INSERT INTO currencies (code, numeric_code, name) VALUES ('RUB', '643', 'Российский рубль')"
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_exchange_rates_currency_id'), table_name='exchange_rates')
    op.drop_table('exchange_rates')
    op.drop_table('currencies')
