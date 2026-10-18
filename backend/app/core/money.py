"""Деньги и курсы: только Decimal, никакого float (CLAUDE.md, правило 5)."""

from decimal import ROUND_HALF_UP, Decimal

KOPECK = Decimal("0.01")
BASE_CURRENCY = "RUB"


def round_money(value: Decimal) -> Decimal:
    """Округление до копеек по правилам арифметики: 0,005 → 0,01."""
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def to_rub(amount: Decimal, rate: Decimal, nominal: int) -> Decimal:
    """Сумма в валюте → рубли по курсу «nominal единиц валюты = rate рублей»."""
    return round_money(amount * rate / nominal)
