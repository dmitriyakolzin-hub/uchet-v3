from decimal import Decimal

import pytest

from app.core.money import round_money, to_rub


@pytest.mark.parametrize("value,expected", [
    ("0.005", "0.01"), ("0.004", "0.00"), ("1.115", "1.12"), ("-1.115", "-1.12"),
    ("2.675", "2.68"),  # во float это 2.67 — проверяем, что считаем в Decimal
])
def test_round_money(value, expected):
    assert round_money(Decimal(value)) == Decimal(expected)


def test_to_rub_with_nominal():
    # 100 иен = 50,1234 ₽; 1 234,56 иены → 618,8 ₽ (618,7954… → 618,80)
    assert to_rub(Decimal("1234.56"), Decimal("50.1234"), 100) == Decimal("618.80")


def test_to_rub_large_amount_exact():
    assert to_rub(Decimal("9999999999999.99"), Decimal("1"), 1) == Decimal("9999999999999.99")
