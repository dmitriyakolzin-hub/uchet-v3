"""Основные средства (docs/TZ.md, раздел 14, «Детали 4.4»): карточка и начисления амортизации."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.reference import OrgScoped


class FixedAsset(OrgScoped, Base):
    """Карточка основного средства. Остатки по нему — в журнале: аналитика «Основное средство» на 01, 02, 08."""

    __tablename__ = "fixed_assets"
    __table_args__ = (
        CheckConstraint("cost >= 0", name="cost"),
        CheckConstraint("useful_life_months BETWEEN 1 AND 1200", name="useful_life"),
        CheckConstraint("disposal_kind IS NULL OR disposal_kind IN ('sale', 'writeoff')", name="disposal_kind"),
        CheckConstraint("(disposed_on IS NULL) = (disposal_kind IS NULL)", name="disposal_fields"),
        Index("uq_fixed_assets_organization_inventory", "organization_id", "inventory_number", unique=True,
              postgresql_where=text("inventory_number <> ''")),
    )

    name: Mapped[str] = mapped_column(String(255))
    inventory_number: Mapped[str] = mapped_column(String(50), default="", server_default="")
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))  # первоначальная стоимость
    useful_life_months: Mapped[int] = mapped_column(SmallInteger)
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    pl_item_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))  # статья амортизации
    # дата ввода в эксплуатацию; None — ещё не введено
    commissioned_on: Mapped[date | None] = mapped_column(Date)
    # операция Дт 01 — Кт 08; None у введённого до даты начальных остатков (остатки — вводом остатков)
    commission_operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    # выбытие: дата, вид (sale — продажа, writeoff — списание) и операция
    disposed_on: Mapped[date | None] = mapped_column(Date)
    disposal_kind: Mapped[str | None] = mapped_column(String(10))
    disposal_operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("investment_projects.id", ondelete="RESTRICT"))  # инвестпроект


class DepreciationRun(Base):
    """Начисление амортизации: юрлицо + месяц, одна проведённая операция журнала."""

    __tablename__ = "depreciation_runs"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "year", "month", name="uq_depreciation_runs_entity_month"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
