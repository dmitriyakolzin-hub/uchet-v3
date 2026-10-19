"""Бюджеты и их версии (docs/TZ.md, раздел 13). План — плановые операции версии (шаг 3.3)."""

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

MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
               "октябрь", "ноябрь", "декабрь"]
QUARTERS = ["I", "II", "III", "IV"]


class Budget(Base):
    """Бюджет организации на период: год, квартал или месяц."""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("organization_id", "kind", "year", "period", name="uq_budgets_organization_period"),
        CheckConstraint("kind IN ('year', 'quarter', 'month')", name="kind"),
        CheckConstraint(
            "(kind = 'year' AND period = 1) OR (kind = 'quarter' AND period BETWEEN 1 AND 4)"
            " OR (kind = 'month' AND period BETWEEN 1 AND 12)",
            name="period",
        ),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="year"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    year: Mapped[int] = mapped_column(SmallInteger)
    period: Mapped[int] = mapped_column(SmallInteger)  # год — 1, квартал — 1…4, месяц — 1…12
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def months(self) -> list[int]:
        """Месяцы года, которые покрывает бюджет."""
        if self.kind == "year":
            return list(range(1, 13))
        if self.kind == "quarter":
            return [3 * self.period - 2, 3 * self.period - 1, 3 * self.period]
        return [self.period]

    @property
    def title(self) -> str:
        if self.kind == "year":
            return f"Бюджет {self.year}"
        if self.kind == "quarter":
            return f"Бюджет {QUARTERS[self.period - 1]} кв. {self.year}"
        return f"Бюджет {MONTH_NAMES[self.period - 1]} {self.year}"


class BudgetVersion(Base):
    """Версия бюджета: черновик → утверждена → архив. Утверждённая версия у бюджета одна."""

    __tablename__ = "budget_versions"
    __table_args__ = (
        UniqueConstraint("budget_id", "number", name="uq_budget_versions_budget_number"),
        CheckConstraint("status IN ('draft', 'approved', 'archived')", name="status"),
        Index("uq_budget_versions_one_approved", "budget_id", unique=True,
              postgresql_where=text("status = 'approved'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(SmallInteger)  # № версии внутри бюджета
    name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")
    based_on_id: Mapped[int | None] = mapped_column(ForeignKey("budget_versions.id", ondelete="SET NULL"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvestmentProject(OrgScoped, Base):
    """Инвестпроект: объект вложений с лимитом на весь срок (docs/TZ.md, раздел 16, «Детали 6.3»)."""

    __tablename__ = "investment_projects"
    __table_args__ = (
        CheckConstraint("status IN ('planned', 'active', 'done')", name="status"),
        CheckConstraint("limit_amount >= 0", name="limit_amount"),
        CheckConstraint("payment_lag_months BETWEEN 0 AND 24", name="payment_lag"),
        CheckConstraint("finish_on IS NULL OR start_on IS NULL OR finish_on >= start_on", name="dates"),
    )

    name: Mapped[str] = mapped_column(String(255))
    cfo_id: Mapped[int] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id", ondelete="RESTRICT"))
    start_on: Mapped[date | None] = mapped_column(Date)
    finish_on: Mapped[date | None] = mapped_column(Date)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(10), default="planned", server_default="planned")
    payment_lag_months: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
