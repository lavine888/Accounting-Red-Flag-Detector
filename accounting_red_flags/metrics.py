"""Pure financial-metric calculations.

Every helper returns ``None`` when the input is missing, non-finite or the
denominator is not economically meaningful. Nothing here ever substitutes a
missing value with zero, and no helper can produce ``inf`` or ``nan``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


def finite(value: Any) -> float | None:
    """Return ``value`` as a finite float, or ``None``."""

    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    """numerator / denominator, requiring a strictly positive denominator."""

    top = finite(numerator)
    bottom = finite(denominator)
    if top is None or bottom is None or bottom <= 0:
        return None
    return top / bottom


def growth_rate(current: Any, previous: Any) -> float | None:
    """(current - previous) / previous with an unambiguous sign base.

    A zero base is only meaningful when both periods are zero (flat). A
    negative base makes a percentage growth rate sign-ambiguous, so it returns
    ``None`` instead of a misleading number.
    """

    now = finite(current)
    before = finite(previous)
    if now is None or before is None:
        return None
    if before == 0:
        return 0.0 if now == 0 else None
    if before < 0:
        return None
    return now / before - 1.0


def average(*values: Any) -> float | None:
    numbers = [finite(value) for value in values]
    if not numbers or any(number is None for number in numbers):
        return None
    return float(np.mean(numbers))


def cash_conversion_ratio(operating_cash_flow: Any, net_profit: Any) -> float | None:
    """CFO / net profit. Only defined for positive reported profit."""

    profit = finite(net_profit)
    if profit is None or profit <= 0:
        return None
    return safe_ratio(operating_cash_flow, profit)


def accrual_ratio(net_profit: Any, operating_cash_flow: Any, average_total_assets: Any) -> float | None:
    """(net profit - CFO) / average total assets."""

    profit = finite(net_profit)
    cash = finite(operating_cash_flow)
    assets = finite(average_total_assets)
    if profit is None or cash is None or assets is None or assets <= 0:
        return None
    return (profit - cash) / assets


def gross_margin(revenue: Any, operating_cost: Any) -> float | None:
    """(revenue - operating cost) / revenue. Only defined for positive revenue."""

    top = finite(revenue)
    cost = finite(operating_cost)
    if top is None or cost is None or top <= 0:
        return None
    return (top - cost) / top


def mean_and_std(values: Iterable[Any]) -> tuple[float | None, float | None]:
    """Population mean and standard deviation (ddof=0), ignoring nothing.

    Returns ``(None, None)`` if any element is missing or the list is empty.
    """

    numbers = [finite(value) for value in values]
    if not numbers or any(number is None for number in numbers):
        return None, None
    array = np.asarray(numbers, dtype=float)
    return float(array.mean()), float(array.std(ddof=0))


def z_score(value: Any, mean: Any, std: Any) -> float | None:
    """Standardised deviation. Zero standard deviation yields ``None``."""

    current = finite(value)
    center = finite(mean)
    spread = finite(std)
    if current is None or center is None or spread is None or spread <= 0:
        return None
    return (current - center) / spread


def strictly_decreasing(values: Iterable[Any]) -> bool | None:
    """True when every step is a strict decrease. ``None`` if any value missing."""

    numbers = [finite(value) for value in values]
    if len(numbers) < 2 or any(number is None for number in numbers):
        return None
    return all(b < a for a, b in zip(numbers, numbers[1:]))
