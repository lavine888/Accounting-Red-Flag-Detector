"""Point-in-time research backtest.

Answers the only question that matters for a scanner: do red flags carry
forward-looking information? For each signal date the module groups names by
red-flag count and risk level and reports 3/6/12-month forward-return
statistics. It never drops a symbol silently: every evaluated name without a
usable forward return is listed under ``missing_symbols``.
"""

from __future__ import annotations

from typing import Any, Callable

from ..models import RiskLevel, Status
from .diagnostics import FLAG_BUCKETS, describe, flag_bucket, portfolio_series, safe_spread
from .forward_returns import add_months

ReturnLoader = Callable[[list[str], str, str], dict[str, float]]


def _group_stats(pairs: list[tuple[str, float]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[float]] = {}
    for key, value in pairs:
        buckets.setdefault(key, []).append(value)
    return {key: describe(values) for key, values in buckets.items()}


def analyze_snapshots(
    snapshots: list[dict[str, Any]],
    return_loader: ReturnLoader,
    *,
    horizons: tuple[int, ...] = (3, 6, 12),
) -> dict[str, Any]:
    if len(snapshots) < 2:
        raise ValueError("backtest requires at least two signal snapshots")
    snapshots = sorted(snapshots, key=lambda item: item["as_of"])

    per_snapshot: list[dict[str, Any]] = []
    pooled_flag: dict[int, dict[str, list[float]]] = {h: {} for h in horizons}
    pooled_risk: dict[int, dict[str, list[float]]] = {h: {} for h in horizons}
    pooled_high_series: dict[int, list[tuple[str, float | None]]] = {h: [] for h in horizons}
    pooled_low_series: dict[int, list[tuple[str, float | None]]] = {h: [] for h in horizons}

    for snapshot in snapshots:
        records = snapshot["records"]
        symbols = [record["symbol"] for record in records]
        evaluated = [record for record in records if record["status"] == Status.EVALUATED.value]
        horizon_payload: dict[str, Any] = {}
        for horizon in horizons:
            end_date = add_months(snapshot["as_of"], horizon)
            forward = return_loader(symbols, snapshot["as_of"], end_date)
            flag_pairs: list[tuple[str, float]] = []
            risk_pairs: list[tuple[str, float]] = []
            missing: list[str] = []
            for record in evaluated:
                value = forward.get(record["symbol"])
                if value is None:
                    missing.append(record["symbol"])
                    continue
                flag_pairs.append((flag_bucket(record["red_flag_count"]), float(value)))
                risk_pairs.append((record["risk_level"], float(value)))
                if record["risk_level"] == RiskLevel.HIGH.value:
                    pooled_high_series[horizon].append((snapshot["as_of"], float(value)))
                if record["risk_level"] == RiskLevel.LOW.value:
                    pooled_low_series[horizon].append((snapshot["as_of"], float(value)))
            for key, value in flag_pairs:
                pooled_flag[horizon].setdefault(key, []).append(value)
            for key, value in risk_pairs:
                pooled_risk[horizon].setdefault(key, []).append(value)
            horizon_payload[str(horizon)] = {
                "end_date": end_date,
                "evaluated_count": len(evaluated),
                "forward_return_coverage": (len(flag_pairs) / len(evaluated)) if evaluated else None,
                "missing_symbols": sorted(missing),
                "groups_by_flag_count": {
                    bucket: _stats_for(flag_pairs, bucket)
                    for bucket in FLAG_BUCKETS
                },
                "groups_by_risk_level": {
                    level: _stats_for(risk_pairs, level)
                    for level in (
                        RiskLevel.LOW.value,
                        RiskLevel.MEDIUM.value,
                        RiskLevel.HIGH.value,
                    )
                },
            }
        per_snapshot.append(
            {
                "signal_date": snapshot["as_of"],
                "universe_size": snapshot["universe_size"],
                "counts": snapshot["counts"],
                "horizons": horizon_payload,
            }
        )

    pooled: dict[str, Any] = {}
    for horizon in horizons:
        flag_groups = {bucket: describe(pooled_flag[horizon].get(bucket, [])) for bucket in FLAG_BUCKETS}
        risk_groups = {
            level: describe(pooled_risk[horizon].get(level, []))
            for level in (RiskLevel.LOW.value, RiskLevel.MEDIUM.value, RiskLevel.HIGH.value)
        }
        high_mean = risk_groups[RiskLevel.HIGH.value]["mean_return"]
        low_mean = risk_groups[RiskLevel.LOW.value]["mean_return"]
        pooled[str(horizon)] = {
            "groups_by_flag_count": flag_groups,
            "groups_by_risk_level": risk_groups,
            "high_minus_low_mean_return": safe_spread(high_mean, low_mean),
            "high_risk_portfolio": portfolio_series(pooled_high_series[horizon]),
            "low_risk_portfolio": portfolio_series(pooled_low_series[horizon]),
        }

    return {
        "signal_dates": [snapshot["as_of"] for snapshot in snapshots],
        "horizons": list(horizons),
        "snapshots": per_snapshot,
        "pooled": pooled,
        "limitations": [
            "Signal dates are year-ends; a report announced after the signal date is not used.",
            "Forward returns use post-adjusted close and ignore transaction costs, liquidity and limit moves.",
            "Only evaluated (complete-evidence) names enter the flag/risk groups; insufficient_data and not_applicable names are reported in counts.",
            "Synthetic fixture returns are demonstrations of the pipeline, not evidence about real markets.",
            "This is research evidence, not an execution simulator or investment advice.",
        ],
    }


def _stats_for(pairs: list[tuple[str, float]], key: str) -> dict[str, Any]:
    return describe([value for item_key, value in pairs if item_key == key])
