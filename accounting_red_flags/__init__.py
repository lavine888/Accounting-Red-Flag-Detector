"""Accounting Red Flag Detector.

Point-in-time A-share accounting red-flag detection with auditable evidence.

Agent investigates. Rules decide. Evidence explains. Missing data never gets
guessed.
"""

from __future__ import annotations

from .config import RULES_VERSION, SCHEMA_VERSION, SKILL_ID, RuleConfig, load_rule_config

__all__ = [
    "RULES_VERSION",
    "SCHEMA_VERSION",
    "SKILL_ID",
    "RuleConfig",
    "load_rule_config",
    "__version__",
]

__version__ = "1.0.0"
