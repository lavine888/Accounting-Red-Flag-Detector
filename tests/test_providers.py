from __future__ import annotations

import pandas as pd
import pytest

from accounting_red_flags.providers import AuthenticationError, FixtureProvider, PandaDataProvider
from accounting_red_flags.providers.fixture import FIXTURE_UNIVERSE, build_fixture_reports


def test_fixture_provider_is_marked_synthetic():
    provider = FixtureProvider()
    assert provider.requires_live_validation is True
    assert "synthetic" in provider.name.lower()


def test_fixture_provider_contract():
    provider = FixtureProvider()
    assert provider.discover_universe("20251231") == FIXTURE_UNIVERSE
    reports = provider.fetch_reports(FIXTURE_UNIVERSE, "20251231")
    assert set(reports["symbol"]) == set(FIXTURE_UNIVERSE)
    industries = provider.fetch_industries(FIXTURE_UNIVERSE, "20251231")
    assert industries["600003.SH"]["industry_code"] == "801780"
    returns = provider.fetch_forward_returns(FIXTURE_UNIVERSE, "20241231", "20251231")
    assert returns["600002.SH"] < 0


def test_fixture_reports_are_deterministic():
    first = build_fixture_reports()
    second = build_fixture_reports()
    pd.testing.assert_frame_equal(first, second)


def test_fixture_forward_returns_filter_by_symbol():
    provider = FixtureProvider()
    assert provider.fetch_forward_returns(["600001.SH"], "20241231", "20251231") == {"600001.SH": 0.10}


def test_provider_requires_both_credentials():
    provider = PandaDataProvider()
    with pytest.raises(AuthenticationError):
        provider.configure_credentials("user", "")
    with pytest.raises(AuthenticationError):
        provider.configure_credentials("", "pass")


def test_provider_reads_environment_once(monkeypatch):
    monkeypatch.setenv("PANDA_DATA_USERNAME", "u")
    monkeypatch.setenv("PANDA_DATA_PASSWORD", "p")
    monkeypatch.setenv("PANDA_DATA_BASE_URL", "http://example.invalid")
    provider = PandaDataProvider()
    provider.consume_environment_credentials()
    assert provider._credentials == ("u", "p", "http://example.invalid")
    import os

    assert "PANDA_DATA_PASSWORD" not in os.environ


def test_missing_credentials_raise_authentication_error(monkeypatch):
    for name in ("PANDA_DATA_USERNAME", "PANDA_DATA_PASSWORD", "PANDA_DATA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    provider = PandaDataProvider()
    with pytest.raises(AuthenticationError):
        provider.ensure_authenticated()


def test_runtime_versions_include_sdk():
    versions = PandaDataProvider().runtime_versions()
    assert versions["panda_data"]
    assert versions["pandas"]


def test_cache_path_is_namespaced_by_account_and_api(monkeypatch, tmp_path):
    provider = PandaDataProvider(username="account-a", password="secret")
    provider.configure_runtime(cache_dir=tmp_path)
    path_a = provider._cache_path("get_fina_reports", {"symbol": ["600001.SH"]})
    other = PandaDataProvider(username="account-b", password="secret")
    other.configure_runtime(cache_dir=tmp_path)
    path_b = other._cache_path("get_fina_reports", {"symbol": ["600001.SH"]})
    path_other_api = provider._cache_path("get_stock_daily", {"symbol": ["600001.SH"]})
    assert path_a is not None and path_b is not None
    assert path_a != path_b
    assert path_a.parent.name == "get_fina_reports"
    assert path_other_api.parent.name == "get_stock_daily"
    assert "secret" not in str(path_a)


def test_cache_path_is_none_without_cache_dir():
    provider = PandaDataProvider(username="u", password="p")
    assert provider._cache_path("get_fina_reports", {}) is None


def test_frame_digest_is_stable_and_value_sensitive():
    frame = pd.DataFrame({"symbol": ["600001.SH"], "is_revenue": [100.0]})
    assert PandaDataProvider._frame_digest(frame) == PandaDataProvider._frame_digest(frame.copy())
    changed = pd.DataFrame({"symbol": ["600001.SH"], "is_revenue": [101.0]})
    assert PandaDataProvider._frame_digest(frame) != PandaDataProvider._frame_digest(changed)


def test_source_provenance_is_a_hash_not_a_secret():
    provider = PandaDataProvider(username="u", password="p")
    provider._record_source(None, "abc")
    provenance = provider.source_provenance()
    assert provenance["response_count"] == 1
    assert len(provenance["response_manifest_hash"]) == 64
    assert "u" not in provenance["response_manifest_hash"]


def test_source_provenance_deduplicates():
    provider = PandaDataProvider(username="u", password="p")
    provider._record_source(None, "abc")
    provider._record_source(None, "abc")
    assert provider.source_provenance()["response_count"] == 1


def test_unknown_api_is_rejected(monkeypatch):
    provider = PandaDataProvider(username="u", password="p")
    provider._authenticated = True
    with pytest.raises(Exception):
        provider.fetch("definitely_not_a_panda_api")
