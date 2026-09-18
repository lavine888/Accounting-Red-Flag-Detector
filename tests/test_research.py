from __future__ import annotations

import pytest

from accounting_red_flags.providers import FixtureProvider
from accounting_red_flags.research import add_months, analyze_snapshots, describe, flag_bucket
from accounting_red_flags.research.diagnostics import portfolio_series
from accounting_red_flags.service import screen


def test_add_months_clamps_to_month_end():
    assert add_months("20240131", 1) == "20240229"
    assert add_months("20230131", 1) == "20230228"
    assert add_months("20241231", 3) == "20250331"
    assert add_months("20241231", 6) == "20250630"
    assert add_months("20241231", 12) == "20251231"
    assert add_months("20240101", 12) == "20250101"


def test_add_months_rejects_invalid_date():
    with pytest.raises(ValueError):
        add_months("not-a-date", 3)


def test_flag_bucket():
    assert [flag_bucket(value) for value in (0, 1, 2, 3, 4, 9)] == ["0", "1", "2", "3", "4+", "4+"]


def test_describe_empty_and_populated():
    assert describe([]) == {"sample_size": 0, "mean_return": None, "median_return": None, "hit_rate": None}
    stats = describe([0.1, -0.1, 0.2, None, "bad"])
    assert stats["sample_size"] == 3
    assert stats["mean_return"] == pytest.approx(0.0666667, rel=1e-4)
    assert stats["median_return"] == pytest.approx(0.1)
    assert stats["hit_rate"] == pytest.approx(2 / 3)


def test_portfolio_series_drawdown():
    series = portfolio_series([("20241231", 0.1), ("20250630", -0.2), ("20251231", 0.05)])
    assert series["periods"] == 3
    assert series["cumulative_return"] == pytest.approx(1.1 * 0.8 * 1.05 - 1)
    assert series["max_drawdown"] == pytest.approx(-0.2)


def test_portfolio_series_ignores_missing_periods():
    series = portfolio_series([("20241231", None), ("20250630", 0.1)])
    assert series["periods"] == 1
    assert series["curve"][0]["return"] is None


def test_analyze_snapshots_requires_two_dates():
    with pytest.raises(ValueError, match="at least two"):
        analyze_snapshots([], lambda *_: {})


@pytest.fixture()
def snapshots():
    provider = FixtureProvider()
    return [
        screen(as_of="20241231", symbols=["600001.SH", "600002.SH", "600003.SH", "600004.SH"], provider=provider),
        screen(as_of="20251231", symbols=["600001.SH", "600002.SH", "600003.SH", "600004.SH"], provider=provider),
    ]


def test_analyze_snapshots_groups_by_flag_and_risk(snapshots):
    returns = {"600001.SH": 0.10, "600002.SH": -0.20}
    result = analyze_snapshots(snapshots, lambda symbols, start, end: returns, horizons=(3, 12))
    assert result["signal_dates"] == ["20241231", "20251231"]
    assert result["horizons"] == [3, 12]
    pooled = result["pooled"]["12"]
    assert pooled["groups_by_risk_level"]["high"]["sample_size"] == 2
    assert pooled["groups_by_risk_level"]["low"]["sample_size"] == 2
    assert pooled["high_minus_low_mean_return"] == pytest.approx(-0.30)
    assert pooled["high_risk_portfolio"]["cumulative_return"] == pytest.approx(0.8 * 0.8 - 1)


def test_analyze_snapshots_never_drops_missing_returns_silently(snapshots):
    result = analyze_snapshots(snapshots, lambda symbols, start, end: {"600001.SH": 0.1}, horizons=(3,))
    first = result["snapshots"][0]["horizons"]["3"]
    assert "600002.SH" in first["missing_symbols"]
    assert first["forward_return_coverage"] is not None
    assert first["forward_return_coverage"] < 1.0


def test_analyze_snapshots_reports_limitations(snapshots):
    result = analyze_snapshots(snapshots, lambda symbols, start, end: {}, horizons=(3,))
    assert any("not evidence about real markets" in item for item in result["limitations"])
    assert result["snapshots"][0]["horizons"]["3"]["groups_by_flag_count"]["0"]["sample_size"] == 0
