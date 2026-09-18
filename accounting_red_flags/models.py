"""Typed enums and light-weight result containers for the red-flag engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FlagState(str, Enum):
    """Three-state rule outcome. ``null`` is deliberately distinct from false."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "null"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"


class Status(str, Enum):
    EVALUATED = "evaluated"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"


FLAG_NAMES = (
    "cash_conversion",
    "receivable_divergence",
    "inventory_divergence",
    "accrual_quality",
    "gross_margin_anomaly",
    "profit_revenue_divergence",
    "cash_conversion_deterioration",
)


@dataclass
class FlagResult:
    """One red flag with its evidence and, when unknown, its missing reason."""

    name: str
    state: bool | None
    value: float | None = None
    threshold: float | None = None
    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "value": self.value,
            "threshold": self.threshold,
            "reason": self.reason,
            "evidence": self.evidence,
        }
