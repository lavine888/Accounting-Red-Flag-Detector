from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounting_red_flags.config import RuleConfig, load_rule_config
from accounting_red_flags.providers import FixtureProvider


@pytest.fixture()
def rule_config() -> RuleConfig:
    return load_rule_config()


@pytest.fixture()
def fixture_provider() -> FixtureProvider:
    return FixtureProvider()


def make_annual(year: int, **values) -> dict:
    """A complete annual evidence row with overridable fields."""

    row = {
        "year": year,
        "quarter": f"{year}q4",
        "announce_date": f"{year + 1}0430",
        "if_adjusted": 0,
        "revenue": 1000.0,
        "operating_cost": 600.0,
        "net_profit": 100.0,
        "operating_cash_flow": 120.0,
        "accounts_receivable": 100.0,
        "inventory": 100.0,
        "total_assets": 1000.0,
    }
    row.update(values)
    return row


@pytest.fixture()
def annual_factory():
    return make_annual
