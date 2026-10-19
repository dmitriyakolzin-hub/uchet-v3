from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Account(Base):
    """Счёт плана счетов. Общий для всех организаций, изменяет администратор."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(r"code ~ '^[0-9]{2}(\.[0-9]{2}){0,2}$'", name="code"),
        CheckConstraint(
            "account_type IN ('active', 'passive', 'active_passive')", name="account_type"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    account_type: Mapped[str] = mapped_column(String(20))
    is_quantitative: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_currency: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    analytics: Mapped[list["AccountAnalytic"]] = relationship(
        order_by="AccountAnalytic.position", cascade="all, delete-orphan", lazy="selectin"
    )


class AccountAnalytic(Base):
    """Обязательная аналитика счёта: вид и её место в проводке."""

    __tablename__ = "account_analytics"
    __table_args__ = (
        UniqueConstraint("account_id", "kind", name="uq_account_analytics_account_kind"),
        CheckConstraint(
            "kind IN ('bank_account', 'counterparty', 'contract', 'employee', 'nomenclature', "
            "'warehouse', 'fixed_asset', 'cfo', 'cash_flow_item', 'pl_item')",
            name="kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    position: Mapped[int] = mapped_column(SmallInteger)
