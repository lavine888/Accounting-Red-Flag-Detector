from __future__ import annotations

import math

import pytest

from accounting_red_flags.cross_section import peer_contexts


def _record(symbol: str, gap: float | None, *, industry: str | None = "801080", status: str = "evaluated"):
    industry_block = None if industry is None else {"industry_code": industry, "industry_name": "电子"}
    return {
        "symbol": symbol,
        "status": status,
        "industry": industry_block,
        "evidence": {"receivable_gap": gap},
    }


def test_peer_context_reports_count_median_and_percentile():
    records = [_record(f"60000{i}.SH", gap) for i, gap in enumerate([0.05, 0.15, 0.25, 0.35, 0.55])]
    contexts = peer_contexts(records, min_sample=5)
    top = contexts["600004.SH"]["metrics"]["receivable_gap"]
    assert top["peer_count"] == 5
    assert top["peer_median"] == pytest.approx(0.25)
    # 4 of 5 peers sit strictly below the maximum.
    assert top["peer_percentile"] == pytest.approx(0.8)
    bottom = contexts["600000.SH"]["metrics"]["receivable_gap"]
    assert bottom["peer_percentile"] == pytest.approx(0.0)


def test_peer_context_is_absent_below_min_sample():
    records = [_record(f"60000{i}.SH", 0.1 * i) for i in range(4)]
    contexts = peer_contexts(records, min_sample=5)
    assert all(context["metrics"] == {} for context in contexts.values())


def test_peer_context_honours_configured_min_sample():
    records = [_record(f"60000{i}.SH", 0.1 * i) for i in range(3)]
    contexts = peer_contexts(records, min_sample=3)
    assert contexts["600002.SH"]["metrics"]["receivable_gap"]["peer_count"] == 3


def test_peer_context_ignores_non_finite_and_missing_values():
    records = [
        _record("600000.SH", 0.10),
        _record("600001.SH", 0.20),
        _record("600002.SH", 0.30),
        _record("600003.SH", None),
        _record("600004.SH", math.nan),
        _record("600005.SH", math.inf),
    ]
    contexts = peer_contexts(records, min_sample=3)
    assert contexts["600000.SH"]["metrics"]["receivable_gap"]["peer_count"] == 3
    # A company without a finite metric gets no peer block for that metric.
    assert contexts["600003.SH"]["metrics"] == {}
    assert contexts["600004.SH"]["metrics"] == {}


def test_financial_and_industry_less_records_have_no_peer_context():
    records = [
        _record("600000.SH", 0.10, status="not_applicable"),
        _record("600001.SH", 0.20, industry=None),
        _record("600002.SH", 0.30),
    ]
    contexts = peer_contexts(records, min_sample=1)
    assert contexts["600000.SH"] is None
    assert contexts["600001.SH"] is None
    # The financial company never enters the peer pool.
    assert contexts["600002.SH"]["metrics"]["receivable_gap"]["peer_count"] == 1


def test_peer_context_is_deterministic():
    records = [_record(f"60000{i}.SH", 0.1 * i) for i in range(5)]
    assert peer_contexts(records, min_sample=5) == peer_contexts(list(reversed(records)), min_sample=5)
