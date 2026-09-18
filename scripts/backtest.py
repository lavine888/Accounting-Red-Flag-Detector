"""Research backtest CLI.

Example::

    python scripts/backtest.py \\
        --signal-dates 20221230 20231229 20241231 20251231 \\
        --all-sh-sz --output output/backtest.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounting_red_flags.config import load_rule_config
from accounting_red_flags.materialization import write_json
from accounting_red_flags.research import analyze_snapshots
from accounting_red_flags.service import screen
from scripts.build import load_symbols_file, make_provider


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Point-in-time red-flag forward-return research")
    parser.add_argument("--signal-dates", nargs="+", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbols", nargs="+")
    group.add_argument("--symbols-file")
    group.add_argument("--all-a", "--all-sh-sz", dest="all_a", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", choices=["pandadata", "fixture"], default="pandadata")
    parser.add_argument("--config")
    parser.add_argument("--horizons", nargs="+", type=int, default=[3, 6, 12])
    parser.add_argument("--cache-dir")
    parser.add_argument("--request-interval", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=1)
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
    rule_config = load_rule_config(args.config)
    snapshots = [
        screen(as_of=day, symbols=symbols, all_a=args.all_a, config=rule_config, provider=provider)
        for day in sorted(set(args.signal_dates))
    ]
    result = analyze_snapshots(snapshots, provider.fetch_forward_returns, horizons=tuple(args.horizons))
    write_json(result, args.output)
    print(json.dumps({"output": str(args.output), "signal_dates": result["signal_dates"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
