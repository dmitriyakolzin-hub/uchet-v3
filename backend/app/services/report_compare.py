"""Сравнение отчётов с другим периодом и «косты» — доля к выручке (docs/TZ.md, раздел 27, «Детали 17.1»).

Сравнение считается тем же отчётом и с теми же фильтрами, только за другой период:
- year — тот же месяц год назад (колонка «Итого» — тот же период прошлого года);
- month — предыдущий месяц (колонка «Итого» — период, сдвинутый на месяц);
- avg6 — среднее за 6 месяцев перед месяцем колонки (колонка «Итого» — сумма этих средних).
"""

import calendar
import datetime as dt
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from app.core.money import round_money
from app.services.reports import ReportFilter, month_columns

ZERO = Decimal("0.00")
MODES = {"year": "с прошлым годом", "month": "с прошлым месяцем", "avg6": "со средним за 6 месяцев"}
AVG_MONTHS = 6

# build(f) → {ключ строки: {ключ колонки «ГГГГ-ММ» / total: сумма}}
Build = Callable[[ReportFilter], dict[str, dict[str, Decimal]]]


def shift(day: dt.date, months: int) -> dt.date:
    """Дата на months месяцев раньше/позже; последний день месяца остаётся последним, 31-е — не дальше конца месяца."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    was_last = day.day == calendar.monthrange(day.year, day.month)[1]
    return dt.date(year, month + 1, last if was_last else min(day.day, last))


def _shifted(f: ReportFilter, build: Build, months: int) -> dict[str, dict[str, Decimal]]:
    """Отчёт за период, сдвинутый на months месяцев; колонки сопоставляются по порядку."""
    other = replace(f, date_from=shift(f.date_from, months), date_to=shift(f.date_to, months))
    own_keys = [c.key for c in month_columns(f.date_from, f.date_to)]
    other_keys = [c.key for c in month_columns(other.date_from, other.date_to)]
    data = build(other)
    return {row: {own: values.get(theirs, ZERO) for own, theirs in zip(own_keys, other_keys, strict=False)}
            for row, values in data.items()}


def _average(f: ReportFilter, build: Build) -> dict[str, dict[str, Decimal]]:
    months = month_columns(f.date_from, f.date_to)[:-1]
    first = f.date_from.replace(day=1)
    other = replace(f, date_from=shift(first, -AVG_MONTHS),
                    date_to=months[-1].date_from.replace(day=1) - dt.timedelta(days=1))
    data = build(other)
    result = {}
    for row, values in data.items():
        averages = {}
        for col in months:
            start = col.date_from.replace(day=1)
            keys = [f"{shift(start, -i):%Y-%m}" for i in range(1, AVG_MONTHS + 1)]
            averages[col.key] = round_money(sum((values.get(k, ZERO) for k in keys), ZERO) / AVG_MONTHS)
        averages["total"] = sum(averages.values(), ZERO)
        result[row] = averages
    return result


def compare(f: ReportFilter, mode: str, build: Build) -> dict[str, dict[str, Decimal]]:
    """Значения для сравнения: {строка: {колонка отчёта: сумма}}."""
    if mode == "year":
        return _shifted(f, build, -12)
    if mode == "month":
        return _shifted(f, build, -1)
    if mode == "avg6":
        return _average(f, build)
    raise ValueError(mode)
