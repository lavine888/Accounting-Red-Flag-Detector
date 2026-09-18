"""Provider abstraction.

A provider is the only place in the codebase allowed to touch the network. The
rule engine and point-in-time layer only ever see plain DataFrames and dicts,
which is what makes them fully unit-testable and deterministic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataProvider(Protocol):
    name: str
    requires_live_validation: bool

    def configure_runtime(
        self, *, cache_dir: str | None = None, min_request_interval: float = 0.0, max_workers: int = 1
    ) -> None: ...

    def ensure_authenticated(self) -> None: ...

    def runtime_versions(self) -> dict[str, str]: ...

    def source_provenance(self) -> dict[str, Any]: ...

    def discover_universe(self, as_of: str) -> list[str]: ...

    def fetch_reports(self, symbols: list[str], as_of: str, years: int) -> pd.DataFrame: ...

    def fetch_industries(self, symbols: list[str], as_of: str) -> dict[str, dict[str, str]]: ...

    def fetch_latest_prices(self, symbols: list[str], as_of: str) -> dict[str, dict[str, Any]]: ...

    def fetch_forward_returns(self, symbols: list[str], start_date: str, end_date: str) -> dict[str, float]: ...
