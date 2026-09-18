"""Versioned production Parquet materialization.

The Parquet table is the factor-style production contract. Rows carry the full
per-company evidence as JSON plus the run provenance, and are keyed by
``(trade_date, factor_id, symbol)`` so a re-run upserts rather than duplicates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from ..config import RULES_VERSION, SCHEMA_VERSION, SKILL_ID, SKILL_NAME
from ..models import RiskLevel
from .json_writer import canonical_json

PRODUCTION_KEY = ["trade_date", "factor_id", "symbol"]

SIGNAL_BY_RISK = {
    RiskLevel.HIGH.value: "review",
    RiskLevel.MEDIUM.value: "watch",
    RiskLevel.LOW.value: "clear",
    RiskLevel.INSUFFICIENT_DATA.value: "unknown",
    RiskLevel.NOT_APPLICABLE.value: "not_applicable",
}


def signal_for(risk_level: str) -> str:
    return SIGNAL_BY_RISK.get(risk_level, "unknown")


def production_frame(result: dict[str, Any]) -> pd.DataFrame:
    now = datetime.now(timezone.utc).isoformat()
    run_metadata = {key: value for key, value in result.items() if key != "records"}
    ranked = sorted(
        result["records"],
        key=lambda record: (
            record["status"] != "evaluated",
            -record["red_flag_count"],
            record["symbol"],
        ),
    )
    rank_by_symbol = {record["symbol"]: index + 1 for index, record in enumerate(ranked)}
    rows = []
    for record in result["records"]:
        available = record["available_rule_count"]
        evaluated = record["status"] == "evaluated"
        rows.append(
            {
                "trade_date": result["as_of"],
                "asset_type": "stock",
                "symbol": record["symbol"],
                "factor_id": SKILL_ID,
                "factor_name": SKILL_NAME,
                "factor_value": record["red_flag_count"] if evaluated else None,
                "score": (record["red_flag_count"] / available) if evaluated and available else float("nan"),
                "rank": rank_by_symbol[record["symbol"]] if evaluated else None,
                "signal": signal_for(record["risk_level"]),
                "confidence": record["coverage_ratio"],
                "risk_level": record["risk_level"],
                "status": record["status"],
                "red_flag_count": record["red_flag_count"],
                "available_rule_count": available,
                "coverage_ratio": record["coverage_ratio"],
                "evidence_json": canonical_json(record),
                "run_metadata_json": canonical_json(run_metadata),
                "data_source": result["data_source"],
                "data_version": result["dataset_version"],
                "rules_version": RULES_VERSION,
                "rule_config_hash": result["rule_config_hash"],
                "run_id": result["run_id"],
                "source_snapshot": result["source_snapshot"],
                "data_sdk_version": result["data_sdk_version"],
                "runtime_versions_json": canonical_json(result["runtime_versions"]),
                "schema_version": SCHEMA_VERSION,
                "update_time": now,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("cannot materialize an empty screen result")
    if frame.duplicated(PRODUCTION_KEY).any():
        raise ValueError(f"duplicate production key: {PRODUCTION_KEY}")
    for payload in frame["evidence_json"]:
        canonical_json_loads(payload)
    return frame


def canonical_json_loads(payload: str) -> Any:
    import json

    return json.loads(payload)


def write_production(frame: pd.DataFrame, path: str | Path, *, replace: bool = False) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    result = frame
    if output.exists() and not replace:
        existing = pd.read_parquet(output)
        if set(existing.columns) != set(frame.columns):
            raise ValueError("production schema changed; use explicit replace after migration review")
        incoming_keys = pd.MultiIndex.from_frame(frame[PRODUCTION_KEY])
        existing_keys = pd.MultiIndex.from_frame(existing[PRODUCTION_KEY])
        result = pd.concat(
            [existing.loc[~existing_keys.isin(incoming_keys)], frame],
            ignore_index=True,
        )
        if result.duplicated(PRODUCTION_KEY).any():
            raise ValueError(f"duplicate production key after upsert: {PRODUCTION_KEY}")
    temporary = output.with_name(f"{output.name}.{uuid4().hex}.tmp")
    try:
        result.to_parquet(temporary, index=False)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
