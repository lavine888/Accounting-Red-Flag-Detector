"""Configuration for the Accounting Red Flag Detector.

All numeric thresholds live in ``config/rules.yaml``. ``RuleConfig`` is the
typed, frozen view of that file. The rule-config hash embedded in every result
is derived from this object, so a threshold change invalidates old artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
from pathlib import Path
from typing import Any

RULES_VERSION = "1.1.0"
SCHEMA_VERSION = "1.2.0"
SKILL_ID = "ARFD-LAVINE"
SKILL_NAME = "Accounting Red Flag Detector - Lavine Version"
DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"


@dataclass(frozen=True)
class RuleConfig:
    """Deterministic thresholds for every red flag and the risk classifier."""

    # history window
    history_years: int = 6
    min_annual_reports: int = 2
    # Fail closed when the newest visible annual report is older than this many
    # years relative to ``as_of``. A delinquent filer must never read as "low".
    max_evidence_age_years: int = 2
    gross_margin_min_history: int = 3
    gross_margin_max_history: int = 5
    deterioration_years: int = 3

    # RF01
    cash_conversion_min: float = 0.80
    # RF02
    receivable_growth_min: float = 0.20
    receivable_gap_min: float = 0.20
    # RF03
    inventory_growth_min: float = 0.20
    inventory_gap_min: float = 0.20
    # RF04
    accrual_ratio_max: float = 0.10
    # RF05
    gross_margin_abs_change_min: float = 0.05
    gross_margin_zscore_min: float = 2.0
    # RF06
    profit_growth_min: float = 0.30
    profit_revenue_gap_min: float = 0.30
    # RF07
    deterioration_latest_max: float = 0.80

    # risk classification
    risk_medium_flags: int = 2
    risk_high_flags: int = 4
    coverage_min: float = 0.60

    # industry scope
    excluded_industry_codes: tuple[str, ...] = ("801780", "801790")
    excluded_industry_pattern: str = "银行|保险|证券|非银金融|信托|多元金融"


# The exact PandaData statement fields this skill treats as evidence. They are
# part of the cache namespace and the source snapshot contract.
REPORT_FIELDS = [
    "symbol",
    "quarter",
    "date",
    "if_adjusted",
    "is_revenue",
    "is_oper_cost",
    "is_n_income_attr_p",
    "cfs_net_cash_operating",
    "bs_net_accts_receive",
    "bs_notes_accts_receiv",
    "bs_inventory",
    "bs_total_assets",
]


def config_to_dict(config: RuleConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["rules_version"] = RULES_VERSION
    payload["schema_version"] = SCHEMA_VERSION
    return payload


def config_hash(config: RuleConfig) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_rule_config(path: str | Path | None = None) -> RuleConfig:
    """Load thresholds from YAML, falling back to dataclass defaults.

    Unknown keys are rejected so a typo cannot silently disable a rule.
    """

    source = Path(path) if path else DEFAULT_RULES_PATH
    if not source.exists():
        return RuleConfig()
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("PyYAML is required to read config/rules.yaml") from exc

    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"rule config {source} must be a mapping")
    # Version keys are validated against the running code, never passed through
    # to ``RuleConfig``. A mismatch is a hard error: silently ignoring it would
    # let an artifact claim a rule/schema version the engine did not run.
    declared_rules = raw.pop("rules_version", None)
    declared_schema = raw.pop("schema_version", None)
    if declared_rules is not None and str(declared_rules) != RULES_VERSION:
        raise ValueError(
            f"rules_version {declared_rules!r} in {source} does not match runtime {RULES_VERSION}"
        )
    if declared_schema is not None and str(declared_schema) != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version {declared_schema!r} in {source} does not match runtime {SCHEMA_VERSION}"
        )
    allowed = {field.name for field in fields(RuleConfig)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown rule config keys: {unknown}")
    if "excluded_industry_codes" in raw:
        raw["excluded_industry_codes"] = tuple(str(code) for code in raw["excluded_industry_codes"])
    return RuleConfig(**raw)
