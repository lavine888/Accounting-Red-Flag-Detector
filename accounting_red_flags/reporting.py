"""Render a validated screening result as a human/agent-readable Markdown report.

The report is a *view*: it never adds, drops or rewrites evidence. Every number
comes straight from the JSON result, and a missing value is printed as ``—``
rather than ``0``. It is deterministic, so the same result always renders the
same report.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

FLAG_LABELS = {
    "cash_conversion": "RF01 利润/现金流背离",
    "receivable_divergence": "RF02 应收账款增速超收入",
    "inventory_divergence": "RF03 存货增速超收入",
    "accrual_quality": "RF04 应计利润过高",
    "gross_margin_anomaly": "RF05 毛利率异常",
    "profit_revenue_divergence": "RF06 利润与收入增速背离",
    "cash_conversion_deterioration": "RF07 现金转化多年恶化",
}

RISK_LABELS = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "insufficient_data": "证据不足",
    "not_applicable": "金融业（不适用）",
}

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
TRIAGE_RISKS = ("high", "medium", "low")

EVIDENCE_ROWS = (
    ("净利润", "net_profit"),
    ("经营现金流", "operating_cash_flow"),
    ("CFO/净利", "cash_conversion_ratio"),
    ("收入增速", "revenue_growth"),
    ("应收增速", "receivable_growth"),
    ("应收-收入差", "receivable_gap"),
    ("存货增速", "inventory_growth"),
    ("存货-收入差", "inventory_gap"),
    ("应计比率", "accrual_ratio"),
    ("毛利率变化", "gross_margin_change"),
    ("利润增速", "profit_growth"),
    ("利润-收入差", "growth_gap"),
)


def _fmt(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "—" if value is None else ("true" if value else "false")
    if isinstance(value, float):
        if value != value:  # NaN
            return "—"
        return f"{value:.4g}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _condition(name: str, details: Mapping[str, Any]) -> str:
    value = details.get("value")
    threshold = _fmt(details.get("threshold"))
    if value is None and details.get("threshold") is None:
        return ""
    # RF05 compares |change| against a positive magnitude threshold.
    if name == "gross_margin_anomaly" and isinstance(value, (int, float)):
        return f"（|{_fmt(value)}| vs 阈值 {threshold}）"
    return f"（{_fmt(value)} vs 阈值 {threshold}）"


def _triage_records(records: Iterable[Mapping[str, Any]], min_risk: str) -> list[Mapping[str, Any]]:
    # TRIAGE_RISKS is ordered high -> low, so "at least min_risk" is a prefix.
    allowed = set(TRIAGE_RISKS[: TRIAGE_RISKS.index(min_risk) + 1])
    selected = [
        record
        for record in records
        if record.get("risk_level") in allowed
    ]
    selected.sort(key=lambda record: (RISK_ORDER.get(record.get("risk_level"), 9), record.get("symbol", "")))
    return selected


def _render_summary(result: Mapping[str, Any]) -> list[str]:
    counts = result.get("counts", {})
    status_counts = result.get("status_counts", {})
    diagnostics = result.get("diagnostics", {})
    lines = ["## 概览", "", "| 风险等级 | 数量 |", "| --- | --- |"]
    for level in ("high", "medium", "low", "insufficient_data", "not_applicable"):
        lines.append(f"| {RISK_LABELS[level]} | {counts.get(level, 0)} |")
    lines += [
        "",
        f"- 股票池：{result.get('universe_size', '—')}",
        f"- 已评估：{status_counts.get('evaluated', 0)}，"
        f"证据不足：{status_counts.get('insufficient_data', 0)}，"
        f"金融业排除：{status_counts.get('not_applicable', 0)}",
        f"- 平均覆盖率：{_pct(diagnostics.get('evaluated_coverage_mean'))}",
    ]
    triggers = diagnostics.get("flag_trigger_counts") or {}
    fired = [name for name in FLAG_LABELS if triggers.get(name)]
    if fired:
        ordered = [f"{FLAG_LABELS[name]}={triggers[name]}" for name in fired]
        lines.append(f"- 规则命中：{'，'.join(ordered)}")
    reasons = diagnostics.get("insufficient_reason_counts") or {}
    if reasons:
        ordered = [f"{name}×{reasons[name]}" for name in sorted(reasons)]
        lines.append(f"- 缺失/排除原因：{'，'.join(ordered)}")
    return lines


def _render_record(record: Mapping[str, Any]) -> list[str]:
    symbol = record.get("symbol", "?")
    risk = record.get("risk_level")
    flags = record.get("red_flag_count", "—")
    coverage = _pct(record.get("coverage_ratio"))
    industry = record.get("industry") or {}
    industry_name = industry.get("industry_name") or "—"
    industry_code = industry.get("industry_code") or industry.get("l1_code") or "—"
    lines = [
        f"### {symbol} — {RISK_LABELS.get(risk, risk)}（{flags} 条红旗，覆盖率 {coverage}）",
        "",
        f"- 行业：{industry_name}（{industry_code}）",
    ]
    triggered = [
        (name, details)
        for name, details in (record.get("flag_details") or {}).items()
        if details.get("state") is True
    ]
    if triggered:
        lines.append("- 命中规则：")
        for name, details in triggered:
            lines.append(f"  - {FLAG_LABELS.get(name, name)}{_condition(name, details)}")
    else:
        lines.append("- 命中规则：无")

    evidence = record.get("evidence") or {}
    shown = [f"{label} {_fmt(evidence.get(key))}" for label, key in EVIDENCE_ROWS if evidence.get(key) is not None]
    if shown:
        lines.append(f"- 关键证据：{'；'.join(shown)}")

    peer = record.get("peer_context") or {}
    peer_metrics = peer.get("metrics") or {}
    if peer_metrics:
        parts = []
        for name in sorted(peer_metrics):
            metric = peer_metrics[name]
            parts.append(
                f"{name} {_pct(metric.get('peer_percentile'))}"
                f"（中位 {_fmt(metric.get('peer_median'))}, n={metric.get('peer_count')}）"
            )
        lines.append(f"- 行业相对分位（仅供参考，不改变判定）：{'；'.join(parts)}")

    unknown = [
        f"{FLAG_LABELS.get(name, name)}={details.get('reason')}"
        for name, details in (record.get("flag_details") or {}).items()
        if details.get("state") is None
    ]
    missing = record.get("missing_reasons") or []
    if unknown or missing:
        parts = list(missing)
        parts += [f"无法判定：{item}" for item in unknown]
        lines.append(f"- 缺失/无法判定：{'；'.join(parts)}")
    return lines


def _render_insufficient(records: list[Mapping[str, Any]]) -> list[str]:
    if not records:
        return []
    lines = [
        "",
        "## 证据不足（需人工补充，不代表安全）",
        "",
        "| 代码 | 行业 | 覆盖率 | 原因 |",
        "| --- | --- | --- | --- |",
    ]
    for record in sorted(records, key=lambda item: item.get("symbol", "")):
        industry = record.get("industry") or {}
        code = industry.get("industry_code") or industry.get("l1_code") or "—"
        reasons = "，".join(record.get("missing_reasons") or []) or "—"
        lines.append(
            f"| {record.get('symbol', '?')} | {code} | {_pct(record.get('coverage_ratio'))} | {reasons} |"
        )
    return lines


def _render_provenance(result: Mapping[str, Any]) -> list[str]:
    return [
        "",
        "## 可追溯性",
        "",
        f"- 决策日：{result.get('as_of', '—')}",
        f"- 数据集版本：{result.get('dataset_version', '—')}",
        f"- 数据来源：{result.get('data_source', '—')}",
        f"- rules / schema：{result.get('rules_version', '—')} / {result.get('schema_version', '—')}",
        f"- rule_config_hash：{str(result.get('rule_config_hash', ''))[:16]}",
        f"- source_snapshot：{str(result.get('source_snapshot', ''))[:16]}",
        f"- run_id：{result.get('run_id', '—')}",
    ]


def render_report(
    result: Mapping[str, Any],
    *,
    min_risk: str = "medium",
    limit: int | None = None,
) -> str:
    """Render a screening result as deterministic Markdown.

    ``min_risk`` selects the triage floor (``high`` < ``medium`` < ``low``);
    ``limit`` caps how many triage records are printed after sorting by risk
    then symbol. Missing evidence is always printed as ``—``, never as ``0``.
    """

    if min_risk not in TRIAGE_RISKS:
        raise ValueError(f"min_risk must be one of {TRIAGE_RISKS}, got {min_risk!r}")

    records = list(result.get("records", []))
    evaluated = [record for record in records if record.get("status") == "evaluated"]
    insufficient = [record for record in records if record.get("status") == "insufficient_data"]
    triage = _triage_records(evaluated, min_risk)
    total_triage = len(triage)
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        triage = triage[:limit]

    lines = ["# 会计红旗筛查报告", ""]
    if result.get("requires_live_validation"):
        lines += [
            "> **注意：合成/演示数据。** 本结果 `requires_live_validation=true`，不可用于生产判断。",
            "",
        ]
    lines += _render_summary(result)
    lines += ["", f"## 待核查清单（风险 ≥ {RISK_LABELS[min_risk]}，共 {total_triage} 只）", ""]
    if not triage:
        lines.append("_无匹配记录。_")
    for record in triage:
        lines += _render_record(record)
        lines.append("")
    if limit is not None and total_triage > len(triage):
        lines += [f"_（仅显示前 {len(triage)} 只，共 {total_triage} 只。）_", ""]
    lines += _render_insufficient(insufficient)
    lines += _render_provenance(result)
    lines += [
        "",
        "> 风险等级是**证据强度**的分级，不是投资建议，也不是收益预测。"
        "`insufficient_data` 表示证据不足，绝不等于低风险。",
        "",
    ]
    return "\n".join(lines)
