from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from accounting_red_flags.config import RULES_VERSION, SCHEMA_VERSION, config_hash, load_rule_config
from accounting_red_flags.providers import FixtureProvider
from accounting_red_flags.service import screen, validate_as_of, validate_symbols
from scripts.validate import validate_result

SYMBOLS = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]


@pytest.fixture()
def result():
    return screen(as_of="20251231", symbols=SYMBOLS, provider=FixtureProvider())


def test_screen_produces_valid_auditable_result(result):
    assert validate_result(result)["status"] == "PASS"


def test_counts_match_records(result):
    assert result["universe_size"] == len(result["records"]) == len(SYMBOLS)
    assert result["counts"] == {
        "low": 1,
        "medium": 0,
        "high": 1,
        "insufficient_data": 1,
        "not_applicable": 1,
    }
    assert result["status_counts"] == {"evaluated": 2, "insufficient_data": 1, "not_applicable": 1}


def test_expected_risk_assignment(result):
    by_symbol = {record["symbol"]: record for record in result["records"]}
    assert by_symbol["600001.SH"]["risk_level"] == "low"
    assert by_symbol["600002.SH"]["risk_level"] == "high"
    assert by_symbol["600002.SH"]["red_flag_count"] == 7
    assert by_symbol["600003.SH"]["status"] == "not_applicable"
    assert by_symbol["600004.SH"]["status"] == "insufficient_data"


def test_metadata_is_internally_consistent(result):
    thresholds = result["thresholds"]
    expected_config_hash = hashlib.sha256(
        __import__("json").dumps(thresholds, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert result["rule_config_hash"] == expected_config_hash
    assert result["rule_config_hash"] == config_hash(load_rule_config())
    assert result["rules_version"] == RULES_VERSION
    assert result["schema_version"] == SCHEMA_VERSION
    expected_universe = hashlib.sha256(
        "\n".join(sorted(SYMBOLS)).encode("utf-8")
    ).hexdigest()
    assert result["universe_hash"] == expected_universe
    assert result["dataset_version"].startswith(f"20251231-{SCHEMA_VERSION}-")


def test_fixture_data_is_flagged_as_requiring_live_validation(result):
    assert result["requires_live_validation"] is True
    assert "synthetic" in result["data_source"].lower()


def test_runs_are_reproducible_except_for_run_id(result):
    again = screen(as_of="20251231", symbols=SYMBOLS, provider=FixtureProvider())
    assert result["dataset_version"] == again["dataset_version"]
    assert result["run_id"] != again["run_id"]
    assert [record["symbol"] for record in result["records"]] == [
        record["symbol"] for record in again["records"]
    ]


def test_dataset_version_binds_rules_version(monkeypatch):
    from accounting_red_flags import service as service_module

    first = screen(as_of="20251231", symbols=SYMBOLS, provider=FixtureProvider())
    monkeypatch.setattr(service_module, "RULES_VERSION", "9.9.9")
    second = screen(as_of="20251231", symbols=SYMBOLS, provider=FixtureProvider())
    assert second["rules_version"] == "9.9.9"
    assert second["dataset_version"] != first["dataset_version"]
    assert second["dataset_version"].startswith(f"20251231-{SCHEMA_VERSION}-")


def test_all_a_uses_provider_universe():
    result = screen(as_of="20251231", all_a=True, provider=FixtureProvider())
    assert result["universe_size"] == 4
    assert validate_result(result)["status"] == "PASS"


def test_symbols_are_sorted_and_deduplicated():
    result = screen(
        as_of="20251231",
        symbols=["600002.SH", "600001.SH", "600002.SH"],
        provider=FixtureProvider(),
    )
    assert [record["symbol"] for record in result["records"]] == ["600002.SH", "600001.SH"]


def test_mutually_exclusive_inputs():
    with pytest.raises(ValueError, match="either symbols or all_a"):
        screen(as_of="20251231", symbols=SYMBOLS, all_a=True, provider=FixtureProvider())
    with pytest.raises(ValueError, match="either symbols or all_a"):
        screen(as_of="20251231", provider=FixtureProvider())


@pytest.mark.parametrize("value", ["2025123", "", "20251332", "abcdefgh"])
def test_invalid_as_of_rejected(value):
    with pytest.raises(ValueError):
        validate_as_of(value)


def test_valid_as_of_normalized():
    assert validate_as_of("2025-12-31") == "20251231"


def test_invalid_symbol_rejected():
    with pytest.raises(ValueError, match="unsupported A-share symbols"):
        validate_symbols(["600001.SH", "AAPL"])


def test_diagnostics_cover_every_flag(result):
    diagnostics = result["diagnostics"]
    assert set(diagnostics["flag_trigger_counts"]) == {
        "cash_conversion",
        "receivable_divergence",
        "inventory_divergence",
        "accrual_quality",
        "gross_margin_anomaly",
        "profit_revenue_divergence",
        "cash_conversion_deterioration",
    }
    assert diagnostics["flag_trigger_counts"]["cash_conversion"] == 2
    assert diagnostics["flag_available_counts"]["receivable_divergence"] == 2
    assert diagnostics["financial_excluded"] == 1


# --- peer-relative context (additive cross-sectional evidence) ---------------

_PEER_SYMBOLS = [f"60010{i}.SH" for i in range(6)]


class _PeerProvider(FixtureProvider):
    """Six electronics names whose receivables gap spans the industry."""

    def fetch_reports(self, symbols, as_of, years=8):
        rows = []
        for index, symbol in enumerate(_PEER_SYMBOLS):
            receivable = 100.0
            for year in (2022, 2023, 2024):
                rows.append(
                    {
                        "symbol": symbol,
                        "quarter": f"{year}q4",
                        "date": f"{year + 1}0430",
                        "if_adjusted": 0,
                        "is_revenue": 1000.0,
                        "is_oper_cost": 600.0,
                        "is_n_income_attr_p": 100.0,
                        "cfs_net_cash_operating": 120.0,
                        "bs_net_accts_receive": receivable,
                        "bs_inventory": 100.0,
                        "bs_total_assets": 1000.0,
                    }
                )
                receivable *= 1.05 + 0.10 * index
        frame = pd.DataFrame(rows)
        return frame[frame["symbol"].isin(symbols)].reset_index(drop=True)

    def fetch_industries(self, symbols, as_of):
        return {
            symbol: {"industry_code": "801080", "industry_name": "电子"}
            for symbol in symbols
            if symbol in _PEER_SYMBOLS
        }


def test_peer_context_is_reported_and_validated():
    result = screen(as_of="20251231", symbols=_PEER_SYMBOLS, provider=_PeerProvider())
    assert validate_result(result)["status"] == "PASS"
    by_symbol = {record["symbol"]: record for record in result["records"]}
    gap = by_symbol["600105.SH"]["peer_context"]["metrics"]["receivable_gap"]
    assert by_symbol["600105.SH"]["peer_context"]["industry_code"] == "801080"
    assert gap["peer_count"] == 6
    assert gap["peer_median"] == pytest.approx(0.30)
    assert gap["peer_percentile"] == pytest.approx(5 / 6)


def test_peer_context_never_changes_a_flag():
    result = screen(as_of="20251231", symbols=_PEER_SYMBOLS, provider=_PeerProvider())
    by_symbol = {record["symbol"]: record for record in result["records"]}
    # Highest peer percentile still triggers the absolute rule; lowest still clears.
    assert by_symbol["600105.SH"]["flag_details"]["receivable_divergence"]["state"] is True
    assert by_symbol["600100.SH"]["flag_details"]["receivable_divergence"]["state"] is False


def test_peer_context_is_empty_when_industry_sample_is_small(result):
    for record in result["records"]:
        if record["status"] == "not_applicable":
            assert record["peer_context"] is None
        else:
            assert record["peer_context"]["metrics"] == {}
