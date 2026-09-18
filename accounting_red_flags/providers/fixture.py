"""Synthetic fixture provider.

This provider exists ONLY so the CLI, JSON/Parquet contract, validator and
research module can be exercised without credentials. It is deterministic,
obviously fake data and is never acceptable as production evidence:

* ``name`` says "synthetic demo data";
* ``requires_live_validation`` is ``True``;
* the service marks every result produced with it and the parquet writer
  refuses to write the canonical production database from fixture data.

Real runs must use :class:`accounting_red_flags.providers.pandadata.PandaDataProvider`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

FIXTURE_UNIVERSE = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]
_FIXTURE_YEARS = (2019, 2020, 2021, 2022, 2023, 2024)


def _row(symbol: str, year: int, **values: Any) -> dict[str, Any]:
    row = {
        "symbol": symbol,
        "quarter": f"{year}q4",
        "date": f"{year + 1}0430",
        "if_adjusted": 0,
        "is_revenue": None,
        "is_oper_cost": None,
        "is_n_income_attr_p": None,
        "cfs_net_cash_operating": None,
        "bs_net_accts_receive": None,
        "bs_notes_accts_receiv": None,
        "bs_inventory": None,
        "bs_total_assets": None,
    }
    row.update(values)
    return row


def build_fixture_reports() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    # 600001.SH - clean: cash-backed growth, stable margin, no red flags.
    revenue = 1000.0
    assets = 2000.0
    for year in _FIXTURE_YEARS:
        profit = revenue * 0.20
        rows.append(
            _row(
                "600001.SH",
                year,
                is_revenue=revenue,
                is_oper_cost=revenue * 0.60,
                is_n_income_attr_p=profit,
                cfs_net_cash_operating=profit * 1.20,
                bs_net_accts_receive=revenue * 0.15,
                bs_inventory=revenue * 0.12,
                bs_total_assets=assets,
            )
        )
        revenue *= 1.10
        assets *= 1.10

    # 600002.SH - red flags: weak cash conversion, AR/inventory build-up,
    # margin collapse, profit/revenue divergence and multi-year deterioration.
    revenue = 1000.0
    assets = 2000.0
    profits = [100.0, 110.0, 120.0, 130.0, 140.0, 300.0]
    cash = [90.0, 80.0, 70.0, 60.0, 50.0, 20.0]
    receivables = [100.0, 150.0, 225.0, 337.0, 506.0, 760.0]
    inventory = [100.0, 130.0, 169.0, 220.0, 286.0, 372.0]
    costs = [600.0, 610.0, 620.0, 630.0, 640.0, 750.0]
    for index, year in enumerate(_FIXTURE_YEARS):
        rows.append(
            _row(
                "600002.SH",
                year,
                is_revenue=revenue,
                is_oper_cost=costs[index],
                is_n_income_attr_p=profits[index],
                cfs_net_cash_operating=cash[index],
                bs_net_accts_receive=receivables[index],
                bs_inventory=inventory[index],
                bs_total_assets=assets,
            )
        )
        revenue += 10.0
        assets += 100.0

    # 600003.SH - a bank: financial industry, excluded as not_applicable.
    for index, year in enumerate(_FIXTURE_YEARS):
        rows.append(
            _row(
                "600003.SH",
                year,
                is_revenue=100000.0,
                is_oper_cost=None,
                is_n_income_attr_p=40000.0,
                cfs_net_cash_operating=90000.0,
                bs_net_accts_receive=None,
                bs_inventory=None,
                bs_total_assets=5_000_000.0 + index * 100_000.0,
            )
        )

    # 600004.SH - sparse: only one visible annual report -> insufficient_data.
    rows.append(
        _row(
            "600004.SH",
            2024,
            is_revenue=500.0,
            is_oper_cost=300.0,
            is_n_income_attr_p=50.0,
            cfs_net_cash_operating=10.0,
            bs_net_accts_receive=200.0,
            bs_inventory=150.0,
            bs_total_assets=1000.0,
        )
    )
    return pd.DataFrame(rows)


_FIXTURE_INDUSTRIES = {
    "600001.SH": {"industry_code": "801120", "industry_name": "食品饮料"},
    "600002.SH": {"industry_code": "801080", "industry_name": "电子"},
    "600003.SH": {"industry_code": "801780", "industry_name": "银行"},
    "600004.SH": {"industry_code": "801750", "industry_name": "计算机"},
}

_FIXTURE_PRICES = {
    "600001.SH": {"date": "20241231", "close": 40.0},
    "600002.SH": {"date": "20241231", "close": 12.0},
    "600003.SH": {"date": "20241231", "close": 8.0},
    "600004.SH": {"date": "20241231", "close": 20.0},
}

_FIXTURE_FORWARD_RETURNS = {
    "600001.SH": 0.10,
    "600002.SH": -0.20,
    "600003.SH": 0.02,
    "600004.SH": 0.01,
}


class FixtureProvider:
    """Deterministic synthetic provider for demos, docs and offline tests."""

    name = "Fixture (synthetic demo data)"
    requires_live_validation = True

    def configure_runtime(
        self, *, cache_dir: str | None = None, min_request_interval: float = 0.0, max_workers: int = 1
    ) -> None:  # noqa: D401 - interface parity
        return None

    def ensure_authenticated(self) -> None:
        return None

    def runtime_versions(self) -> dict[str, str]:
        return {"panda_data": "fixture", "pandas": pd.__version__, "numpy": "fixture", "pyarrow": "fixture"}

    def source_provenance(self) -> dict[str, Any]:
        return {"response_count": 1, "response_manifest_hash": "fixture"}

    def discover_universe(self, as_of: str) -> list[str]:
        return list(FIXTURE_UNIVERSE)

    def fetch_reports(self, symbols: list[str], as_of: str, years: int = 8) -> pd.DataFrame:
        frame = build_fixture_reports()
        return frame[frame["symbol"].isin(symbols)].reset_index(drop=True)

    def fetch_industries(self, symbols: list[str], as_of: str) -> dict[str, dict[str, str]]:
        return {symbol: dict(value) for symbol, value in _FIXTURE_INDUSTRIES.items() if symbol in symbols}

    def fetch_latest_prices(self, symbols: list[str], as_of: str) -> dict[str, dict[str, Any]]:
        return {symbol: dict(value) for symbol, value in _FIXTURE_PRICES.items() if symbol in symbols}

    def fetch_forward_returns(self, symbols: list[str], start_date: str, end_date: str) -> dict[str, float]:
        return {symbol: value for symbol, value in _FIXTURE_FORWARD_RETURNS.items() if symbol in symbols}
