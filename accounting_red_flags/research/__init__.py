"""Research modules: forward returns, diagnostics and the red-flag backtest."""

from __future__ import annotations

from .backtest import analyze_snapshots
from .diagnostics import describe, flag_bucket
from .forward_returns import add_months, horizon_end_dates

__all__ = [
    "analyze_snapshots",
    "describe",
    "flag_bucket",
    "add_months",
    "horizon_end_dates",
]
