from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from accounting_red_flags.config import SCHEMA_VERSION
from accounting_red_flags.materialization import production_frame, signal_for, write_production
from accounting_red_flags.materialization.parquet_writer import PRODUCTION_KEY
from accounting_red_flags.providers import FixtureProvider
from accounting_red_flags.service import screen
from scripts.validate import validate_production, validate_result

SYMBOLS = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]


@pytest.fixture()
def result():
    return screen(as_of="20251231", symbols=SYMBOLS, provider=FixtureProvider())


def test_production_frame_shape_and_key(result):
    frame = production_frame(result)
    assert len(frame) == len(SYMBOLS)
    assert not frame.duplicated(PRODUCTION_KEY).any()
    assert set(["trade_date", "symbol", "factor_id", "signal", "evidence_json"]).issubset(frame.columns)
    assert frame["trade_date"].eq("20251231").all()
    assert frame["schema_version"].eq(SCHEMA_VERSION).all()


def test_signal_mapping(result):
    frame = production_frame(result).set_index("symbol")
    assert frame.loc["600002.SH", "signal"] == "review"
    assert frame.loc["600001.SH", "signal"] == "clear"
    assert frame.loc["600003.SH", "signal"] == "not_applicable"
    assert frame.loc["600004.SH", "signal"] == "unknown"
    assert signal_for("medium") == "watch"
    assert signal_for("nonsense") == "unknown"


def test_non_evaluated_rows_have_no_factor_value_or_rank(result):
    frame = production_frame(result).set_index("symbol")
    assert pd.isna(frame.loc["600003.SH", "factor_value"])
    assert pd.isna(frame.loc["600003.SH", "rank"])
    assert frame.loc["600002.SH", "factor_value"] == 7
    assert frame.loc["600002.SH", "rank"] == 1


def test_evidence_json_round_trips(result):
    frame = production_frame(result)
    row = frame[frame["symbol"] == "600002.SH"].iloc[0]
    evidence = json.loads(row["evidence_json"])
    assert evidence["symbol"] == "600002.SH"
    assert evidence["red_flag_count"] == row["red_flag_count"]
    metadata = json.loads(row["run_metadata_json"])
    assert metadata["dataset_version"] == row["data_version"]


def test_write_and_validate_production(tmp_path, result):
    path = tmp_path / "database.parquet"
    write_production(production_frame(result), path)
    frame = pd.read_parquet(path)
    assert validate_production(frame)["status"] == "PASS"


def test_write_production_upserts_by_key(tmp_path, result):
    path = tmp_path / "database.parquet"
    write_production(production_frame(result), path)
    second = screen(as_of="20241231", symbols=SYMBOLS, provider=FixtureProvider())
    write_production(production_frame(second), path)
    frame = pd.read_parquet(path)
    assert len(frame) == 2 * len(SYMBOLS)
    assert set(frame["trade_date"]) == {"20241231", "20251231"}
    assert not frame.duplicated(PRODUCTION_KEY).any()


def test_write_production_replaces_same_key(tmp_path, result):
    path = tmp_path / "database.parquet"
    write_production(production_frame(result), path)
    write_production(production_frame(result), path)
    frame = pd.read_parquet(path)
    assert len(frame) == len(SYMBOLS)


def test_production_frame_rejects_empty_records(result):
    empty = dict(result)
    empty["records"] = []
    with pytest.raises(ValueError):
        production_frame(empty)


def test_validator_catches_tampered_flag_count(result):
    tampered = copy.deepcopy(result)
    tampered["records"][0]["red_flag_count"] = 99
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("red_flag_count" in error for error in report["errors"])


def test_validator_catches_tampered_risk_level(result):
    tampered = copy.deepcopy(result)
    high = next(record for record in tampered["records"] if record["symbol"] == "600002.SH")
    high["risk_level"] = "low"
    report = validate_result(tampered)
    assert report["status"] == "FAIL"


def test_validator_catches_future_announcement(result):
    tampered = copy.deepcopy(result)
    tampered["records"][0]["announcement_dates"] = ["20990101"]
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("future announcement" in error for error in report["errors"])


def test_validator_catches_missing_flag_key(result):
    tampered = copy.deepcopy(result)
    del tampered["records"][0]["flags"]["cash_conversion"]
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("flags keys" in error for error in report["errors"])


def test_validator_catches_threshold_tampering(result):
    tampered = copy.deepcopy(result)
    tampered["thresholds"]["coverage_min"] = 0.0
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("rule_config_hash" in error for error in report["errors"])


def test_validator_catches_dataset_version_mismatch(result):
    tampered = copy.deepcopy(result)
    tampered["dataset_version"] = "20251231-1.0.0-deadbeef"
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("dataset_version" in error for error in report["errors"])


def test_validator_catches_duplicate_symbols(result):
    tampered = copy.deepcopy(result)
    tampered["records"][1]["symbol"] = tampered["records"][0]["symbol"]
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("duplicate" in error for error in report["errors"])


def test_validate_production_catches_signal_mismatch(tmp_path, result):
    frame = production_frame(result)
    frame.loc[frame["symbol"] == "600002.SH", "signal"] = "clear"
    report = validate_production(frame)
    assert report["status"] == "FAIL"
    assert any("signal" in error for error in report["errors"])


def test_validate_production_catches_bad_json(tmp_path, result):
    frame = production_frame(result)
    frame.loc[0, "evidence_json"] = "{not json"
    report = validate_production(frame)
    assert report["status"] == "FAIL"
    assert any("invalid JSON" in error for error in report["errors"])


def test_validate_production_catches_missing_columns(result):
    frame = production_frame(result).drop(columns=["evidence_json"])
    report = validate_production(frame)
    assert report["status"] == "FAIL"
    assert any("missing production columns" in error for error in report["errors"])


# --- v1.1.0 integrity regressions ------------------------------------------


def test_validator_catches_tampered_diagnostics(result):
    for key, value in (
        ("risk_level_counts", {"low": 999}),
        ("industry_coverage", 999),
        ("evaluated_coverage_mean", 999.0),
    ):
        tampered = copy.deepcopy(result)
        tampered["diagnostics"][key] = value
        report = validate_result(tampered)
        assert report["status"] == "FAIL", key
        assert any("diagnostic" in error for error in report["errors"]), key


def test_validator_catches_tampered_flag_details(result):
    tampered = copy.deepcopy(result)
    record = next(item for item in tampered["records"] if item["symbol"] == "600002.SH")
    record["flag_details"]["cash_conversion"]["value"] = 0.99
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("flag_details" in error for error in report["errors"])


def test_validator_catches_tampered_annual_history(result):
    tampered = copy.deepcopy(result)
    record = next(item for item in tampered["records"] if item["symbol"] == "600002.SH")
    record["annual_history"][-1]["revenue"] = 1.0
    report = validate_result(tampered)
    assert report["status"] == "FAIL"


def test_validator_catches_inconsistent_live_source(result):
    tampered = copy.deepcopy(result)
    tampered["data_source"] = "PandaData"
    report = validate_result(tampered)
    assert report["status"] == "FAIL"
    assert any("requires_live_validation" in error for error in report["errors"])


def test_validate_production_recomputes_row_metrics(result):
    for column, value in (
        ("score", 0.0),
        ("rank", 99),
        ("confidence", 0.0),
        ("factor_value", 0),
    ):
        frame = production_frame(result)
        frame[column] = value
        report = validate_production(frame)
        assert report["status"] == "FAIL", column


def test_validate_production_rejects_rewritten_evidence(tmp_path, result):
    frame = production_frame(result)
    row_index = frame.index[frame["symbol"] == "600002.SH"][0]
    evidence = json.loads(frame.loc[row_index, "evidence_json"])
    evidence["red_flag_count"] = 0
    frame.loc[row_index, "red_flag_count"] = 0
    frame.loc[row_index, "evidence_json"] = json.dumps(evidence)
    report = validate_production(frame)
    assert report["status"] == "FAIL"
