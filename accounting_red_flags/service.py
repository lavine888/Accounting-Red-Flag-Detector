"""Top-level screening service.

This module wires the provider, the point-in-time layer and the rule engine
together. It owns the run-level metadata (versions, hashes, provenance) that
makes an artifact auditable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import re
from typing import Any
from uuid import uuid4

from .config import (
    RULES_VERSION,
    SCHEMA_VERSION,
    SKILL_ID,
    RuleConfig,
    config_hash,
    load_rule_config,
)
from .cross_section import peer_contexts
from .models import FLAG_NAMES, RiskLevel, Status
from .point_in_time.reports import annual_rows, select_visible_revisions
from .providers.base import DataProvider
from .providers.pandadata import PandaDataProvider
from .rules import evaluate_symbol
from .util import clean_date, clean_symbol, is_valid_symbol


def validate_as_of(value: str) -> str:
    cleaned = clean_date(value)
    if not re.fullmatch(r"\d{8}", cleaned):
        raise ValueError("as_of must use YYYYMMDD")
    datetime.strptime(cleaned, "%Y%m%d")
    return cleaned


def validate_symbols(symbols: list[str]) -> list[str]:
    normalized = [clean_symbol(symbol) for symbol in symbols]
    invalid = [symbol for symbol in normalized if not is_valid_symbol(symbol)]
    if invalid:
        raise ValueError(f"unsupported A-share symbols: {invalid}")
    return sorted({str(symbol) for symbol in normalized})


def screen(
    *,
    as_of: str,
    symbols: list[str] | None = None,
    all_a: bool = False,
    config: RuleConfig | None = None,
    provider: DataProvider | None = None,
) -> dict[str, Any]:
    """Run the fail-closed red-flag screen."""

    as_of = validate_as_of(as_of)
    if all_a == bool(symbols):
        raise ValueError("provide either symbols or all_a=True")
    config = config or load_rule_config()
    provider = provider or PandaDataProvider()

    if all_a:
        universe = provider.discover_universe(as_of)
    else:
        universe = validate_symbols(symbols or [])

    provider.ensure_authenticated()
    reports = provider.fetch_reports(universe, as_of, years=config.history_years + 2)
    visible, conflicts = select_visible_revisions(reports, as_of)
    industries = provider.fetch_industries(universe, as_of)

    records = []
    for symbol in universe:
        annual = annual_rows(visible, symbol, max_years=config.history_years)
        record = evaluate_symbol(
            symbol,
            annual,
            industries.get(symbol),
            as_of,
            config,
            conflicts,
        )
        record["rule_version"] = RULES_VERSION
        records.append(record)

    # Peer-relative context is additive: it is attached after the rules have
    # decided, and it never changes a flag or a risk level.
    contexts = peer_contexts(records, min_sample=config.peer_min_sample)
    for record in records:
        record["peer_context"] = contexts.get(record["symbol"])

    records.sort(
        key=lambda record: (
            -record["red_flag_count"],
            -record["coverage_ratio"],
            record["symbol"],
        )
    )

    thresholds = asdict(config)
    config_digest = config_hash(config)
    versions = provider.runtime_versions()
    generated_at = datetime.now(timezone.utc).isoformat()
    universe_hash = hashlib.sha256("\n".join(sorted(universe)).encode("utf-8")).hexdigest()
    source = provider.source_provenance()
    dataset_hash = hashlib.sha256(
        f"{as_of}:{SCHEMA_VERSION}:{RULES_VERSION}:{config_digest}:{universe_hash}:{source['response_manifest_hash']}".encode("utf-8")
    ).hexdigest()

    risk_counts = Counter(record["risk_level"] for record in records)
    status_counts = Counter(record["status"] for record in records)
    flag_trigger_counts = Counter(
        name for record in records for name in FLAG_NAMES if record["flags"].get(name) is True
    )
    flag_available_counts = Counter(
        name for record in records for name in FLAG_NAMES if record["flags"].get(name) is not None
    )
    insufficient_reasons = Counter(
        reason for record in records for reason in record["missing_reasons"]
    )
    provider_name = getattr(provider, "name", "unknown")
    requires_live = bool(getattr(provider, "requires_live_validation", False))

    return {
        "skill_id": SKILL_ID,
        "as_of": as_of,
        "generated_at": generated_at,
        "run_id": str(uuid4()),
        "data_source": provider_name,
        "requires_live_validation": requires_live,
        "data_sdk_version": versions.get("panda_data", ""),
        "runtime_versions": versions,
        "universe_hash": universe_hash,
        "source_response_count": source["response_count"],
        "source_snapshot": source["response_manifest_hash"],
        "dataset_version": f"{as_of}-{SCHEMA_VERSION}-{dataset_hash[:16]}",
        "rules_version": RULES_VERSION,
        "schema_version": SCHEMA_VERSION,
        "rule_config_hash": config_digest,
        "thresholds": thresholds,
        "universe_size": len(universe),
        "counts": {
            "low": risk_counts.get(RiskLevel.LOW.value, 0),
            "medium": risk_counts.get(RiskLevel.MEDIUM.value, 0),
            "high": risk_counts.get(RiskLevel.HIGH.value, 0),
            "insufficient_data": risk_counts.get(RiskLevel.INSUFFICIENT_DATA.value, 0),
            "not_applicable": risk_counts.get(RiskLevel.NOT_APPLICABLE.value, 0),
        },
        "status_counts": {
            Status.EVALUATED.value: status_counts.get(Status.EVALUATED.value, 0),
            Status.INSUFFICIENT_DATA.value: status_counts.get(Status.INSUFFICIENT_DATA.value, 0),
            Status.NOT_APPLICABLE.value: status_counts.get(Status.NOT_APPLICABLE.value, 0),
        },
        "diagnostics": {
            "risk_level_counts": dict(sorted(risk_counts.items())),
            "flag_trigger_counts": {name: flag_trigger_counts.get(name, 0) for name in FLAG_NAMES},
            "flag_available_counts": {name: flag_available_counts.get(name, 0) for name in FLAG_NAMES},
            "insufficient_reason_counts": dict(sorted(insufficient_reasons.items())),
            "industry_coverage": sum(
                record["industry"] is not None and bool(record["industry"]) for record in records
            ),
            "financial_excluded": risk_counts.get(RiskLevel.NOT_APPLICABLE.value, 0),
            "evaluated_coverage_mean": (
                sum(record["coverage_ratio"] for record in records if record["status"] == Status.EVALUATED.value)
                / status_counts.get(Status.EVALUATED.value, 1)
                if status_counts.get(Status.EVALUATED.value)
                else None
            ),
        },
        "high_risk_symbols": [
            record["symbol"] for record in records if record["risk_level"] == RiskLevel.HIGH.value
        ],
        "medium_risk_symbols": [
            record["symbol"] for record in records if record["risk_level"] == RiskLevel.MEDIUM.value
        ],
        "records": records,
    }
