from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Identity, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

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
    """Статья ДДС и/или ОПиУ с шаблоном проводки. Общий справочник (docs/TZ.md, раздел 8)."""

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
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    is_cash_flow: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cash_flow_activity: Mapped[str | None] = mapped_column(String(20))
    cash_flow_direction: Mapped[str | None] = mapped_column(String(20))
    is_pl: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pl_section: Mapped[str | None] = mapped_column(String(20))
    # Шаблон проводки: подставляется при вводе операции, можно поменять вручную
    debit_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    credit_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
