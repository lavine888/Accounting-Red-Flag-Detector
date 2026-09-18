"""Descriptive statistics shared by the research backtest."""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

from ..metrics import finite

FLAG_BUCKETS = ("0", "1", "2", "3", "4+")


def flag_bucket(count: int) -> str:
    return "4+" if count >= 4 else str(count)


def describe(values: Iterable[Any]) -> dict[str, Any]:
    numbers = [finite(value) for value in values]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return {"sample_size": 0, "mean_return": None, "median_return": None, "hit_rate": None}
    array = np.asarray(numbers, dtype=float)
    return {
        "sample_size": len(numbers),
        "mean_return": float(array.mean()),
        "median_return": float(np.median(array)),
        "hit_rate": float((array > 0).mean()),
    }


def portfolio_series(returns_by_date: list[tuple[str, float | None]]) -> dict[str, Any]:
    """Cumulative equal-weight series and max drawdown for one bucket."""

    equity = 1.0
    curve: list[dict[str, Any]] = []
    peak = 1.0
    max_drawdown = 0.0
    for date, value in returns_by_date:
        number = finite(value)
        if number is None:
            curve.append({"date": date, "return": None, "equity": equity})
            continue
        equity *= 1.0 + number
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
        curve.append({"date": date, "return": number, "equity": equity})
    periods = [value for _, value in returns_by_date if finite(value) is not None]
    return {
        "cumulative_return": equity - 1.0 if periods else None,
        "max_drawdown": max_drawdown if periods else None,
        "periods": len(periods),
        "curve": curve,
    }


def safe_spread(high: float | None, low: float | None) -> float | None:
    if high is None or low is None:
        return None
    if not (math.isfinite(high) and math.isfinite(low)):
        return None
    return high - low
