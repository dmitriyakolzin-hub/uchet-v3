from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Currency(Base):
    """Валюта. Справочник общий для всех организаций; RUB — базовая валюта."""

    __tablename__ = "currencies"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z]{3}$'", name="code"),
        CheckConstraint("numeric_code ~ '^[0-9]{3}$'", name="numeric_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(3), unique=True)
    numeric_code: Mapped[str] = mapped_column(String(3), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExchangeRate(Base):
    """Курс: nominal единиц валюты = rate рублей на дату rate_date."""

    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("currency_id", "rate_date", name="uq_exchange_rates_currency_date"),
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint("nominal > 0", name="nominal_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    currency_id: Mapped[int] = mapped_column(
        ForeignKey("currencies.id", ondelete="CASCADE"), index=True
    )
    rate_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    nominal: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
