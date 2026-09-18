"""Strict validator for JSON results and production Parquet.

The validator re-derives every aggregate and every classification from the
per-record evidence. If a result was hand-edited, truncated or produced by a
different rule version, validation fails with a readable error list.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounting_red_flags.config import RULES_VERSION, SCHEMA_VERSION
from accounting_red_flags.materialization.parquet_writer import PRODUCTION_KEY, signal_for
from accounting_red_flags.models import FLAG_NAMES, RiskLevel, Status

RISK_LEVELS = {
    RiskLevel.LOW.value,
    RiskLevel.MEDIUM.value,
    RiskLevel.HIGH.value,
    RiskLevel.INSUFFICIENT_DATA.value,
    RiskLevel.NOT_APPLICABLE.value,
}
STATUSES = {Status.EVALUATED.value, Status.INSUFFICIENT_DATA.value, Status.NOT_APPLICABLE.value}
REQUIRED_METADATA = {
    "run_id",
    "dataset_version",
    "rule_config_hash",
    "source_snapshot",
    "data_sdk_version",
    "runtime_versions",
    "universe_hash",
    "source_response_count",
    "rules_version",
    "schema_version",
    "diagnostics",
    "thresholds",
    "counts",
    "status_counts",
    "data_source",
}
REQUIRED_EVIDENCE_KEYS = {
    "net_profit",
    "operating_cash_flow",
    "cash_conversion_ratio",
    "receivable_growth",
    "revenue_growth",
    "inventory_growth",
    "accrual_ratio",
}


def _valid_date(value) -> bool:
    try:
        datetime.strptime(str(value), "%Y%m%d")
        return True
    except (TypeError, ValueError):
        return False


def _classify(red_flag_count: int, coverage: float, thresholds: dict) -> str:
    if coverage < float(thresholds.get("coverage_min", 0.6)):
        return RiskLevel.INSUFFICIENT_DATA.value
    if red_flag_count >= int(thresholds.get("risk_high_flags", 4)):
        return RiskLevel.HIGH.value
    if red_flag_count >= int(thresholds.get("risk_medium_flags", 2)):
        return RiskLevel.MEDIUM.value
    return RiskLevel.LOW.value


def validate_result(result: dict) -> dict:
    errors: list[str] = []
    as_of = str(result.get("as_of", ""))
    if not _valid_date(as_of):
        errors.append("invalid result as_of")
    missing_metadata = sorted(REQUIRED_METADATA - set(result))
    if missing_metadata:
        errors.append(f"missing metadata {missing_metadata}")

    records = result.get("records", [])
    if not isinstance(records, list) or not records:
        errors.append("records must be a non-empty list")
        return {"status": "FAIL", "errors": errors, "record_count": 0}

    symbols = [record.get("symbol") for record in records]
    if len(symbols) != len(set(symbols)):
        errors.append("duplicate record symbols")
    if result.get("universe_size") != len(records):
        errors.append("universe_size does not match records")
    if result.get("rules_version") != RULES_VERSION:
        errors.append("rules_version does not match runtime")
    if result.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version does not match runtime")

    thresholds = result.get("thresholds", {})
    expected_config_hash = hashlib.sha256(
        json.dumps(thresholds, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if result.get("rule_config_hash") != expected_config_hash:
        errors.append("rule_config_hash does not match thresholds")
    expected_universe_hash = hashlib.sha256(
        "\n".join(sorted(str(symbol) for symbol in symbols)).encode("utf-8")
    ).hexdigest()
    if result.get("universe_hash") != expected_universe_hash:
        errors.append("universe_hash does not match records")
    expected_dataset_hash = hashlib.sha256(
        f"{as_of}:{SCHEMA_VERSION}:{expected_config_hash}:{expected_universe_hash}:{result.get('source_snapshot', '')}".encode("utf-8")
    ).hexdigest()
    expected_dataset_version = f"{as_of}-{SCHEMA_VERSION}-{expected_dataset_hash[:16]}"
    if result.get("dataset_version") != expected_dataset_version:
        errors.append("dataset_version does not match inputs")

    expected_counts = {level: 0 for level in ("low", "medium", "high", "insufficient_data", "not_applicable")}
    expected_status = {status: 0 for status in ("evaluated", "insufficient_data", "not_applicable")}
    for record in records:
        risk = record.get("risk_level")
        status = record.get("status")
        if risk in expected_counts:
            expected_counts[risk] += 1
        if status in expected_status:
            expected_status[status] += 1
    if result.get("counts") != expected_counts:
        errors.append("counts do not match records")
    if result.get("status_counts") != expected_status:
        errors.append("status_counts do not match records")

    expected_high = [r.get("symbol") for r in records if r.get("risk_level") == RiskLevel.HIGH.value]
    expected_medium = [r.get("symbol") for r in records if r.get("risk_level") == RiskLevel.MEDIUM.value]
    if result.get("high_risk_symbols") != expected_high:
        errors.append("high_risk_symbols do not match records")
    if result.get("medium_risk_symbols") != expected_medium:
        errors.append("medium_risk_symbols do not match records")

    diagnostics = result.get("diagnostics", {})
    expected_reasons = dict(sorted(Counter(
        reason for record in records for reason in record.get("missing_reasons", [])
    ).items()))
    if diagnostics.get("insufficient_reason_counts") != expected_reasons:
        errors.append("diagnostic insufficient reasons do not match records")
    expected_triggers = {
        name: sum(record.get("flags", {}).get(name) is True for record in records) for name in FLAG_NAMES
    }
    if diagnostics.get("flag_trigger_counts") != expected_triggers:
        errors.append("diagnostic flag trigger counts do not match records")
    expected_available = {
        name: sum(record.get("flags", {}).get(name) is not None for record in records) for name in FLAG_NAMES
    }
    if diagnostics.get("flag_available_counts") != expected_available:
        errors.append("diagnostic flag availability counts do not match records")

    for record in records:
        symbol = record.get("symbol")
        status = record.get("status")
        risk = record.get("risk_level")
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", str(symbol or "")):
            errors.append(f"{symbol}: invalid symbol")
        if status not in STATUSES:
            errors.append(f"{symbol}: invalid status {status}")
        if risk not in RISK_LEVELS:
            errors.append(f"{symbol}: invalid risk_level {risk}")
        flags = record.get("flags", {})
        if set(flags) != set(FLAG_NAMES):
            errors.append(f"{symbol}: flags keys do not match the rule set")
            continue
        if any(value not in (True, False, None) for value in flags.values()):
            errors.append(f"{symbol}: flag state must be true/false/null")
        triggered = sum(value is True for value in flags.values())
        available = sum(value is not None for value in flags.values())
        coverage = available / len(FLAG_NAMES)
        if record.get("red_flag_count") != triggered:
            errors.append(f"{symbol}: red_flag_count does not match flags")
        if record.get("available_rule_count") != available:
            errors.append(f"{symbol}: available_rule_count does not match flags")
        if abs(float(record.get("coverage_ratio", -1)) - coverage) > 1e-9:
            errors.append(f"{symbol}: coverage_ratio does not match flags")

        if status == Status.EVALUATED.value:
            if risk not in {RiskLevel.LOW.value, RiskLevel.MEDIUM.value, RiskLevel.HIGH.value}:
                errors.append(f"{symbol}: evaluated status with risk {risk}")
            else:
                expected_risk = _classify(triggered, coverage, thresholds)
                if risk != expected_risk:
                    errors.append(f"{symbol}: risk_level {risk} != re-derived {expected_risk}")
        elif status == Status.INSUFFICIENT_DATA.value:
            if risk != RiskLevel.INSUFFICIENT_DATA.value:
                errors.append(f"{symbol}: insufficient status with risk {risk}")
            if not record.get("missing_reasons"):
                errors.append(f"{symbol}: insufficient_data without reasons")
        elif status == Status.NOT_APPLICABLE.value and risk != RiskLevel.NOT_APPLICABLE.value:
            errors.append(f"{symbol}: not_applicable status with risk {risk}")

        announcements = record.get("announcement_dates", [])
        invalid = [date for date in announcements if not _valid_date(date)]
        future = [date for date in announcements if _valid_date(date) and str(date) > as_of]
        if invalid:
            errors.append(f"{symbol}: invalid announcement dates {invalid}")
        if future:
            errors.append(f"{symbol}: future announcement dates {future}")
        for version in record.get("report_versions", []):
            date = version.get("announce_date")
            if not _valid_date(date):
                errors.append(f"{symbol}: invalid report version date {date}")
            elif str(date) > as_of:
                errors.append(f"{symbol}: future report version date {date}")
        if REQUIRED_EVIDENCE_KEYS - set(record.get("evidence", {})):
            errors.append(f"{symbol}: missing required evidence keys")
        if record.get("rule_version") != RULES_VERSION:
            errors.append(f"{symbol}: rule_version does not match runtime")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "record_count": len(records)}


def validate_production(frame: pd.DataFrame) -> dict:
    errors: list[str] = []
    required = {
        "trade_date",
        "factor_id",
        "symbol",
        "signal",
        "risk_level",
        "status",
        "evidence_json",
        "run_metadata_json",
        "data_version",
        "rules_version",
        "rule_config_hash",
        "run_id",
        "source_snapshot",
        "data_sdk_version",
        "runtime_versions_json",
        "schema_version",
        "red_flag_count",
        "coverage_ratio",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        return {"status": "FAIL", "errors": [f"missing production columns {missing}"], "record_count": len(frame)}
    if frame.duplicated(PRODUCTION_KEY).any():
        errors.append("duplicate production keys")
    if not frame["schema_version"].eq(SCHEMA_VERSION).all():
        errors.append("production schema_version does not match runtime")
    if not frame["rules_version"].eq(RULES_VERSION).all():
        errors.append("production rules_version does not match runtime")
    for row in frame.itertuples(index=False):
        try:
            evidence = json.loads(row.evidence_json)
            metadata = json.loads(row.run_metadata_json)
            runtime = json.loads(row.runtime_versions_json)
        except (TypeError, json.JSONDecodeError):
            errors.append(f"{row.symbol}: invalid JSON evidence or metadata")
            continue
        if evidence.get("symbol") != row.symbol or evidence.get("status") != row.status:
            errors.append(f"{row.symbol}: row and evidence mismatch")
        if evidence.get("risk_level") != row.risk_level:
            errors.append(f"{row.symbol}: risk_level mismatch between row and evidence")
        if row.signal != signal_for(row.risk_level):
            errors.append(f"{row.symbol}: signal does not match risk_level")
        if evidence.get("red_flag_count") != row.red_flag_count:
            errors.append(f"{row.symbol}: red_flag_count mismatch")
        for key, value in (
            ("run_id", row.run_id),
            ("dataset_version", row.data_version),
            ("rule_config_hash", row.rule_config_hash),
            ("source_snapshot", row.source_snapshot),
        ):
            if metadata.get(key) != value:
                errors.append(f"{row.symbol}: {key} metadata mismatch")
        if runtime.get("panda_data") != row.data_sdk_version:
            errors.append(f"{row.symbol}: runtime SDK metadata mismatch")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "record_count": len(frame)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a red-flag JSON or Parquet result")
    parser.add_argument("result")
    args = parser.parse_args()
    path = Path(args.result)
    if path.suffix.lower() == ".parquet":
        report = validate_production(pd.read_parquet(path))
    else:
        report = validate_result(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
