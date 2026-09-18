"""Render a validated red-flag JSON result as a Markdown report.

The report is a read-only view: it is validated first (fail closed), then
rendered without adding, dropping or rewriting any evidence. A missing value is
printed as ``—``, never as ``0``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounting_red_flags.reporting import render_report
from scripts.validate import validate_result

if hasattr(sys.stdout, "reconfigure"):
    # Windows consoles default to GBK; the report is UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a red-flag JSON result as Markdown")
    parser.add_argument("result", help="path to a JSON result produced by scripts/build.py")
    parser.add_argument(
        "--min-risk",
        choices=["high", "medium", "low"],
        default="medium",
        help="triage floor (default: medium, i.e. high + medium)",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the triage list")
    parser.add_argument("--output", default=None, help="write to this file instead of stdout")
    args = parser.parse_args()

    path = Path(args.result)
    result = json.loads(path.read_text(encoding="utf-8"))
    report = validate_result(result)
    if report["status"] != "PASS":
        print("refusing to render an unvalidated result:", file=sys.stderr)
        for error in report["errors"]:
            print(f"  - {error}", file=sys.stderr)
        return 1

    markdown = render_report(result, min_risk=args.min_risk, limit=args.limit)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
