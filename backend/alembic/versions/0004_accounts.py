"""План счетов и обязательная аналитика; стартовый план счетов

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Стартовый план счетов (docs/TZ.md, раздел 7). Субсчета наследуют аналитику и признаки родителя.
# (код, наименование, вид, количество, валюта, аналитика)
A, P, AP = "active", "passive", "active_passive"
MONEY = ["bank_account", "cash_flow_item"]
SETTLEMENTS = ["counterparty", "contract"]
STOCK = ["nomenclature", "warehouse"]
COSTS = ["cfo", "pl_item"]
STAFF = ["employee"]

CHART = [
    ("01", "Основные средства", A, False, False, []),
    ("02", "Амортизация основных средств", P, False, False, []),
    ("04", "Нематериальные активы", A, False, False, []),
    ("05", "Амортизация нематериальных активов", P, False, False, []),
    ("07", "Оборудование к установке", A, False, False, []),
    ("08", "Вложения во внеоборотные активы", A, False, False, []),
    ("10", "Материалы", A, True, False, STOCK),
    ("19", "НДС по приобретённым ценностям", A, False, False, []),
    ("20", "Основное производство", A, False, False, COSTS),
    ("23", "Вспомогательные производства", A, False, False, COSTS),
    ("25", "Общепроизводственные расходы", A, False, False, COSTS),
    ("26", "Общехозяйственные расходы", A, False, False, COSTS),
    ("41", "Товары", A, True, False, STOCK),
    ("43", "Готовая продукция", A, True, False, STOCK),
    ("44", "Расходы на продажу", A, False, False, COSTS),
    ("50", "Касса", A, False, False, ["cfo", "cash_flow_item"]),
    ("51", "Расчётные счета", A, False, False, MONEY),
    ("52", "Валютные счета", A, False, True, MONEY),
    ("55", "Специальные счета в банках", A, False, False, MONEY),
    ("57", "Переводы в пути", A, False, False, []),
    ("58", "Финансовые вложения", A, False, False, ["counterparty"]),
    ("60", "Расчёты с поставщиками и подрядчиками", AP, False, False, SETTLEMENTS),
    ("60.01", "Расчёты с поставщиками и подрядчиками", AP, False, False, None),
    ("60.02", "Авансы выданные", AP, False, False, None),
    ("62", "Расчёты с покупателями и заказчиками", AP, False, False, SETTLEMENTS),
    ("62.01", "Расчёты с покупателями и заказчиками", AP, False, False, None),
    ("62.02", "Авансы полученные", AP, False, False, None),
    ("66", "Расчёты по краткосрочным кредитам и займам", P, False, False, SETTLEMENTS),
    ("67", "Расчёты по долгосрочным кредитам и займам", P, False, False, SETTLEMENTS),
    ("68", "Расчёты по налогам и сборам", AP, False, False, []),
    ("68.01", "НДФЛ", AP, False, False, None),
    ("68.02", "НДС", AP, False, False, None),
    ("68.04", "Налог на прибыль", AP, False, False, None),
    ("68.10", "Прочие налоги и сборы", AP, False, False, None),
    ("69", "Расчёты по социальному страхованию и обеспечению", AP, False, False, []),
    ("70", "Расчёты с персоналом по оплате труда", P, False, False, STAFF),
    ("71", "Расчёты с подотчётными лицами", AP, False, False, STAFF),
    ("73", "Расчёты с персоналом по прочим операциям", AP, False, False, STAFF),
    ("75", "Расчёты с учредителями", AP, False, False, ["counterparty"]),
    ("76", "Расчёты с разными дебиторами и кредиторами", AP, False, False, SETTLEMENTS),
    ("79", "Внутрихозяйственные расчёты", AP, False, False, SETTLEMENTS),
    ("80", "Уставный капитал", P, False, False, []),
    ("82", "Резервный капитал", P, False, False, []),
    ("83", "Добавочный капитал", P, False, False, []),
    ("84", "Нераспределённая прибыль (непокрытый убыток)", AP, False, False, []),
    ("90", "Продажи", AP, False, False, COSTS),
    ("90.01", "Выручка", P, False, False, None),
    ("90.02", "Себестоимость продаж", A, False, False, None),
    ("90.03", "НДС", A, False, False, None),
    ("90.09", "Прибыль / убыток от продаж", AP, False, False, None),
    ("91", "Прочие доходы и расходы", AP, False, False, COSTS),
    ("91.01", "Прочие доходы", P, False, False, None),
    ("91.02", "Прочие расходы", A, False, False, None),
    ("91.09", "Сальдо прочих доходов и расходов", AP, False, False, None),
    ("94", "Недостачи и потери от порчи ценностей", A, False, False, []),
    ("96", "Резервы предстоящих расходов", P, False, False, []),
    ("97", "Расходы будущих периодов", A, False, False, []),
    ("98", "Доходы будущих периодов", P, False, False, []),
    ("99", "Прибыли и убытки", AP, False, False, []),
]


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('account_type', sa.String(length=20), nullable=False),
    sa.Column('is_quantitative', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_currency', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("account_type IN ('active', 'passive', 'active_passive')", name=op.f('ck_accounts_account_type')),
    sa.CheckConstraint("code ~ '^[0-9]{2}(\\.[0-9]{2}){0,2}$'", name=op.f('ck_accounts_code')),
    sa.ForeignKeyConstraint(['parent_id'], ['accounts.id'], name=op.f('fk_accounts_parent_id_accounts'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
    sa.UniqueConstraint('code', name=op.f('uq_accounts_code'))
    )
    op.create_index(op.f('ix_accounts_parent_id'), 'accounts', ['parent_id'], unique=False)
    op.create_table('account_analytics',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.CheckConstraint("kind IN ('bank_account', 'counterparty', 'contract', 'employee', 'nomenclature', 'warehouse', 'cfo', 'cash_flow_item', 'pl_item')", name=op.f('ck_account_analytics_kind')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_account_analytics_account_id_accounts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_account_analytics')),
    sa.UniqueConstraint('account_id', 'kind', name='uq_account_analytics_account_kind')
    )

    conn = op.get_bind()
    ids: dict[str, int] = {}
    inherited: dict[str, tuple[bool, bool, list[str]]] = {}
    for code, name, account_type, quantitative, currency, analytics in CHART:
        parent = code.rsplit(".", 1)[0] if "." in code else None
        if analytics is None:  # субсчёт: признаки и аналитика родителя
            quantitative, currency, analytics = inherited[parent]
        inherited[code] = (quantitative, currency, analytics)
        ids[code] = conn.execute(
            sa.text(
                "INSERT INTO accounts (code, name, parent_id, account_type, is_quantitative, is_currency) "
                "VALUES (:code, :name, :parent_id, :account_type, :q, :c) RETURNING id"
            ),
            {"code": code, "name": name, "parent_id": ids.get(parent) if parent else None,
             "account_type": account_type, "q": quantitative, "c": currency},
        ).scalar_one()
        for position, kind in enumerate(analytics, start=1):
            conn.execute(
                sa.text("INSERT INTO account_analytics (account_id, kind, position) VALUES (:a, :k, :p)"),
                {"a": ids[code], "k": kind, "p": position},
            )


def downgrade() -> None:
    op.drop_table('account_analytics')
    op.drop_index(op.f('ix_accounts_parent_id'), table_name='accounts')
    op.drop_table('accounts')
