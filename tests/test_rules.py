from __future__ import annotations

import pytest

from accounting_red_flags.config import RuleConfig
from accounting_red_flags.models import FLAG_NAMES, RiskLevel, Status
from accounting_red_flags.rules import (
    evaluate_symbol,
    flag_accrual_quality,
    flag_cash_conversion,
    flag_cash_conversion_deterioration,
    flag_gross_margin_anomaly,
    flag_inventory_divergence,
    flag_profit_revenue_divergence,
    flag_receivable_divergence,
    industry_known,
    is_financial_industry,
)

from conftest import make_annual

CONFIG = RuleConfig()


def series(years, **fields):
    rows = []
    for index, year in enumerate(years):
        row = make_annual(year)
        for name, values in fields.items():
            if isinstance(values, (list, tuple)):
                row[name] = values[index]
            else:
                row[name] = values
        rows.append(row)
    return rows


# --- RF01 ------------------------------------------------------------------


def test_rf01_boundary_is_exclusive():
    at_threshold = flag_cash_conversion(make_annual(2024, net_profit=100.0, operating_cash_flow=80.0), CONFIG)
    assert at_threshold.state is False
    below = flag_cash_conversion(make_annual(2024, net_profit=100.0, operating_cash_flow=79.0), CONFIG)
    assert below.state is True


def test_rf01_missing_cash_flow_is_null():
    result = flag_cash_conversion(make_annual(2024, operating_cash_flow=None), CONFIG)
    assert result.state is None
    assert result.reason == "missing_cash_flow_evidence"


def test_rf01_loss_making_company_is_not_flagged():
    result = flag_cash_conversion(make_annual(2024, net_profit=-10.0, operating_cash_flow=-50.0), CONFIG)
    assert result.state is False
    assert result.evidence["note"] == "nonpositive_net_profit"


# --- RF02 / RF03 -----------------------------------------------------------


def test_rf02_gap_boundary():
    prior = make_annual(2023, accounts_receivable=100.0, revenue=1000.0)
    latest = make_annual(2024, accounts_receivable=130.0, revenue=1100.0)
    assert flag_receivable_divergence(latest, prior, CONFIG).state is False
    latest_above = make_annual(2024, accounts_receivable=131.0, revenue=1100.0)
    assert flag_receivable_divergence(latest_above, prior, CONFIG).state is True


def test_rf02_growth_floor_boundary():
    prior = make_annual(2023, accounts_receivable=100.0, revenue=1000.0)
    latest = make_annual(2024, accounts_receivable=120.0, revenue=1100.0)
    assert flag_receivable_divergence(latest, prior, CONFIG).state is False


def test_rf02_without_prior_is_null():
    result = flag_receivable_divergence(make_annual(2024), None, CONFIG)
    assert result.state is None
    assert result.reason == "insufficient_annual_history"


def test_rf03_gap_boundary():
    prior = make_annual(2023, inventory=100.0, revenue=1000.0)
    assert flag_inventory_divergence(make_annual(2024, inventory=130.0, revenue=1100.0), prior, CONFIG).state is False
    assert flag_inventory_divergence(make_annual(2024, inventory=131.0, revenue=1100.0), prior, CONFIG).state is True


def test_rf02_nonpositive_base_is_null_not_guessed():
    prior = make_annual(2023, accounts_receivable=-5.0, revenue=1000.0)
    result = flag_receivable_divergence(make_annual(2024, accounts_receivable=100.0, revenue=1100.0), prior, CONFIG)
    assert result.state is None
    assert result.reason == "nonpositive_previous_base"


# --- RF04 ------------------------------------------------------------------


def test_rf04_boundary_is_exclusive():
    prior = make_annual(2023, total_assets=1000.0)
    at_threshold = flag_accrual_quality(make_annual(2024, net_profit=100.0, operating_cash_flow=0.0, total_assets=1000.0), prior, CONFIG)
    assert at_threshold.state is False
    above = flag_accrual_quality(make_annual(2024, net_profit=101.0, operating_cash_flow=0.0, total_assets=1000.0), prior, CONFIG)
    assert above.state is True


def test_rf04_missing_assets_is_null():
    prior = make_annual(2023, total_assets=None)
    result = flag_accrual_quality(make_annual(2024), prior, CONFIG)
    assert result.state is None
    assert result.reason == "missing_average_total_assets"


def test_rf04_without_prior_is_null():
    assert flag_accrual_quality(make_annual(2024), None, CONFIG).reason == "insufficient_annual_history"


# --- RF05 ------------------------------------------------------------------


def test_rf05_boundary_uses_absolute_change_floor():
    history = series([2020, 2021, 2022, 2023], revenue=[1000.0] * 4, operating_cost=[600.0] * 4)
    below = history + [make_annual(2024, revenue=1000.0, operating_cost=649.0)]
    assert flag_gross_margin_anomaly(below, CONFIG).state is False
    above = history + [make_annual(2024, revenue=1000.0, operating_cost=651.0)]
    assert flag_gross_margin_anomaly(above, CONFIG).state is True


def test_rf05_requires_minimum_history():
    history = series([2023], revenue=[1000.0], operating_cost=[600.0])
    latest = make_annual(2024, revenue=1000.0, operating_cost=200.0)
    result = flag_gross_margin_anomaly(history + [latest], CONFIG)
    assert result.state is None
    assert result.reason == "insufficient_gross_margin_history"


def test_rf05_uses_zscore_when_history_is_volatile():
    margins = [0.30, 0.50, 0.30, 0.50]
    history = series(
        [2020, 2021, 2022, 2023],
        revenue=[1000.0] * 4,
        operating_cost=[(1 - margin) * 1000.0 for margin in margins],
    )
    latest = make_annual(2024, revenue=1000.0, operating_cost=1000.0 * (1 - 0.35))
    result = flag_gross_margin_anomaly(history + [latest], CONFIG)
    assert result.state is None or isinstance(result.state, bool)
    assert result.evidence["gross_margin_threshold"] >= CONFIG.gross_margin_abs_change_min


def test_rf05_empty_history_is_null():
    assert flag_gross_margin_anomaly([], CONFIG).reason == "no_visible_annual_reports"


# --- RF06 ------------------------------------------------------------------


def test_rf06_gap_boundary():
    prior = make_annual(2023, net_profit=100.0, revenue=1000.0)
    at_threshold = flag_profit_revenue_divergence(make_annual(2024, net_profit=140.0, revenue=1100.0), prior, CONFIG)
    assert at_threshold.state is False
    above = flag_profit_revenue_divergence(make_annual(2024, net_profit=141.0, revenue=1100.0), prior, CONFIG)
    assert above.state is True


def test_rf06_growth_floor_boundary():
    prior = make_annual(2023, net_profit=100.0, revenue=1000.0)
    at_floor = flag_profit_revenue_divergence(make_annual(2024, net_profit=129.0, revenue=1000.0), prior, CONFIG)
    assert at_floor.state is False


# --- RF07 ------------------------------------------------------------------


def test_rf07_boundaries():
    years = [2022, 2023, 2024]
    at_threshold = series(years, net_profit=[100.0] * 3, operating_cash_flow=[100.0, 90.0, 80.0])
    assert flag_cash_conversion_deterioration(at_threshold, CONFIG).state is False
    below = series(years, net_profit=[100.0] * 3, operating_cash_flow=[100.0, 90.0, 79.0])
    assert flag_cash_conversion_deterioration(below, CONFIG).state is True
    flat = series(years, net_profit=[100.0] * 3, operating_cash_flow=[100.0, 90.0, 90.0])
    assert flag_cash_conversion_deterioration(flat, CONFIG).state is False


def test_rf07_requires_full_window():
    rows = series([2023, 2024], net_profit=[100.0, 100.0], operating_cash_flow=[100.0, 50.0])
    result = flag_cash_conversion_deterioration(rows, CONFIG)
    assert result.state is None
    assert result.reason == "insufficient_annual_history"


def test_rf07_nonpositive_profit_is_null():
    rows = series([2022, 2023, 2024], net_profit=[100.0, -1.0, 100.0], operating_cash_flow=[100.0, 90.0, 10.0])
    result = flag_cash_conversion_deterioration(rows, CONFIG)
    assert result.state is None
    assert result.reason == "nonpositive_net_profit_in_window"


# --- industry scope --------------------------------------------------------


def test_financial_industry_detection_by_code_and_name():
    assert is_financial_industry({"industry_code": "801780", "industry_name": ""}, CONFIG) is True
    assert is_financial_industry({"industry_code": "801790", "industry_name": ""}, CONFIG) is True
    assert is_financial_industry({"industry_code": "999999", "industry_name": "保险"}, CONFIG) is True
    assert is_financial_industry({"industry_code": "801080", "industry_name": "电子"}, CONFIG) is False
    assert is_financial_industry(None, CONFIG) is False


def test_industry_known():
    assert industry_known({"industry_code": "801080"}) is True
    assert industry_known({"industry_name": "电子"}) is True
    assert industry_known(None) is False
    assert industry_known({}) is False


# --- orchestration ---------------------------------------------------------


def _clean_series():
    return series(
        [2020, 2021, 2022, 2023, 2024],
        revenue=[1000.0, 1100.0, 1210.0, 1331.0, 1464.1],
        operating_cost=[600.0, 660.0, 726.0, 798.6, 878.46],
        net_profit=[200.0, 220.0, 242.0, 266.2, 292.82],
        operating_cash_flow=[240.0, 264.0, 290.4, 319.44, 351.384],
        accounts_receivable=[150.0, 165.0, 181.5, 199.65, 219.615],
        inventory=[120.0, 132.0, 145.2, 159.72, 175.692],
        total_assets=[1000.0, 1100.0, 1210.0, 1331.0, 1464.1],
    )


def _evaluate(annual, industry=None, config=CONFIG, conflicts=None):
    return evaluate_symbol("600001.SH", annual, industry, "20251231", config, conflicts)


def test_clean_company_is_low_risk_with_full_coverage():
    record = _evaluate(_clean_series(), {"industry_code": "801080", "industry_name": "电子"})
    assert record["status"] == Status.EVALUATED.value
    assert record["risk_level"] == RiskLevel.LOW.value
    assert record["red_flag_count"] == 0
    assert record["available_rule_count"] == 7
    assert record["coverage_ratio"] == 1.0
    assert record["missing_reasons"] == []


def test_financial_company_is_not_applicable():
    record = _evaluate(_clean_series(), {"industry_code": "801780", "industry_name": "银行"})
    assert record["status"] == Status.NOT_APPLICABLE.value
    assert record["risk_level"] == RiskLevel.NOT_APPLICABLE.value
    assert all(state is None for state in record["flags"].values())
    assert record["missing_reasons"] == ["financial_industry_not_applicable"]


def test_missing_industry_fails_closed():
    record = _evaluate(_clean_series(), None)
    assert record["status"] == Status.INSUFFICIENT_DATA.value
    assert "missing_industry" in record["missing_reasons"]


def test_single_annual_report_is_insufficient():
    record = _evaluate(series([2024], revenue=[1000.0]), {"industry_code": "801080"})
    assert record["status"] == Status.INSUFFICIENT_DATA.value
    assert "insufficient_annual_history" in record["missing_reasons"]


def test_conflicting_revision_fails_closed():
    record = _evaluate(_clean_series(), {"industry_code": "801080"}, conflicts={("600001.SH", "2024q4")})
    assert record["status"] == Status.INSUFFICIENT_DATA.value
    assert "conflicting_latest_revisions" in record["missing_reasons"]
    assert record["conflicting_quarters"] == ["2024q4"]


def test_non_contiguous_history_is_recorded():
    annual = _clean_series()
    del annual[3]  # drop 2023, leaving 2020, 2021, 2022, 2024
    record = _evaluate(annual, {"industry_code": "801080"})
    assert "non_contiguous_annual_history" in record["missing_reasons"]
    assert record["flags"]["cash_conversion"] is not None


@pytest.mark.parametrize(
    "mutate,expected_flags,expected_risk",
    [
        (lambda rows: None, 0, RiskLevel.LOW.value),
        (lambda rows: rows[4].update(operating_cash_flow=200.0), 1, RiskLevel.LOW.value),
        (
            lambda rows: (rows[4].update(operating_cash_flow=200.0), rows[4].update(accounts_receivable=600.0)),
            2,
            RiskLevel.MEDIUM.value,
        ),
        (
            lambda rows: (
                rows[4].update(operating_cash_flow=200.0),
                rows[4].update(accounts_receivable=600.0),
                rows[4].update(inventory=800.0),
            ),
            3,
            RiskLevel.MEDIUM.value,
        ),
        (
            lambda rows: (
                rows[4].update(operating_cash_flow=200.0),
                rows[4].update(accounts_receivable=600.0),
                rows[4].update(inventory=800.0),
                rows[4].update(operating_cost=1200.0),
            ),
            4,
            RiskLevel.HIGH.value,
        ),
    ],
)
def test_risk_classification_boundaries(mutate, expected_flags, expected_risk):
    rows = _clean_series()
    mutate(rows)
    record = _evaluate(rows, {"industry_code": "801080"})
    assert record["red_flag_count"] == expected_flags, record["flags"]
    assert record["risk_level"] == expected_risk


def test_insufficient_coverage_fails_closed_even_with_flags():
    # Only RF01 has evidence; the rest are null because the prior year is absent
    # in every metric that needs it, so coverage is far below the 60% floor.
    rows = series([2024], revenue=[1000.0], net_profit=[100.0], operating_cash_flow=[10.0])
    record = _evaluate(rows, {"industry_code": "801080"})
    assert record["coverage_ratio"] < CONFIG.coverage_min
    assert record["status"] == Status.INSUFFICIENT_DATA.value
    assert "insufficient_coverage" in record["missing_reasons"]


def test_flags_are_three_state_only():
    record = _evaluate(_clean_series(), {"industry_code": "801080"})
    assert set(record["flags"]) == set(FLAG_NAMES)
    assert all(value in (True, False, None) for value in record["flags"].values())


def test_evidence_never_fills_missing_with_zero():
    rows = series([2023, 2024], revenue=[1000.0, 1100.0], net_profit=[100.0, 100.0], operating_cash_flow=[100.0, None])
    record = _evaluate(rows, {"industry_code": "801080"})
    assert record["evidence"]["operating_cash_flow"] is None
    assert record["evidence"]["cash_conversion_ratio"] is None
