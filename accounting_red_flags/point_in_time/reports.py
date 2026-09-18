"""Point-in-time selection of financial reports.

The single most important guarantee in this project: a screen for ``as_of`` may
only use a report version that an investor could actually have seen on that
date. Selection is therefore driven by ``announce_date <= as_of`` and never by
the fiscal period alone.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..metrics import finite
from ..util import clean_date, clean_symbol, quarter_key


REQUIRED_REPORT_COLUMNS = {"symbol", "quarter", "date", "if_adjusted"}


def select_visible_revisions(
    frame: pd.DataFrame, as_of: str
) -> tuple[pd.DataFrame, set[tuple[str, str]]]:
    """Keep the last report version visible by ``as_of`` for each period.

    Two filings for the same ``(symbol, quarter)`` announced on the same latest
    date with different values are a genuine conflict; those periods are
    reported back so the caller can fail closed instead of picking arbitrarily.
    """

    missing = REQUIRED_REPORT_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"financial reports missing columns: {sorted(missing)}")
    work = frame.copy()
    work["symbol"] = work["symbol"].map(clean_symbol)
    work["quarter"] = work["quarter"].astype(str).str.lower()
    work["date"] = work["date"].map(clean_date)
    work["if_adjusted"] = pd.to_numeric(work["if_adjusted"], errors="coerce").astype("Int64")
    work = work[
        work["symbol"].notna()
        & work["quarter"].str.match(r"^\d{4}q[1-4]$")
        & work["date"].ne("")
        & (work["date"] <= clean_date(as_of))
    ].drop_duplicates()
    if work.empty:
        return work.reset_index(drop=True), set()

    keys = ["symbol", "quarter"]
    work = work.sort_values(keys + ["date"], kind="stable")
    latest_date = work.groupby(keys, sort=False)["date"].transform("max")
    latest = work[work["date"].eq(latest_date)].copy()
    conflicts: set[tuple[str, str]] = set()
    value_columns = [column for column in latest.columns if column not in keys + ["date"]]
    for key, group in latest.groupby(keys, sort=False):
        if value_columns and group[value_columns].nunique(dropna=False).gt(1).any():
            conflicts.add((str(key[0]), str(key[1])))
    selected = latest.drop_duplicates(keys, keep="last").reset_index(drop=True)
    return selected, conflicts


def _value(row: pd.Series, *names: str) -> float | None:
    """First finite value among ``names`` (used for documented field fallbacks)."""

    for name in names:
        if name in row.index:
            number = finite(row[name])
            if number is not None:
                return number
    return None


def annual_rows(
    visible: pd.DataFrame, symbol: str, *, max_years: int
) -> list[dict[str, Any]]:
    """Return normalized, chronologically sorted annual (q4) evidence rows.

    Only q4 filings are used: at q4 PandaData reports full-year cumulative
    statements, which is the evidence base for every metric in V1.
    """

    own = visible[visible["symbol"].map(clean_symbol).eq(clean_symbol(symbol))].copy()
    if own.empty:
        return []
    own["_key"] = own["quarter"].map(quarter_key)
    annual = own[own["quarter"].astype(str).str.endswith("q4")].copy()
    annual = annual[annual["_key"].notna()]
    annual["year"] = annual["_key"].map(lambda value: value[0] if value else None)
    annual = annual.dropna(subset=["year"]).sort_values("year")
    if annual.empty:
        return []
    annual = annual.tail(max_years)
    rows: list[dict[str, Any]] = []
    for _, row in annual.iterrows():
        rows.append(
            {
                "year": int(row["year"]),
                "quarter": str(row["quarter"]),
                "announce_date": clean_date(row["date"]),
                "if_adjusted": int(row["if_adjusted"]) if pd.notna(row["if_adjusted"]) else None,
                "revenue": _value(row, "is_revenue", "is_total_revenue"),
                "operating_cost": _value(row, "is_oper_cost", "is_total_cogs"),
                "net_profit": _value(row, "is_n_income_attr_p"),
                "operating_cash_flow": _value(row, "cfs_net_cash_operating"),
                "accounts_receivable": _value(row, "bs_net_accts_receive", "bs_notes_accts_receiv"),
                "inventory": _value(row, "bs_inventory"),
                "total_assets": _value(row, "bs_total_assets"),
            }
        )
    return rows
