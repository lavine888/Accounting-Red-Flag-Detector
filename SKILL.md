---
name: accounting-red-flag-detector-lavine-version
description: "PandaData-only, point-in-time A-share accounting red-flag detector covering seven forensic rules (cash conversion, receivables/inventory divergence, accruals, gross-margin anomaly, profit-revenue divergence, multi-year cash-conversion deterioration) with three-state flags, fail-closed coverage and evidence freshness, financial-industry exclusion and auditable JSON/Parquet evidence. Use when an agent needs an accounting-quality screen, historical signal reconstruction or forward-return research without future information."
quantSkills:
  organization: https://github.com/lavine888
  repository: lavine888/Accounting-Red-Flag-Detector
  repository_url: https://github.com/lavine888/Accounting-Red-Flag-Detector
  project_type: skill
  collection: liangshuyuan-arfd
  license: GPL-3.0
  category: factor
  tags: [a-share, accounting, red-flags, forensic, point-in-time, screener]
  platforms: [claude-code, codex, openclaw]
  language: zh-en
  status: active
  validation_level: runnable
  maintainer_type: community
  requires: []
  summary_zh: 基于 PandaData 点时证据执行七条会计红旗筛查，缺失数据永不猜成 0。
  summary_en: PandaData-only point-in-time A-share accounting red-flag detector with auditable evidence.
---

```json qsh-form
{
  "version": 1,
  "task": {
    "placeholder": "例如：筛查贵州茅台和隆基绿能的会计红旗并解释每条规则",
    "required": true
  },
  "fields": [
    {
      "key": "as_of",
      "type": "date",
      "label": "决策日期"
    },
    {
      "key": "symbols",
      "type": "text",
      "label": "A股代码"
    },
    {
      "key": "scope",
      "type": "select",
      "label": "股票池",
      "options": [
        { "value": "symbols", "label": "指定代码" },
        { "value": "all_sh_sz", "label": "全部沪深A股" }
      ]
    }
  ],
  "prompt_template": "{{task}}；决策日：{{as_of}}；股票池：{{symbols}}（范围：{{scope}}）。只使用当时可见的 PandaData 年报证据。附件：{{#attachments}}"
}
```

# Accounting Red Flag Detector - Lavine Version

Use this skill to run a point-in-time accounting-quality screen over China A-shares. It rejects incomplete, conflicting, future-dated or non-PandaData inputs instead of filling missing values with zeros.

**Agent investigates. Rules decide. Evidence explains.**

## Core Workflow

1. Resolve an explicit A-share list or the point-in-time full SH/SZ universe.
2. Download PandaData financial statements in windows of at most 5 years (20 quarters), with `is_latest=false`.
3. Keep only the last report version announced on or before the decision date (`announcement_date <= as_of`). Two same-day filings with different values are a conflict and fail closed.
4. Normalize each visible period into annual (q4) evidence: revenue, operating cost, parent net profit, operating cash flow, receivables, inventory, total assets.
5. Resolve Shenwan L1 industry membership valid on the decision date. Financial industries (banking `801780`, non-bank financials `801790`, or matching names) return `not_applicable`.
6. Evaluate the seven red flags. Each is `true`, `false`, or `null` (missing evidence). Nothing is imputed.
7. Classify risk: 0–1 flags `low`, 2–3 `medium`, ≥ 4 `high`; coverage below 60% or evidence older than `max_evidence_age_years` (default 2 years) is `insufficient_data` (fail closed).
8. Emit JSON and/or the versioned production Parquet factor table with full evidence and provenance.

## Hard Rules

All thresholds live in `config/rules.yaml`; the engine never hard-codes a number.

| ID | Rule | Condition |
| --- | --- | --- |
| RF01 | Profit / cash-flow divergence | `CFO / net profit < 0.80` |
| RF02 | Receivables outgrowing revenue | `AR growth > 0.20` and `AR growth − revenue growth > 0.20` |
| RF03 | Inventory outgrowing revenue | `inventory growth > 0.20` and `inventory growth − revenue growth > 0.20` |
| RF04 | Excessive accruals | `(net profit − CFO) / average total assets > 0.10` |
| RF05 | Gross-margin anomaly | `|latest margin − prior mean| > max(0.05, 2σ)` over 3–5 prior years |
| RF06 | Profit / revenue divergence | `profit growth > 0.30` and `profit growth − revenue growth > 0.30` |
| RF07 | Multi-year cash deterioration | last 3 years `CFO / net profit` strictly decreasing and latest `< 0.80` |

## Evidence Discipline

- **Point-in-time**: selection is driven by `announcement_date`, never by fiscal period alone.
- **Three-state**: `null` means "no conclusion allowed", not "no problem".
- **No zero-fill**: a missing field never becomes `0`; growth rates over a non-positive base return `null`.
- **Fail-closed**: missing industry, conflicting revisions, missing adjustment flag, fewer than two annual reports, coverage below 60%, or a latest annual report older than `max_evidence_age_years` all produce `insufficient_data`.
- **Financials out of scope**: banks, insurers and brokers are `not_applicable`, never forced through industrial rules.
- **Auditable**: every record carries `flag_details` (value, threshold, reason), `evidence`, `announcement_dates`, `report_versions` and `missing_reasons`.

## Commands

```bash
python scripts/build.py --as-of 20251231 --symbols 600519.SH
python scripts/build.py --as-of 20251231 --all-sh-sz \
    --cache-dir output/panda-cache --request-interval 1.2 --workers 8 \
    --json-output output/red-flags.json \
    --parquet-output production/database.parquet
python scripts/validate.py output/red-flags.json
python scripts/backtest.py --signal-dates 20221230 20231229 20241231 \
    --all-sh-sz --output output/backtest.json
```

Credentials are read only from `PANDA_DATA_USERNAME` / `PANDA_DATA_PASSWORD` (and optional `PANDA_DATA_BASE_URL`), then removed from the process environment. They are never written to disk or logs.

## Output Contract

- **JSON**: run metadata (`dataset_version`, `rule_config_hash`, `source_snapshot`, `universe_hash`, `counts`, `diagnostics`) plus one record per company.
- **Parquet**: keyed by `(trade_date, factor_id, symbol)`, upserted, with `factor_value`, `score`, `rank`, `signal`, `confidence`, `risk_level`, `status`, `evidence_json`, `run_metadata_json` and version/provenance columns.

The validator re-derives every aggregate and every risk level, then re-runs the rule engine over each record's own `annual_history` to rebuild `flags`, `flag_details`, `evidence` and coverage. Tampered, truncated or internally inconsistent artifacts fail.

## Fail-Closed Contract

| Situation | Result |
| --- | --- |
| No visible annual report | `insufficient_data` (`no_visible_annual_reports`) |
| Fewer than two annual reports | `insufficient_data` (`insufficient_annual_history`) |
| Evidence coverage < 60% | `insufficient_data` (`insufficient_coverage`) |
| Latest annual report older than `max_evidence_age_years` | `insufficient_data` (`stale_annual_evidence`) |
| Same-day conflicting revisions | `insufficient_data` (`conflicting_latest_revisions`) |
| Missing industry membership | `insufficient_data` (`missing_industry`) |
| Missing / invalid adjustment flag | `insufficient_data` |
| Financial industry | `not_applicable` |

## Boundaries

Not investment advice. No return promises. Synthetic fixture data is never production evidence and can never be written to the canonical production database. V1 excludes quarterly reports, footnote text, audit opinions and related-party transactions.

## References

- `references/methodology.md` — rule definitions, boundary semantics, risk classification.
- `references/data_guide.md` — PandaData fields, fallbacks, cache and provenance.
- `references/source_boundary.md` — what is and is not claimed.
- `production/SKILL.md` — production deployment contract.
