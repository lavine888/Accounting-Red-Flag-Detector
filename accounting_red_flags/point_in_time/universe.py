"""Point-in-time universe and industry membership.

A security is in scope only if it was listed on or before the decision date and
not yet delisted. Industry membership is resolved to the assignment valid on
the decision date; a symbol with no known membership is treated as missing
evidence (fail closed), never assumed to be non-financial.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..util import clean_date, clean_symbol


def filter_a_share_universe(frame: pd.DataFrame, as_of: str) -> list[str]:
    """Point-in-time SH/SZ universe from a ``get_stock_detail`` frame."""

    if frame.empty or "symbol" not in frame:
        return []
    cutoff = clean_date(as_of)
    work = frame.copy()
    work["symbol"] = work["symbol"].map(clean_symbol)
    listed = work.get("listed_date", pd.Series("", index=work.index)).map(clean_date)
    delisted = work.get("de_listed_date", pd.Series("", index=work.index))
    delisted = delisted.fillna("").map(clean_date)
    mask = (
        work["symbol"].astype(str).str.match(r"^\d{6}\.(SH|SZ)$")
        & listed.ne("")
        & (listed <= cutoff)
        & ((delisted == "") | (delisted > cutoff))
    )
    return sorted(set(work.loc[mask, "symbol"].dropna().astype(str)))


def select_industries(
    constituents: pd.DataFrame, details: pd.DataFrame, as_of: str
) -> dict[str, dict[str, str]]:
    """Resolve each symbol's Shenwan L1 industry valid on ``as_of``.

    Symbols with overlapping assignments on the same date are dropped (their
    membership is ambiguous) and later reported as missing industry evidence.
    """

    if constituents.empty:
        return {}
    work = constituents.copy()
    work["symbol"] = work["stock_symbol"].map(clean_symbol)
    work["in_date"] = work["in_date"].map(clean_date)
    work["out_date"] = work["out_date"].fillna("").map(clean_date)
    cutoff = clean_date(as_of)
    work = work[
        work["in_date"].ne("")
        & (work["in_date"] <= cutoff)
        & ((work["out_date"] == "") | (work["out_date"] > cutoff))
    ]
    conflicting = set(
        work.groupby("symbol")["l1_code"].nunique().loc[lambda values: values > 1].index
    )
    work = work[~work["symbol"].isin(conflicting)]
    work = work.sort_values(["symbol", "in_date"]).drop_duplicates("symbol", keep="last")
    names: dict[str, str] = {}
    if {"industry_code", "industry_name"}.issubset(details.columns):
        names = details.set_index("industry_code")["industry_name"].astype(str).to_dict()
    return {
        str(row["symbol"]): {
            "industry_code": str(row["l1_code"]),
            "industry_name": str(names.get(row["l1_code"], "")),
        }
        for _, row in work.iterrows()
    }
