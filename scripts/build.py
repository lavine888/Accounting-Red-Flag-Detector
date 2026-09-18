"""Build / screening CLI.

Examples
--------
Single company::

    python scripts/build.py --as-of 20251231 --symbols 600519.SH

Full SH/SZ market::

    python scripts/build.py --as-of 20251231 --all-sh-sz \\
        --cache-dir output/panda-cache --request-interval 1.2 --workers 8 \\
        --json-output output/red-flags.json \\
        --parquet-output production/database.parquet
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounting_red_flags.config import load_rule_config
from accounting_red_flags.materialization import production_frame, write_production
from accounting_red_flags.providers import FixtureProvider, PandaDataProvider
from accounting_red_flags.service import screen


def make_provider(name: str):
    if name == "fixture":
        return FixtureProvider()
    if name == "pandadata":
        return PandaDataProvider()
    raise ValueError(f"unknown provider: {name}")


def run(input_data: dict, config: dict | None = None, provider=None) -> dict:
    config = config or {}
    allowed = {"materialize", "output_path", "replace_production", "rule_config_path"}
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ValueError(f"unknown config keys: {unknown}")
    rule_config = load_rule_config(config.get("rule_config_path"))
    active_provider = provider or make_provider(input_data.get("provider", "pandadata"))
    result = screen(
        as_of=input_data["as_of"],
        symbols=input_data.get("symbols"),
        all_a=bool(input_data.get("all_a", False)),
        config=rule_config,
        provider=active_provider,
    )
    if config.get("materialize"):
        output = Path(config.get("output_path", "production/database.parquet"))
        if output.name == "database.parquet":
            if not input_data.get("all_a"):
                raise ValueError("partial universes cannot write canonical production/database.parquet")
            if result["requires_live_validation"]:
                raise ValueError("synthetic fixture data cannot write the canonical production database")
        write_production(
            production_frame(result),
            output,
            replace=bool(config.get("replace_production", False)),
        )
        result["production_path"] = str(output)
    return result


def validate_input(input_data: dict) -> None:
    if not isinstance(input_data, dict):
        raise TypeError("input_data must be a dict")
    if "as_of" not in input_data:
        raise ValueError("input_data requires as_of")
    if bool(input_data.get("symbols")) == bool(input_data.get("all_a")):
        raise ValueError("provide either symbols or all_a=True")


def load_symbols_file(path: str | Path) -> list[str]:
    source = Path(path)
    if source.suffix.lower() == ".csv":
        frame = pd.read_csv(source, dtype=str)
        if "symbol" not in frame:
            raise ValueError("symbols CSV requires a symbol column")
        values = frame["symbol"].dropna().astype(str).tolist()
    else:
        values = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not values:
        raise ValueError("symbols file is empty")
    return values


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Point-in-time A-share accounting red flag detector")
    parser.add_argument("--as-of", required=True, help="decision date YYYYMMDD")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbols", nargs="+")
    group.add_argument("--symbols-file")
    group.add_argument("--all-a", "--all-sh-sz", dest="all_a", action="store_true")
    parser.add_argument("--json-output")
    parser.add_argument("--parquet-output")
    parser.add_argument("--replace-production", action="store_true")
    parser.add_argument("--provider", choices=["pandadata", "fixture"], default="pandadata")
    parser.add_argument("--config", help="path to a rules YAML (defaults to config/rules.yaml)")
    parser.add_argument("--cache-dir", help="resumable PandaData response cache")
    parser.add_argument("--request-interval", type=float, default=0.0, help="minimum seconds between API calls")
    parser.add_argument("--workers", type=int, default=1, help="maximum concurrent report requests")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    symbols = load_symbols_file(args.symbols_file) if args.symbols_file else args.symbols
    provider = make_provider(args.provider)
    provider.configure_runtime(
        cache_dir=args.cache_dir,
        min_request_interval=args.request_interval,
        max_workers=args.workers,
    )
    input_data = {"as_of": args.as_of, "symbols": symbols, "all_a": args.all_a, "provider": args.provider}
    validate_input(input_data)
    run_config = {"rule_config_path": args.config}
    if args.parquet_output:
        run_config.update(
            {
                "materialize": True,
                "output_path": args.parquet_output,
                "replace_production": args.replace_production,
            }
        )
    result = run(input_data, run_config, provider=provider)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False, default=str)
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
