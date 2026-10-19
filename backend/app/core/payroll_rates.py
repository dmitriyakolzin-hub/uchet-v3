"""Ставки НДФЛ и страховых взносов (docs/TZ.md, раздел 14). Только Decimal."""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from app.core.money import round_money

# НДФЛ с 2025 г.: прогрессивная шкала по доходу с начала года (верхняя граница ступени, ставка)
NDFL_SCALE = [
    (Decimal("2400000"), Decimal("0.13")),
    (Decimal("5000000"), Decimal("0.15")),
    (Decimal("20000000"), Decimal("0.18")),
    (Decimal("50000000"), Decimal("0.20")),
    (None, Decimal("0.22")),
]

# МРОТ и предельная база для взносов по годам; для следующих лет — последние известные значения
MROT = {2025: Decimal("22440"), 2026: Decimal("27093")}
INSURANCE_BASE_LIMIT = {2025: Decimal("2759000"), 2026: Decimal("2979000")}

GENERAL_RATE, GENERAL_OVER_LIMIT = Decimal("0.30"), Decimal("0.151")
SME_RATE, SME_REDUCED = Decimal("0.30"), Decimal("0.15")

# Нерабочие праздники (месяц, день); переносы выходных не учитываются — норму дней можно поправить
HOLIDAYS = {(1, d) for d in range(1, 9)} | {(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}


def _for_year(table: dict[int, Decimal], year: int) -> Decimal:
    known = [y for y in table if y <= year]
    return table[max(known)] if known else table[min(table)]


def mrot(year: int) -> Decimal:
    return _for_year(MROT, year)


def base_limit(year: int) -> Decimal:
    return _for_year(INSURANCE_BASE_LIMIT, year)


def ndfl_total(income_ytd: Decimal) -> Decimal:
    """НДФЛ со всего дохода с начала года по шкале, в целых рублях."""
    tax, lower = Decimal(0), Decimal(0)
    for upper, rate in NDFL_SCALE:
        top = income_ytd if upper is None else min(income_ytd, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or income_ytd <= upper:
            break
        lower = upper
    return tax.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def insurance(tariff: str, year: int, income: Decimal, income_ytd_before: Decimal) -> Decimal:
    """Взносы работодателя с выплат месяца."""
    if tariff == "general":
        limit = base_limit(year)
        within = max(Decimal(0), min(income, limit - income_ytd_before))
        return round_money(within * GENERAL_RATE + (income - within) * GENERAL_OVER_LIMIT)
    threshold = mrot(year) * Decimal("1.5")
    within = min(income, threshold)
    return round_money(within * SME_RATE + (income - within) * SME_REDUCED)


def working_days(start: dt.date, end: dt.date) -> int:
    """Рабочие дни (пн–пт без праздников) с start по end включительно."""
    days, day = 0, start
    while day <= end:
        if day.weekday() < 5 and (day.month, day.day) not in HOLIDAYS:
            days += 1
        day += dt.timedelta(days=1)
    return days
