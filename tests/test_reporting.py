from __future__ import annotations

import json
import sys

import pytest

from accounting_red_flags.providers import FixtureProvider
from accounting_red_flags.reporting import render_report
from accounting_red_flags.service import screen
from scripts import report as report_cli

SYMBOLS = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]


@pytest.fixture()
def result():
    return screen(as_of="20251231", symbols=SYMBOLS, provider=FixtureProvider())


def test_report_renders_summary_triage_and_provenance(result):
    markdown = render_report(result)
    assert "# 会计红旗筛查报告" in markdown
    assert "600002.SH" in markdown  # high risk is in the triage list
    assert "RF01 利润/现金流背离" in markdown
    assert "## 可追溯性" in markdown
    assert result["dataset_version"] in markdown


def test_report_flags_synthetic_data(result):
    assert "requires_live_validation=true" in render_report(result)


def test_min_risk_filters_the_triage_list(result):
    high_only = render_report(result, min_risk="high")
    assert "600002.SH" in high_only
    assert "### 600001.SH" not in high_only

    medium = render_report(result, min_risk="medium")
    assert "### 600002.SH" in medium
    assert "### 600001.SH" not in medium

    everything = render_report(result, min_risk="low")
    assert "### 600002.SH" in everything
    assert "### 600001.SH" in everything


def test_limit_caps_the_triage_list(result):
    markdown = render_report(result, min_risk="low", limit=1)
    assert "### 600002.SH" in markdown
    assert "### 600001.SH" not in markdown
    assert "共 2 只" in markdown


def test_insufficient_records_are_listed_with_reasons(result):
    markdown = render_report(result)
    assert "## 证据不足" in markdown
    assert "600004.SH" in markdown
    assert "insufficient_annual_history" in markdown


def test_financial_records_are_not_in_the_triage_list(result):
    markdown = render_report(result, min_risk="low")
    assert "### 600003.SH" not in markdown


def test_missing_values_render_as_dash_not_zero():
    synthetic = {
        "as_of": "20251231",
        "dataset_version": "20251231-1.3.0-deadbeef",
        "data_source": "PandaData",
        "requires_live_validation": False,
        "universe_size": 1,
        "counts": {"high": 1, "medium": 0, "low": 0, "insufficient_data": 0, "not_applicable": 0},
        "status_counts": {"evaluated": 1, "insufficient_data": 0, "not_applicable": 0},
        "diagnostics": {},
        "records": [
            {
                "symbol": "600000.SH",
                "status": "evaluated",
                "risk_level": "high",
                "red_flag_count": 1,
                "coverage_ratio": 1.0,
                "industry": {"industry_code": "801080", "industry_name": "电子"},
                "flag_details": {
                    "cash_conversion": {"state": True, "value": None, "threshold": 0.8, "reason": None},
                    "accrual_quality": {"state": None, "value": None, "threshold": None, "reason": "missing_accrual_evidence"},
                },
                "evidence": {"net_profit": None, "operating_cash_flow": 10.0},
                "peer_context": None,
                "missing_reasons": ["missing_accrual_evidence"],
            }
        ],
    }
    markdown = render_report(synthetic, min_risk="high")
    # A None value/threshold renders as an explicit dash, never a spurious zero.
    assert "—" in markdown
    assert "经营现金流 10" in markdown
    # None evidence is omitted entirely rather than being shown as 0.
    assert "净利润" not in markdown
    assert "净利润 0" not in markdown


def test_gross_margin_shows_absolute_value():
    synthetic = {
        "as_of": "20251231",
        "counts": {},
        "status_counts": {"evaluated": 1},
        "diagnostics": {},
        "records": [
            {
                "symbol": "600000.SH",
                "status": "evaluated",
                "risk_level": "high",
                "red_flag_count": 1,
                "coverage_ratio": 1.0,
                "industry": None,
                "flag_details": {
                    "gross_margin_anomaly": {"state": True, "value": -0.1, "threshold": 0.05, "reason": None},
                },
                "evidence": {},
                "missing_reasons": [],
            }
        ],
    }
    assert "|-0.1| vs 阈值 0.05" in render_report(synthetic, min_risk="high")


def test_peer_context_is_shown_when_present():
    synthetic = {
        "as_of": "20251231",
        "counts": {},
        "status_counts": {"evaluated": 1},
        "diagnostics": {},
        "records": [
            {
                "symbol": "600000.SH",
                "status": "evaluated",
                "risk_level": "high",
                "red_flag_count": 1,
                "coverage_ratio": 1.0,
                "industry": None,
                "flag_details": {},
                "evidence": {},
                "peer_context": {
                    "industry_code": "801080",
                    "metrics": {"receivable_gap": {"peer_count": 42, "peer_median": 0.08, "peer_percentile": 0.93}},
                },
                "missing_reasons": [],
            }
        ],
    }
    markdown = render_report(synthetic, min_risk="high")
    assert "行业相对分位" in markdown
    assert "93.0%" in markdown


def test_invalid_min_risk_is_rejected(result):
    with pytest.raises(ValueError, match="min_risk"):
        render_report(result, min_risk="nonsense")


def test_report_is_deterministic(result):
    assert render_report(result) == render_report(result)


def test_cli_writes_validated_report(tmp_path, result):
    source = tmp_path / "result.json"
    source.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "report.md"
    argv = sys.argv
    sys.argv = ["report.py", str(source), "--min-risk", "high", "--output", str(output)]
    try:
        assert report_cli.main() == 0
    finally:
        sys.argv = argv
    assert "会计红旗筛查报告" in output.read_text(encoding="utf-8")


def test_cli_refuses_unvalidated_result(tmp_path, result):
    tampered = json.loads(json.dumps(result))
    tampered["records"][0]["red_flag_count"] = 99
    source = tmp_path / "tampered.json"
    source.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    argv = sys.argv
    sys.argv = ["report.py", str(source)]
    try:
        assert report_cli.main() == 1
    finally:
        sys.argv = argv
