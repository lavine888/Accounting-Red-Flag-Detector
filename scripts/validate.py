"""Strict validator for JSON results and production Parquet.

The validator re-derives every aggregate and every classification from the
per-record evidence. If a result was hand-edited, truncated or produced by a
different rule version, validation fails with a readable error list.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import fields
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounting_red_flags.config import RULES_VERSION, SCHEMA_VERSION, RuleConfig
from accounting_red_flags.cross_section import peer_contexts
from accounting_red_flags.materialization.parquet_writer import PRODUCTION_KEY, signal_for
from accounting_red_flags.models import FLAG_NAMES, RiskLevel, Status
from accounting_red_flags.rules import evaluate_symbol

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


# Every field the engine derives for one company. The validator re-runs the
# engine over the record's own ``annual_history`` and compares all of them, so
# tampering with a flag, a detail, coverage or a risk level cannot survive even
# if the top-level aggregates are edited to match.
RECORD_COMPARE_KEYS = (
    "symbol",
    "as_of",
    "status",
    "risk_level",
    "is_financial",
    "industry",
    "red_flag_count",
    "available_rule_count",
    "total_rule_count",
    "coverage_ratio",
    "flags",
    "flag_details",
    "evidence",
    "latest_fiscal_year",
    "previous_fiscal_year",
    "annual_history",
    "announcement_dates",
    "report_periods",
    "report_versions",
    "conflicting_quarters",
    "missing_reasons",
)


def _rule_config(thresholds: dict) -> RuleConfig:
    allowed = {field.name for field in fields(RuleConfig)}
    payload = {key: value for key, value in thresholds.items() if key in allowed}
    if "excluded_industry_codes" in payload:
        payload["excluded_industry_codes"] = tuple(payload["excluded_industry_codes"])
    return RuleConfig(**payload)


def _recompute_record(record: dict, as_of: str, config: RuleConfig) -> dict:
    conflicts = {(record.get("symbol"), quarter) for quarter in record.get("conflicting_quarters", [])}
    return evaluate_symbol(
        record.get("symbol", ""),
        list(record.get("annual_history", [])),
        record.get("industry"),
        as_of,
        config,
        conflicts,
    )


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
    recompute_config: RuleConfig | None
    try:
        recompute_config = _rule_config(thresholds)
    except (TypeError, ValueError) as exc:
        errors.append(f"thresholds cannot build a rule config: {exc}")
        recompute_config = None
    expected_universe_hash = hashlib.sha256(
        "\n".join(sorted(str(symbol) for symbol in symbols)).encode("utf-8")
    ).hexdigest()
    if result.get("universe_hash") != expected_universe_hash:
        errors.append("universe_hash does not match records")
    expected_dataset_hash = hashlib.sha256(
        f"{as_of}:{SCHEMA_VERSION}:{RULES_VERSION}:{expected_config_hash}:{expected_universe_hash}:{result.get('source_snapshot', '')}".encode("utf-8")
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
    expected_risk_counts = dict(sorted(Counter(record.get("risk_level") for record in records).items()))
    if diagnostics.get("risk_level_counts") != expected_risk_counts:
        errors.append("diagnostic risk level counts do not match records")
    expected_industry_coverage = sum(bool(record.get("industry")) for record in records)
    if diagnostics.get("industry_coverage") != expected_industry_coverage:
        errors.append("diagnostic industry coverage does not match records")
    expected_financial = sum(record.get("risk_level") == RiskLevel.NOT_APPLICABLE.value for record in records)
    if diagnostics.get("financial_excluded") != expected_financial:
        errors.append("diagnostic financial exclusion count does not match records")
    evaluated_records = [record for record in records if record.get("status") == Status.EVALUATED.value]
    expected_coverage_mean = (
        sum(float(record.get("coverage_ratio", 0.0)) for record in evaluated_records) / len(evaluated_records)
        if evaluated_records
        else None
    )
    if diagnostics.get("evaluated_coverage_mean") != expected_coverage_mean:
        errors.append("diagnostic evaluated coverage mean does not match records")

    requires_live = result.get("requires_live_validation")
    data_source = result.get("data_source")
    if not isinstance(requires_live, bool):
        errors.append("requires_live_validation must be a boolean")
    else:
        # PandaData is the only live source in this contract; any other source
        # must declare that its output still requires live validation.
        live_source = data_source == "PandaData"
        if live_source == requires_live:
            errors.append(
                "data_source and requires_live_validation are inconsistent: "
                "PandaData is the only live source and must set requires_live_validation=false"
            )
    if not str(result.get("source_snapshot", "")):
        errors.append("source_snapshot must be non-empty")

    derived_records: list[dict] = []
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
        if recompute_config is not None:
            try:
                derived = _recompute_record(record, as_of, recompute_config)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                errors.append(f"{symbol}: recomputation failed: {type(exc).__name__}: {exc}")
            else:
                derived_records.append(derived)
                for key in RECORD_COMPARE_KEYS:
                    if derived.get(key) != record.get(key):
                        errors.append(f"{symbol}: {key} does not match recomputed evidence")
    if recompute_config is not None and len(derived_records) == len(records):
        # Peer context is derived from the *recomputed* evidence, so a tampered
        # peer block fails even if the top-level aggregates were edited to match.
        expected_contexts = peer_contexts(
            derived_records, min_sample=recompute_config.peer_min_sample
        )
        for record in records:
            symbol = record.get("symbol")
            if record.get("peer_context") != expected_contexts.get(symbol):
                errors.append(f"{symbol}: peer_context does not match recomputed peer statistics")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "record_count": len(records)}


def _close(actual, expected, tolerance: float = 1e-9) -> bool:
    try:
        left = float(actual)
        right = float(expected)
    except (TypeError, ValueError):
        return False
    if math.isnan(right):
        return math.isnan(left)
    if math.isnan(left):
        return False
    return abs(left - right) <= tolerance


def _expected_rank(records: list[dict]) -> dict[str, int]:
    ranked = sorted(
        records,
        key=lambda record: (
            record.get("status") != Status.EVALUATED.value,
            -int(record.get("red_flag_count", 0) or 0),
            str(record.get("symbol")),
        ),
    )
    return {str(record.get("symbol")): index + 1 for index, record in enumerate(ranked)}


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
        "available_rule_count",
        "coverage_ratio",
        "factor_value",
        "score",
        "rank",
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

    for trade_date, group in frame.groupby("trade_date", sort=False):
        records: list[dict] = []
        metadata: dict | None = None
        runtime: dict | None = None
        for row in group.itertuples(index=False):
            try:
                evidence = json.loads(row.evidence_json)
                row_metadata = json.loads(row.run_metadata_json)
                row_runtime = json.loads(row.runtime_versions_json)
            except (TypeError, json.JSONDecodeError):
                errors.append(f"{trade_date}/{row.symbol}: invalid JSON evidence or metadata")
                continue
            records.append(evidence)
            if metadata is None:
                metadata, runtime = row_metadata, row_runtime
            elif row_metadata != metadata:
                errors.append(f"{trade_date}/{row.symbol}: run metadata differs within a trade date")
            if row_metadata.get("as_of") != str(trade_date):
                errors.append(f"{trade_date}/{row.symbol}: trade_date does not match run metadata as_of")
            if evidence.get("symbol") != row.symbol or evidence.get("status") != row.status:
                errors.append(f"{trade_date}/{row.symbol}: row and evidence mismatch")
            if evidence.get("risk_level") != row.risk_level:
                errors.append(f"{trade_date}/{row.symbol}: risk_level mismatch between row and evidence")
            if row.signal != signal_for(row.risk_level):
                errors.append(f"{trade_date}/{row.symbol}: signal does not match risk_level")
            if evidence.get("red_flag_count") != row.red_flag_count:
                errors.append(f"{trade_date}/{row.symbol}: red_flag_count mismatch")
            if evidence.get("available_rule_count") != row.available_rule_count:
                errors.append(f"{trade_date}/{row.symbol}: available_rule_count mismatch")
            if not _close(evidence.get("coverage_ratio"), row.coverage_ratio):
                errors.append(f"{trade_date}/{row.symbol}: coverage_ratio mismatch")
            if not _close(row.confidence, evidence.get("coverage_ratio")):
                errors.append(f"{trade_date}/{row.symbol}: confidence is not the evidence coverage ratio")

            evaluated = evidence.get("status") == Status.EVALUATED.value
            available = int(evidence.get("available_rule_count") or 0)
            triggered = int(evidence.get("red_flag_count") or 0)
            if evaluated:
                if row.factor_value != triggered:
                    errors.append(f"{trade_date}/{row.symbol}: factor_value is not the red-flag count")
                expected_score = triggered / available if available else float("nan")
                if not _close(row.score, expected_score):
                    errors.append(f"{trade_date}/{row.symbol}: score is not red-flag count / available rules")
            else:
                if not pd.isna(row.factor_value):
                    errors.append(f"{trade_date}/{row.symbol}: non-evaluated row has a factor_value")
                if not pd.isna(row.score):
                    errors.append(f"{trade_date}/{row.symbol}: non-evaluated row has a score")

            for key, value in (
                ("run_id", row.run_id),
                ("dataset_version", row.data_version),
                ("rule_config_hash", row.rule_config_hash),
                ("source_snapshot", row.source_snapshot),
            ):
                if row_metadata.get(key) != value:
                    errors.append(f"{trade_date}/{row.symbol}: {key} metadata mismatch")
            if row_runtime.get("panda_data") != row.data_sdk_version:
                errors.append(f"{trade_date}/{row.symbol}: runtime SDK metadata mismatch")

        if metadata is None:
            continue
        rank_by_symbol = _expected_rank(records)
        for row in group.itertuples(index=False):
            try:
                evidence = json.loads(row.evidence_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if evidence.get("status") == Status.EVALUATED.value:
                if int(row.rank) != rank_by_symbol.get(str(row.symbol)):
                    errors.append(f"{trade_date}/{row.symbol}: rank does not match red-flag ordering")
            elif not pd.isna(row.rank):
                errors.append(f"{trade_date}/{row.symbol}: non-evaluated row has a rank")

        # Reconstruct the full run result and re-derive every record from its
        # own annual history. This is what makes a fully rewritten, internally
        # consistent row set still fail: the engine must reproduce it exactly.
        result = dict(metadata)
        result["records"] = records
        report = validate_result(result)
        errors.extend(f"{trade_date}: {error}" for error in report["errors"])

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
