"""Deterministic red-flag rules and risk classification.

The engine is intentionally boring: pure functions over already-selected
point-in-time evidence. It never sees the network, never sees the future and
never guesses. Every flag is ``true`` (triggered with complete evidence),
``false`` (not triggered with complete evidence) or ``null`` (evidence missing,
so no conclusion is allowed).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .config import RuleConfig
from .metrics import (
    accrual_ratio,
    average,
    cash_conversion_ratio,
    finite,
    gross_margin,
    growth_rate,
    mean_and_std,
    strictly_decreasing,
    z_score,
)
from .models import FLAG_NAMES, FlagResult, RiskLevel, Status
from .util import clean_symbol


def _value(row: Mapping[str, Any], key: str) -> float | None:
    return finite(row.get(key))


def _consecutive_prior(by_year: dict[int, Mapping[str, Any]], latest_year: int, count: int) -> list[Mapping[str, Any]]:
    """The immediately preceding consecutive annual rows, newest first."""

    rows: list[Mapping[str, Any]] = []
    year = latest_year - 1
    while year >= latest_year - count and year in by_year:
        rows.append(by_year[year])
        year -= 1
    return rows


def _recent_consecutive(by_year: dict[int, Mapping[str, Any]], latest_year: int, count: int) -> list[Mapping[str, Any]]:
    """The latest ``count`` consecutive annual rows, oldest first."""

    rows: list[Mapping[str, Any]] = []
    for offset in range(count - 1, -1, -1):
        year = latest_year - offset
        if year not in by_year:
            return []
        rows.append(by_year[year])
    return rows


def is_financial_industry(industry: Mapping[str, Any] | None, config: RuleConfig) -> bool:
    industry = industry or {}
    code = str(industry.get("industry_code") or industry.get("l1_code") or "").strip()
    name = str(industry.get("industry_name") or "")
    if code and code in set(config.excluded_industry_codes):
        return True
    return bool(name) and bool(re.search(config.excluded_industry_pattern, name, re.I))


def industry_known(industry: Mapping[str, Any] | None) -> bool:
    industry = industry or {}
    return bool(str(industry.get("industry_code") or industry.get("l1_code") or "").strip()) or bool(
        str(industry.get("industry_name") or "").strip()
    )


# --- individual red flags --------------------------------------------------


def flag_cash_conversion(latest: Mapping[str, Any], config: RuleConfig) -> FlagResult:
    """RF01 - reported profit not backed by operating cash flow."""

    profit = _value(latest, "net_profit")
    cash = _value(latest, "operating_cash_flow")
    evidence: dict[str, Any] = {
        "net_profit": profit,
        "operating_cash_flow": cash,
        "cash_flow_negative": None if cash is None else cash < 0,
    }
    if profit is None or cash is None:
        return FlagResult("cash_conversion", None, reason="missing_cash_flow_evidence", evidence=evidence)
    ratio = cash_conversion_ratio(cash, profit)
    evidence["cash_conversion_ratio"] = ratio
    if profit <= 0:
        # The rule is conditional on positive profit, so it is not triggered,
        # but a loss-making company with negative operating cash flow is still
        # recorded explicitly for downstream readers.
        evidence["note"] = "nonpositive_net_profit"
        return FlagResult("cash_conversion", False, threshold=config.cash_conversion_min, evidence=evidence)
    return FlagResult(
        "cash_conversion",
        ratio < config.cash_conversion_min,
        value=ratio,
        threshold=config.cash_conversion_min,
        evidence=evidence,
    )


def _divergence(
    name: str,
    latest: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    current_field: str,
    prior_field: str,
    growth_min: float,
    gap_min: float,
    missing_reason: str,
) -> FlagResult:
    if prior is None:
        return FlagResult(name, None, reason="insufficient_annual_history")
    current_value = _value(latest, current_field)
    prior_value = _value(prior, prior_field)
    revenue_now = _value(latest, "revenue")
    revenue_before = _value(prior, "revenue")
    evidence = {
        current_field: current_value,
        f"previous_{current_field}": prior_value,
        "revenue_growth": None,
        f"{current_field}_growth": None,
        "gap": None,
    }
    if None in (current_value, prior_value, revenue_now, revenue_before):
        return FlagResult(name, None, reason=missing_reason, evidence=evidence)
    item_growth = growth_rate(current_value, prior_value)
    revenue_growth = growth_rate(revenue_now, revenue_before)
    evidence["revenue_growth"] = revenue_growth
    evidence[f"{current_field}_growth"] = item_growth
    if item_growth is None or revenue_growth is None:
        return FlagResult(name, None, reason="nonpositive_previous_base", evidence=evidence)
    gap = item_growth - revenue_growth
    evidence["gap"] = gap
    state = item_growth > growth_min and gap > gap_min
    return FlagResult(name, state, value=gap, threshold=gap_min, evidence=evidence)


def flag_receivable_divergence(
    latest: Mapping[str, Any], prior: Mapping[str, Any] | None, config: RuleConfig
) -> FlagResult:
    """RF02 - receivables growing faster than revenue."""

    return _divergence(
        "receivable_divergence",
        latest,
        prior,
        "accounts_receivable",
        "accounts_receivable",
        config.receivable_growth_min,
        config.receivable_gap_min,
        "missing_receivable_evidence",
    )


def flag_inventory_divergence(
    latest: Mapping[str, Any], prior: Mapping[str, Any] | None, config: RuleConfig
) -> FlagResult:
    """RF03 - inventory growing faster than revenue."""

    return _divergence(
        "inventory_divergence",
        latest,
        prior,
        "inventory",
        "inventory",
        config.inventory_growth_min,
        config.inventory_gap_min,
        "missing_inventory_evidence",
    )


def flag_accrual_quality(
    latest: Mapping[str, Any], prior: Mapping[str, Any] | None, config: RuleConfig
) -> FlagResult:
    """RF04 - profit not supported by cash (accrual ratio)."""

    profit = _value(latest, "net_profit")
    cash = _value(latest, "operating_cash_flow")
    assets_now = _value(latest, "total_assets")
    assets_before = _value(prior, "total_assets") if prior else None
    average_assets = average(assets_now, assets_before) if prior else None
    evidence = {
        "net_profit": profit,
        "operating_cash_flow": cash,
        "average_total_assets": average_assets,
        "accrual_ratio": None,
    }
    if prior is None:
        return FlagResult("accrual_quality", None, reason="insufficient_annual_history", evidence=evidence)
    if profit is None or cash is None:
        return FlagResult("accrual_quality", None, reason="missing_accrual_evidence", evidence=evidence)
    if average_assets is None or average_assets <= 0:
        return FlagResult("accrual_quality", None, reason="missing_average_total_assets", evidence=evidence)
    ratio = accrual_ratio(profit, cash, average_assets)
    evidence["accrual_ratio"] = ratio
    return FlagResult(
        "accrual_quality",
        ratio > config.accrual_ratio_max,
        value=ratio,
        threshold=config.accrual_ratio_max,
        evidence=evidence,
    )


def flag_gross_margin_anomaly(
    annual: list[Mapping[str, Any]], config: RuleConfig
) -> FlagResult:
    """RF05 - gross margin deviating from the company's own history."""

    if not annual:
        return FlagResult("gross_margin_anomaly", None, reason="no_visible_annual_reports")
    by_year = {int(row["year"]): row for row in annual}
    latest_year = max(by_year)
    latest = by_year[latest_year]
    latest_margin = gross_margin(_value(latest, "revenue"), _value(latest, "operating_cost"))
    baseline_rows = _consecutive_prior(by_year, latest_year, config.gross_margin_max_history)
    baseline = [
        gross_margin(_value(row, "revenue"), _value(row, "operating_cost")) for row in baseline_rows
    ]
    evidence: dict[str, Any] = {
        "gross_margin_latest": latest_margin,
        "gross_margin_history": baseline,
        "gross_margin_historical_mean": None,
        "gross_margin_change": None,
        "gross_margin_zscore": None,
    }
    if latest_margin is None or len(baseline) < config.gross_margin_min_history or any(v is None for v in baseline):
        return FlagResult(
            "gross_margin_anomaly", None, reason="insufficient_gross_margin_history", evidence=evidence
        )
    mean, std = mean_and_std(baseline)
    assert mean is not None and std is not None
    change = latest_margin - mean
    z = z_score(latest_margin, mean, std)
    threshold = max(config.gross_margin_abs_change_min, config.gross_margin_zscore_min * std)
    evidence.update(
        {
            "gross_margin_historical_mean": mean,
            "gross_margin_change": change,
            "gross_margin_zscore": z,
            "gross_margin_threshold": threshold,
        }
    )
    return FlagResult(
        "gross_margin_anomaly",
        abs(change) > threshold,
        value=change,
        threshold=threshold,
        evidence=evidence,
    )


def flag_profit_revenue_divergence(
    latest: Mapping[str, Any], prior: Mapping[str, Any] | None, config: RuleConfig
) -> FlagResult:
    """RF06 - profit growing much faster than revenue."""

    if prior is None:
        return FlagResult("profit_revenue_divergence", None, reason="insufficient_annual_history")
    profit_now = _value(latest, "net_profit")
    profit_before = _value(prior, "net_profit")
    revenue_now = _value(latest, "revenue")
    revenue_before = _value(prior, "revenue")
    evidence = {
        "profit_growth": None,
        "revenue_growth": None,
        "growth_gap": None,
        "net_profit": profit_now,
        "previous_net_profit": profit_before,
    }
    if None in (profit_now, profit_before, revenue_now, revenue_before):
        return FlagResult("profit_revenue_divergence", None, reason="missing_profit_revenue_evidence", evidence=evidence)
    profit_growth = growth_rate(profit_now, profit_before)
    revenue_growth = growth_rate(revenue_now, revenue_before)
    evidence["profit_growth"] = profit_growth
    evidence["revenue_growth"] = revenue_growth
    if profit_growth is None or revenue_growth is None:
        return FlagResult("profit_revenue_divergence", None, reason="nonpositive_previous_base", evidence=evidence)
    gap = profit_growth - revenue_growth
    evidence["growth_gap"] = gap
    state = profit_growth > config.profit_growth_min and gap > config.profit_revenue_gap_min
    return FlagResult("profit_revenue_divergence", state, value=gap, threshold=config.profit_revenue_gap_min, evidence=evidence)


def flag_cash_conversion_deterioration(
    annual: list[Mapping[str, Any]], config: RuleConfig
) -> FlagResult:
    """RF07 - cash conversion falling for several consecutive years."""

    if not annual:
        return FlagResult("cash_conversion_deterioration", None, reason="no_visible_annual_reports")
    by_year = {int(row["year"]): row for row in annual}
    latest_year = max(by_year)
    window = _recent_consecutive(by_year, latest_year, config.deterioration_years)
    if len(window) < config.deterioration_years:
        return FlagResult(
            "cash_conversion_deterioration", None, reason="insufficient_annual_history"
        )
    series: list[float] = []
    for row in window:
        profit = _value(row, "net_profit")
        cash = _value(row, "operating_cash_flow")
        if profit is None or cash is None:
            return FlagResult(
                "cash_conversion_deterioration", None, reason="missing_cash_flow_evidence"
            )
        if profit <= 0:
            return FlagResult(
                "cash_conversion_deterioration",
                None,
                reason="nonpositive_net_profit_in_window",
                evidence={"cash_conversion_series": series},
            )
        series.append(cash / profit)
    decreasing = strictly_decreasing(series)
    state = bool(decreasing) and series[-1] < config.deterioration_latest_max
    return FlagResult(
        "cash_conversion_deterioration",
        state,
        value=series[-1],
        threshold=config.deterioration_latest_max,
        evidence={"cash_conversion_series": series, "strictly_decreasing": decreasing},
    )


# --- orchestration ---------------------------------------------------------


def evaluate_symbol(
    symbol: str,
    annual: list[dict[str, Any]],
    industry: Mapping[str, Any] | None,
    as_of: str,
    config: RuleConfig,
    conflicts: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Evaluate all seven flags and classify risk for one company."""

    symbol = clean_symbol(symbol) or str(symbol)
    # Defensive chronological sort: RF01/RF02/RF03/RF04/RF06 read the last two
    # rows positionally, so an unsorted caller must not silently change the
    # answer. The point-in-time loader already sorts, this is belt-and-braces.
    annual = sorted(annual, key=lambda row: int(row["year"]) if row.get("year") is not None else 0)
    conflicts = conflicts or set()
    own_conflicts = sorted(quarter for item_symbol, quarter in conflicts if item_symbol == symbol)
    global_reasons: list[str] = []

    if not industry_known(industry):
        global_reasons.append("missing_industry")
    if own_conflicts:
        global_reasons.append("conflicting_latest_revisions")
    if len(annual) < config.min_annual_reports:
        global_reasons.append("insufficient_annual_history" if annual else "no_visible_annual_reports")
    flags_adjusted = [row.get("if_adjusted") for row in annual]
    if any(value is None for value in flags_adjusted):
        global_reasons.append("missing_adjustment_flag")
    elif any(value not in (0, 1) for value in flags_adjusted):
        global_reasons.append("invalid_adjustment_flag")

    financial = is_financial_industry(industry, config)

    if financial:
        details = {
            name: FlagResult(name, None, reason="financial_industry_not_applicable").to_dict()
            for name in FLAG_NAMES
        }
        return _record(
            symbol=symbol,
            as_of=as_of,
            industry=industry,
            annual=annual,
            config=config,
            details=details,
            status=Status.NOT_APPLICABLE,
            risk_level=RiskLevel.NOT_APPLICABLE,
            global_reasons=["financial_industry_not_applicable"],
            conflicts=own_conflicts,
        )

    latest = annual[-1] if annual else None
    prior = annual[-2] if len(annual) >= 2 else None
    latest_year = int(latest["year"]) if latest else None
    prior_is_consecutive = bool(prior) and int(prior["year"]) == (latest_year - 1 if latest_year else None)
    effective_prior = prior if prior_is_consecutive else None

    # Fail closed on stale evidence: a company that has stopped filing must not
    # be classified as low risk from an annual report that is years old. This
    # is the freshness counterpart of the coverage floor.
    as_of_year = int(str(as_of)[:4]) if str(as_of)[:4].isdigit() else None
    if (
        as_of_year is not None
        and latest_year is not None
        and as_of_year - latest_year > config.max_evidence_age_years
    ):
        global_reasons.append("stale_annual_evidence")

    details = {
        "cash_conversion": flag_cash_conversion(latest, config).to_dict() if latest else FlagResult("cash_conversion", None, reason="no_visible_annual_reports").to_dict(),
        "receivable_divergence": flag_receivable_divergence(latest, effective_prior, config).to_dict() if latest else FlagResult("receivable_divergence", None, reason="no_visible_annual_reports").to_dict(),
        "inventory_divergence": flag_inventory_divergence(latest, effective_prior, config).to_dict() if latest else FlagResult("inventory_divergence", None, reason="no_visible_annual_reports").to_dict(),
        "accrual_quality": flag_accrual_quality(latest, effective_prior, config).to_dict() if latest else FlagResult("accrual_quality", None, reason="no_visible_annual_reports").to_dict(),
        "gross_margin_anomaly": flag_gross_margin_anomaly(annual, config).to_dict(),
        "profit_revenue_divergence": flag_profit_revenue_divergence(latest, effective_prior, config).to_dict() if latest else FlagResult("profit_revenue_divergence", None, reason="no_visible_annual_reports").to_dict(),
        "cash_conversion_deterioration": flag_cash_conversion_deterioration(annual, config).to_dict(),
    }
    if prior is not None and effective_prior is None:
        # A gap in the annual series is explicit evidence, not a silent skip.
        global_reasons.append("non_contiguous_annual_history")

    states = [details[name]["state"] for name in FLAG_NAMES]
    available = sum(state is not None for state in states)
    triggered = sum(state is True for state in states)
    coverage = available / len(FLAG_NAMES)
    if coverage < config.coverage_min:
        global_reasons.append("insufficient_coverage")

    if global_reasons:
        risk = RiskLevel.INSUFFICIENT_DATA
        status = Status.INSUFFICIENT_DATA
    elif triggered >= config.risk_high_flags:
        risk, status = RiskLevel.HIGH, Status.EVALUATED
    elif triggered >= config.risk_medium_flags:
        risk, status = RiskLevel.MEDIUM, Status.EVALUATED
    else:
        risk, status = RiskLevel.LOW, Status.EVALUATED

    return _record(
        symbol=symbol,
        as_of=as_of,
        industry=industry,
        annual=annual,
        config=config,
        details=details,
        status=status,
        risk_level=risk,
        global_reasons=global_reasons,
        conflicts=own_conflicts,
    )


def _first_present(
    details: Mapping[str, Mapping[str, Any]], names: Sequence[str], key: str
) -> Any:
    """First non-null value for ``key`` across several rule evidences.

    The divergence rules all derive revenue growth from the same two periods,
    so whichever rule had complete evidence first supplies it. This keeps the
    top-level ``revenue_growth`` from reading null just because receivables
    were missing while inventory and profit were present.
    """

    for name in names:
        value = details[name]["evidence"].get(key)
        if value is not None:
            return value
    return None


def _record(
    *,
    symbol: str,
    as_of: str,
    industry: Mapping[str, Any] | None,
    annual: list[dict[str, Any]],
    config: RuleConfig,
    details: dict[str, dict[str, Any]],
    status: Status,
    risk_level: RiskLevel,
    global_reasons: list[str],
    conflicts: list[str],
) -> dict[str, Any]:
    flags = {name: details[name]["state"] for name in FLAG_NAMES}
    states = list(flags.values())
    available = sum(state is not None for state in states)
    triggered = sum(state is True for state in states)
    coverage = available / len(FLAG_NAMES) if FLAG_NAMES else 0.0

    flag_missing = sorted({details[name]["reason"] for name in FLAG_NAMES if details[name]["reason"]})
    missing_reasons = sorted(set(global_reasons) | set(flag_missing))

    latest = annual[-1] if annual else {}
    prior = annual[-2] if len(annual) >= 2 else {}
    as_of_text = str(as_of)[:4]
    evidence_age_years = (
        int(as_of_text) - int(latest["year"])
        if latest and as_of_text.isdigit() and latest.get("year") is not None
        else None
    )
    cash_series = details["cash_conversion_deterioration"]["evidence"].get("cash_conversion_series")
    if cash_series is None:
        cash_series = [
            None if row.get("net_profit") in (None, 0) or row.get("net_profit", 0) <= 0
            else (row.get("operating_cash_flow") / row["net_profit"] if row.get("operating_cash_flow") is not None else None)
            for row in annual
        ]

    evidence = {
        "net_profit": latest.get("net_profit"),
        "operating_cash_flow": latest.get("operating_cash_flow"),
        "cash_conversion_ratio": details["cash_conversion"]["evidence"].get("cash_conversion_ratio"),
        "cash_flow_negative": details["cash_conversion"]["evidence"].get("cash_flow_negative"),
        "receivable_growth": details["receivable_divergence"]["evidence"].get("accounts_receivable_growth"),
        "revenue_growth": _first_present(
            details,
            ("receivable_divergence", "inventory_divergence", "profit_revenue_divergence"),
            "revenue_growth",
        ),
        "receivable_gap": details["receivable_divergence"]["evidence"].get("gap"),
        "inventory_growth": details["inventory_divergence"]["evidence"].get("inventory_growth"),
        "inventory_gap": details["inventory_divergence"]["evidence"].get("gap"),
        "accrual_ratio": details["accrual_quality"]["evidence"].get("accrual_ratio"),
        "gross_margin_latest": details["gross_margin_anomaly"]["evidence"].get("gross_margin_latest"),
        "gross_margin_historical_mean": details["gross_margin_anomaly"]["evidence"].get("gross_margin_historical_mean"),
        "gross_margin_change": details["gross_margin_anomaly"]["evidence"].get("gross_margin_change"),
        "profit_growth": details["profit_revenue_divergence"]["evidence"].get("profit_growth"),
        "growth_gap": details["profit_revenue_divergence"]["evidence"].get("growth_gap"),
        "cash_conversion_series": cash_series,
        "evidence_age_years": evidence_age_years,
    }

    return {
        "symbol": symbol,
        "as_of": as_of,
        "status": status.value,
        "risk_level": risk_level.value,
        "is_financial": risk_level is RiskLevel.NOT_APPLICABLE,
        "industry": dict(industry) if industry else None,
        "red_flag_count": triggered,
        "available_rule_count": available,
        "total_rule_count": len(FLAG_NAMES),
        "coverage_ratio": coverage,
        "flags": flags,
        "flag_details": details,
        "evidence": evidence,
        "latest_fiscal_year": latest.get("year"),
        "previous_fiscal_year": prior.get("year"),
        "annual_history": annual,
        "announcement_dates": sorted({row.get("announce_date") for row in annual if row.get("announce_date")}),
        "report_periods": [row.get("quarter") for row in annual],
        "report_versions": [
            {
                "quarter": row.get("quarter"),
                "announce_date": row.get("announce_date"),
                "if_adjusted": row.get("if_adjusted"),
            }
            for row in annual
        ],
        "conflicting_quarters": conflicts,
        "missing_reasons": missing_reasons,
        "rule_version": None,  # filled by the service with the run-level version
    }
