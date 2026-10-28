from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Identity, Index, Integer, String, event, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.standard_articles import STANDARD_ARTICLES
from app.db.base import Base
from app.models.organization import Organization

CASH_FLOW_ACTIVITIES = {
    "operating": "Операционная",
    "investing": "Инвестиционная",
    "financing": "Финансовая",
}
CASH_FLOW_DIRECTIONS = {"inflow": "Поступление", "outflow": "Выплата"}
PL_SECTIONS = {
    "revenue": "Выручка",
    "cost": "Себестоимость",
    "selling": "Коммерческие расходы",
    "admin": "Управленческие расходы",
    "other_income": "Прочие доходы",
    "other_expense": "Прочие расходы",
    "income_tax": "Налог на прибыль",
}


class Article(Base):
    """Статья ДДС и/или ОПиУ с шаблоном проводки. Справочник у каждой организации свой, статьи — деревом
    (docs/TZ.md, раздел 8 и раздел 19, «Детали 9.1»): группа — статья с подстатьями, в операции ставятся только
    статьи без подстатей."""

    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint("is_cash_flow OR is_pl", name="cash_flow_or_pl"),
        CheckConstraint(
            "is_cash_flow = (cash_flow_activity IS NOT NULL AND cash_flow_direction IS NOT NULL)",
            name="cash_flow_fields",
        ),
        CheckConstraint("is_pl = (pl_section IS NOT NULL)", name="pl_fields"),
        CheckConstraint(
            "cash_flow_activity IN ('operating', 'investing', 'financing')", name="cash_flow_activity"
        ),
        CheckConstraint("cash_flow_direction IN ('inflow', 'outflow')", name="cash_flow_direction"),
        CheckConstraint(
            "pl_section IN ('revenue', 'cost', 'selling', 'admin', 'other_income', "
            "'other_expense', 'income_tax')",
            name="pl_section",
        ),
        Index("uq_articles_sibling_name", "organization_id", "parent_id", "name", unique=True,
              postgresql_nulls_not_distinct=True),
        Index("uq_articles_organization_code", "organization_id", "code", unique=True,
              postgresql_where=text("code IS NOT NULL")),
        Index("uq_articles_organization_system_key", "organization_id", "system_key", unique=True,
              postgresql_where=text("system_key IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"), index=True)
    code: Mapped[str | None] = mapped_column(String(30))  # код статьи, например «2.6.13.1.»
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # порядок среди соседних
    # статья, которую использует сама программа (app/core/standard_articles.py: SYSTEM_KEYS)
    system_key: Mapped[str | None] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(255))
    is_cash_flow: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cash_flow_activity: Mapped[str | None] = mapped_column(String(20))
    cash_flow_direction: Mapped[str | None] = mapped_column(String(20))
    is_pl: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pl_section: Mapped[str | None] = mapped_column(String(20))
    # Шаблон проводки: подставляется при вводе операции, можно поменять вручную
    debit_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    credit_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    # Перевод между своими счетами: в отчёте ДДС поступление и выплата по ней взаимно гасятся
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


def seed_articles(connection, organization_id: int) -> None:
    """Стандартный набор статей новой организации (шаблоны проводок — по кодам счетов)."""
    accounts = dict(connection.execute(text("SELECT code, id FROM accounts")).all())
    for order, (name, flow, section, debit, credit, key) in enumerate(STANDARD_ARTICLES, start=1):
        connection.execute(text(
            "INSERT INTO articles (organization_id, name, sort_order, system_key, is_cash_flow, cash_flow_activity,"
            " cash_flow_direction, is_pl, pl_section, debit_account_id, credit_account_id, is_transfer, is_active)"
            " VALUES (:org, :name, :order, :key, :cf, :activity, :direction, :pl, :section, :debit, :credit,"
            " :transfer, true)"), {
            "org": organization_id, "name": name, "order": order, "key": key, "cf": flow is not None,
            "activity": flow[0] if flow else None, "direction": flow[1] if flow else None,
            "pl": section is not None, "section": section, "debit": accounts.get(debit), "credit": accounts.get(credit),
            "transfer": key == "transfer"})


@event.listens_for(Organization, "after_insert")
def _organization_articles(_mapper, connection, target: Organization) -> None:
    seed_articles(connection, target.id)
