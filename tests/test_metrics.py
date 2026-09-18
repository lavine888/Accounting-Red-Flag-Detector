from __future__ import annotations

import math

import pytest

from accounting_red_flags.metrics import (
    accrual_ratio,
    average,
    cash_conversion_ratio,
    finite,
    gross_margin,
    growth_rate,
    mean_and_std,
    safe_ratio,
    strictly_decreasing,
    z_score,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, 1.0),
        (1.5, 1.5),
        ("2.5", 2.5),
        (None, None),
        ("", None),
        ("abc", None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        (True, 1.0),
    ],
)
def test_finite(value, expected):
    assert finite(value) == expected


def test_safe_ratio_requires_positive_denominator():
    assert safe_ratio(10, 2) == 5.0
    assert safe_ratio(10, 0) is None
    assert safe_ratio(10, -2) is None
    assert safe_ratio(None, 2) is None
    assert safe_ratio(10, None) is None


def test_growth_rate_boundaries():
    assert growth_rate(110, 100) == pytest.approx(0.1)
    assert growth_rate(0, 0) == 0.0
    assert growth_rate(100, 0) is None
    assert growth_rate(100, -100) is None
    assert growth_rate(50, 100) == pytest.approx(-0.5)
    assert growth_rate(None, 100) is None
    assert growth_rate(100, None) is None


def test_average_requires_all_values():
    assert average(1, 3) == 2.0
    assert average(1, 2, 3) == 2.0
    assert average(1, None) is None
    assert average() is None


def test_cash_conversion_ratio_only_for_positive_profit():
    assert cash_conversion_ratio(120, 100) == pytest.approx(1.2)
    assert cash_conversion_ratio(-5, 100) == pytest.approx(-0.05)
    assert cash_conversion_ratio(10, 0) is None
    assert cash_conversion_ratio(10, -1) is None
    assert cash_conversion_ratio(None, 100) is None


def test_accrual_ratio():
    assert accrual_ratio(100, 40, 1000) == pytest.approx(0.06)
    assert accrual_ratio(100, 40, 0) is None
    assert accrual_ratio(100, None, 1000) is None
    assert accrual_ratio(None, 40, 1000) is None


def test_gross_margin():
    assert gross_margin(1000, 600) == pytest.approx(0.4)
    assert gross_margin(1000, 1200) == pytest.approx(-0.2)
    assert gross_margin(0, 600) is None
    assert gross_margin(1000, None) is None
    assert gross_margin(None, 600) is None


def test_mean_and_std_population():
    mean, std = mean_and_std([1, 2, 3, 4])
    assert mean == pytest.approx(2.5)
    assert std == pytest.approx(math.sqrt(1.25))
    assert mean_and_std([1, None]) == (None, None)
    assert mean_and_std([]) == (None, None)


def test_z_score():
    assert z_score(3, 1, 1) == pytest.approx(2.0)
    assert z_score(3, 1, 0) is None
    assert z_score(None, 1, 1) is None
    assert z_score(3, None, 1) is None


def test_strictly_decreasing():
    assert strictly_decreasing([3, 2, 1]) is True
    assert strictly_decreasing([3, 3, 1]) is False
    assert strictly_decreasing([1, 2, 3]) is False
    assert strictly_decreasing([2, 1]) is True
    assert strictly_decreasing([1]) is None
    assert strictly_decreasing([3, None, 1]) is None


def test_no_helper_returns_nan_or_inf():
    values = [
        safe_ratio(1, 3),
        growth_rate(1, 3),
        average(1, 3),
        cash_conversion_ratio(1, 3),
        accrual_ratio(1, 2, 3),
        gross_margin(1, 3),
        z_score(1, 2, 3),
    ]
    for value in values:
        assert value is None or math.isfinite(value)
