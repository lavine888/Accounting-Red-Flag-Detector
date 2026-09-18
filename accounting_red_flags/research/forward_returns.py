"""Forward-return horizon helpers."""

from __future__ import annotations

from datetime import datetime
import calendar

from ..util import clean_date


def add_months(date: str, months: int) -> str:
    """Return ``date`` shifted by ``months``, clamped to the month end."""

    cleaned = clean_date(date)
    if not cleaned:
        raise ValueError(f"invalid date: {date!r}")
    base = datetime.strptime(cleaned, "%Y%m%d")
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return f"{year:04d}{month:02d}{day:02d}"


def horizon_end_dates(signal_date: str, horizons: tuple[int, ...] = (3, 6, 12)) -> dict[int, str]:
    return {months: add_months(signal_date, months) for months in horizons}
