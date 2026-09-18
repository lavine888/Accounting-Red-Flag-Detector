from __future__ import annotations

import pytest

from accounting_red_flags.config import (
    RULES_VERSION,
    SCHEMA_VERSION,
    RuleConfig,
    config_hash,
    config_to_dict,
    load_rule_config,
)


def test_default_config_loads_from_yaml():
    config = load_rule_config()
    assert isinstance(config, RuleConfig)
    assert config.coverage_min == 0.60
    assert config.risk_medium_flags == 2
    assert config.risk_high_flags == 4
    assert config.cash_conversion_min == 0.80
    assert config.accrual_ratio_max == 0.10
    assert config.excluded_industry_codes == ("801780", "801790")


def test_missing_file_falls_back_to_defaults(tmp_path):
    config = load_rule_config(tmp_path / "nope.yaml")
    assert config == RuleConfig()


def test_version_keys_in_yaml_do_not_override_code(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("rules_version: '9.9.9'\nschema_version: '9.9.9'\ncoverage_min: 0.7\n", encoding="utf-8")
    config = load_rule_config(path)
    assert config.coverage_min == 0.7
    assert RULES_VERSION == "1.0.0"
    assert SCHEMA_VERSION == "1.0.0"


def test_unknown_key_is_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("cash_conversion_minimum: 0.8\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown rule config keys"):
        load_rule_config(path)


def test_non_mapping_yaml_is_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_rule_config(path)


def test_config_hash_is_stable_and_threshold_sensitive():
    base = RuleConfig()
    assert config_hash(base) == config_hash(RuleConfig())
    assert config_hash(base) != config_hash(RuleConfig(coverage_min=0.61))


def test_config_to_dict_carries_versions():
    payload = config_to_dict(RuleConfig())
    assert payload["rules_version"] == RULES_VERSION
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["coverage_min"] == 0.60


def test_config_is_frozen():
    config = RuleConfig()
    with pytest.raises(Exception):
        config.coverage_min = 0.1  # type: ignore[misc]
