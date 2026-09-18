from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from accounting_red_flags.providers import FixtureProvider
from scripts.build import load_symbols_file, run, validate_input

SYMBOLS = ["600001.SH", "600002.SH"]


def test_run_with_fixture_provider():
    result = run(
        {"as_of": "20251231", "symbols": SYMBOLS, "provider": "fixture"},
        provider=FixtureProvider(),
    )
    assert result["universe_size"] == 2
    assert result["requires_live_validation"] is True


def test_run_materializes_to_non_canonical_path(tmp_path):
    output = tmp_path / "demo.parquet"
    result = run(
        {"as_of": "20251231", "symbols": SYMBOLS, "provider": "fixture"},
        {"materialize": True, "output_path": str(output)},
        provider=FixtureProvider(),
    )
    assert result["production_path"] == str(output)
    frame = pd.read_parquet(output)
    assert len(frame) == 2


def test_run_refuses_canonical_database_from_partial_universe(tmp_path):
    output = tmp_path / "database.parquet"
    with pytest.raises(ValueError, match="partial universes"):
        run(
            {"as_of": "20251231", "symbols": SYMBOLS, "provider": "fixture"},
            {"materialize": True, "output_path": str(output)},
            provider=FixtureProvider(),
        )


def test_run_refuses_canonical_database_from_synthetic_data(tmp_path):
    output = tmp_path / "database.parquet"
    with pytest.raises(ValueError, match="synthetic fixture data"):
        run(
            {"as_of": "20251231", "all_a": True, "provider": "fixture"},
            {"materialize": True, "output_path": str(output)},
            provider=FixtureProvider(),
        )


def test_run_rejects_unknown_config_keys():
    with pytest.raises(ValueError, match="unknown config keys"):
        run({"as_of": "20251231", "symbols": SYMBOLS, "provider": "fixture"}, {"oops": 1})


def test_validate_input_requires_as_of():
    with pytest.raises(ValueError, match="as_of"):
        validate_input({"symbols": SYMBOLS})


def test_validate_input_requires_exactly_one_universe():
    with pytest.raises(ValueError, match="either symbols or all_a"):
        validate_input({"as_of": "20251231"})
    with pytest.raises(ValueError, match="either symbols or all_a"):
        validate_input({"as_of": "20251231", "symbols": SYMBOLS, "all_a": True})


def test_load_symbols_from_text(tmp_path):
    path = tmp_path / "symbols.txt"
    path.write_text("600001.SH\n\n600002.SH\n", encoding="utf-8")
    assert load_symbols_file(path) == ["600001.SH", "600002.SH"]


def test_load_symbols_from_csv(tmp_path):
    path = tmp_path / "symbols.csv"
    pd.DataFrame({"symbol": ["600001.SH", "600002.SH"]}).to_csv(path, index=False)
    assert load_symbols_file(path) == ["600001.SH", "600002.SH"]


def test_load_symbols_csv_requires_column(tmp_path):
    path = tmp_path / "symbols.csv"
    pd.DataFrame({"ticker": ["600001.SH"]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="symbol column"):
        load_symbols_file(path)


def test_load_symbols_rejects_empty(tmp_path):
    path = tmp_path / "symbols.txt"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_symbols_file(path)


def test_run_output_is_json_serializable():
    result = run({"as_of": "20251231", "symbols": SYMBOLS, "provider": "fixture"}, provider=FixtureProvider())
    payload = json.dumps(result, allow_nan=False, default=str)
    assert "600001.SH" in payload
