"""Статьи ДДС и ОПиУ с шаблонами проводок; стартовый набор статей

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Стартовый набор статей (docs/TZ.md, раздел 8):
# (наименование, (вид деятельности, направление) или None, раздел ОПиУ или None, Дт, Кт)
OP, INV, FIN = "operating", "investing", "financing"
IN, OUT = "inflow", "outflow"
ARTICLES = [
    # ДДС — операционная деятельность
    ("Поступление от покупателей", (OP, IN), None, "51", "62.01"),
    ("Авансы от покупателей", (OP, IN), None, "51", "62.02"),
    ("Возврат средств от поставщиков", (OP, IN), None, "51", "60.01"),
    ("Прочие поступления", (OP, IN), None, "51", "76"),
    ("Оплата поставщикам", (OP, OUT), None, "60.01", "51"),
    ("Авансы поставщикам", (OP, OUT), None, "60.02", "51"),
    ("Возврат средств покупателям", (OP, OUT), None, "62.01", "51"),
    ("Выплата заработной платы", (OP, OUT), None, "70", "51"),
    ("Уплата НДФЛ", (OP, OUT), None, "68.01", "51"),
    ("Уплата страховых взносов", (OP, OUT), None, "69", "51"),
    ("Уплата НДС", (OP, OUT), None, "68.02", "51"),
    ("Уплата налога на прибыль", (OP, OUT), None, "68.04", "51"),
    ("Уплата прочих налогов", (OP, OUT), None, "68.10", "51"),
    ("Выдача под отчёт", (OP, OUT), None, "71", "51"),
    ("Банковские комиссии", (OP, OUT), "other_expense", "91.02", "51"),
    ("Прочие выплаты", (OP, OUT), None, "76", "51"),
    # ДДС — инвестиционная деятельность
    ("Покупка основных средств", (INV, OUT), None, "60.01", "51"),
    ("Продажа основных средств", (INV, IN), None, "51", "62.01"),
    ("Выдача займов", (INV, OUT), None, "58", "51"),
    ("Возврат выданных займов", (INV, IN), None, "51", "58"),
    # ДДС — финансовая деятельность
    ("Получение кредитов и займов", (FIN, IN), None, "51", "66"),
    ("Погашение кредитов и займов", (FIN, OUT), None, "66", "51"),
    ("Уплата процентов по кредитам и займам", (FIN, OUT), None, "66", "51"),
    ("Вклады учредителей", (FIN, IN), None, "51", "75"),
    ("Выплата дивидендов", (FIN, OUT), None, "75", "51"),
    # ОПиУ
    ("Выручка от продаж", None, "revenue", "62.01", "90.01"),
    ("Себестоимость продаж", None, "cost", "90.02", "41"),
    ("Оплата труда — коммерческий персонал", None, "selling", "44", "70"),
    ("Оплата труда — управленческий персонал", None, "admin", "26", "70"),
    ("Страховые взносы с оплаты труда", None, "admin", "26", "69"),
    ("Аренда", None, "selling", "44", "60.01"),
    ("Коммунальные услуги", None, "selling", "44", "60.01"),
    ("Реклама и маркетинг", None, "selling", "44", "60.01"),
    ("Связь и IT", None, "admin", "26", "60.01"),
    ("Прочие управленческие расходы", None, "admin", "26", "60.01"),
    ("Проценты по кредитам и займам", None, "other_expense", "91.02", "66"),
    ("Прочие доходы", None, "other_income", "76", "91.01"),
    ("Прочие расходы", None, "other_expense", "91.02", "76"),
    ("Налог на прибыль", None, "income_tax", "99", "68.04"),
]


def upgrade() -> None:
    op.create_table('articles',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('is_cash_flow', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('cash_flow_activity', sa.String(length=20), nullable=True),
    sa.Column('cash_flow_direction', sa.String(length=20), nullable=True),
    sa.Column('is_pl', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('pl_section', sa.String(length=20), nullable=True),
    sa.Column('debit_account_id', sa.BigInteger(), nullable=True),
    sa.Column('credit_account_id', sa.BigInteger(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("cash_flow_activity IN ('operating', 'investing', 'financing')", name=op.f('ck_articles_cash_flow_activity')),
    sa.CheckConstraint("cash_flow_direction IN ('inflow', 'outflow')", name=op.f('ck_articles_cash_flow_direction')),
    sa.CheckConstraint("pl_section IN ('revenue', 'cost', 'selling', 'admin', 'other_income', 'other_expense', 'income_tax')", name=op.f('ck_articles_pl_section')),
    sa.CheckConstraint('is_cash_flow = (cash_flow_activity IS NOT NULL AND cash_flow_direction IS NOT NULL)', name=op.f('ck_articles_cash_flow_fields')),
    sa.CheckConstraint('is_cash_flow OR is_pl', name=op.f('ck_articles_cash_flow_or_pl')),
    sa.CheckConstraint('is_pl = (pl_section IS NOT NULL)', name=op.f('ck_articles_pl_fields')),
    sa.ForeignKeyConstraint(['credit_account_id'], ['accounts.id'], name=op.f('fk_articles_credit_account_id_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['debit_account_id'], ['accounts.id'], name=op.f('fk_articles_debit_account_id_accounts'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_articles')),
    sa.UniqueConstraint('name', name=op.f('uq_articles_name'))
    )

    conn = op.get_bind()
    for name, cash_flow, section, debit, credit in ARTICLES:
        conn.execute(
            sa.text(
                "INSERT INTO articles (name, is_cash_flow, cash_flow_activity, cash_flow_direction, "
                "is_pl, pl_section, debit_account_id, credit_account_id) VALUES (:name, :cf, :act, :dir, "
                ":pl, :section, (SELECT id FROM accounts WHERE code = :dt), "
                "(SELECT id FROM accounts WHERE code = :kt))"
            ),
            {"name": name, "cf": cash_flow is not None, "act": cash_flow[0] if cash_flow else None,
             "dir": cash_flow[1] if cash_flow else None, "pl": section is not None, "section": section,
             "dt": debit, "kt": credit},
        )

def downgrade() -> None:
    op.drop_table('articles')
