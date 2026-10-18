"""Курс валюты на дату. Используется журналом операций для пересчёта в рубли (шаг 1.5)."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import BASE_CURRENCY
from app.models import Currency, ExchangeRate


class RateNotFound(LookupError):
    pass


@dataclass(frozen=True)
class EffectiveRate:
    rate: Decimal
    nominal: int
    rate_date: dt.date | None  # None — базовая валюта, курс всегда 1


def rate_on(db: Session, currency: Currency, on_date: dt.date) -> EffectiveRate:
    """Последний курс на дату on_date или раньше."""
    if currency.code == BASE_CURRENCY:
        return EffectiveRate(rate=Decimal(1), nominal=1, rate_date=None)
    row = db.scalar(
        select(ExchangeRate)
        .where(ExchangeRate.currency_id == currency.id, ExchangeRate.rate_date <= on_date)
        .order_by(ExchangeRate.rate_date.desc())
        .limit(1)
    )
    if row is None:
        raise RateNotFound(f"Нет курса {currency.code} на {on_date:%d.%m.%Y} или ранее")
    return EffectiveRate(rate=row.rate, nominal=row.nominal, rate_date=row.rate_date)
