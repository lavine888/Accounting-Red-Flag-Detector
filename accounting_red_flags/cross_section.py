"""Cross-sectional (peer-relative) context for the divergence rules.

RF02/RF03/RF04/RF06 compare a company against its own prior year, so a whole
industry with structurally high receivables, inventory or accruals (real
estate, construction, government-facing contractors) can look anomalous. This
module reports where a company sits relative to its Shenwan L1 peers.

It is deliberately *additive*: it never changes a flag or a risk level. The
rules still decide; the peer percentile is context for the investigator, not
absolution. This matters for fraud detection, where normalising away an
anomaly because peers share it would hide exactly the systemic cases the tool
exists to surface.

The output is pure and deterministic: the same records always produce the same
peer statistics, so the validator can recompute every value.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

# Metrics where "higher is more anomalous". RF05 is excluded on purpose: it is
# already measured against the company's own history, so it is self-normalising.
PEER_METRICS = ("receivable_gap", "inventory_gap", "accrual_ratio", "growth_gap")

NOT_APPLICABLE = "not_applicable"


def industry_code(record: Mapping[str, Any]) -> str:
    industry = record.get("industry") or {}
    return str(industry.get("industry_code") or industry.get("l1_code") or "").strip()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pools(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, list[float]]]:
    """Metric value pools per industry, over every non-financial company."""

    pools: dict[str, dict[str, list[float]]] = {}
    for record in records:
        if record.get("status") == NOT_APPLICABLE:
            continue
        code = industry_code(record)
        if not code:
            continue
        evidence = record.get("evidence") or {}
        for metric in PEER_METRICS:
            value = _finite(evidence.get(metric))
            if value is not None:
                pools.setdefault(code, {}).setdefault(metric, []).append(value)
    return pools


def peer_contexts(
    records: Sequence[Mapping[str, Any]], *, min_sample: int
) -> dict[str, dict[str, Any] | None]:
    """Peer statistics for every record, keyed by symbol.

    ``peer_percentile`` is the share of industry peers with a strictly smaller
    metric value (0.0 = the industry minimum, ``(n-1)/n`` = the maximum). A
    metric is only reported when the industry pool has at least ``min_sample``
    observations; otherwise the company keeps its absolute-rule evidence and
    the metric is simply absent, never guessed.

    Financial (``not_applicable``) companies and companies without an industry
    get ``None``.
    """

    pools = _pools(records)
    contexts: dict[str, dict[str, Any] | None] = {}
    for record in records:
        symbol = str(record.get("symbol"))
        if record.get("status") == NOT_APPLICABLE:
            contexts[symbol] = None
            continue
        code = industry_code(record)
        if not code:
            contexts[symbol] = None
            continue
        evidence = record.get("evidence") or {}
        metrics: dict[str, dict[str, Any]] = {}
        for metric in PEER_METRICS:
            value = _finite(evidence.get(metric))
            pool = pools.get(code, {}).get(metric, [])
            if value is None or len(pool) < min_sample:
                continue
            below = sum(1 for peer in pool if peer < value)
            metrics[metric] = {
                "peer_count": len(pool),
                "peer_median": statistics.median(pool),
                "peer_percentile": below / len(pool),
            }
        contexts[symbol] = {"industry_code": code, "metrics": metrics}
    return contexts
