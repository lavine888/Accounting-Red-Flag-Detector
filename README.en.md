# Accounting Red Flag Detector · Lavine Version

> **Agent investigates. Rules decide. Evidence explains.**
> Missing data is never guessed as zero.

A **point-in-time** accounting-quality screener for China A-shares. It applies seven red-flag rules to each company's own annual report history and emits auditable JSON / Parquet artifacts plus a forward-return research backtest.

- **Point-in-time**: only report versions with `announcement_date <= as_of` are used. No look-ahead.
- **Three-state flags**: `true` / `false` / `null`. Missing evidence stays `null`, never `0`.
- **Fail-closed**: evidence coverage below 60% becomes `insufficient_data`, never `low` risk.
- **Financials excluded**: banks, insurers and brokers return `not_applicable`.
- **All thresholds live in `config/rules.yaml`.** No magic numbers in code.

---

## 1. The seven red flags

`latest` is the most recent visible annual (q4) report; `prior` is the year before it.

| ID | Name | Condition | Default threshold |
| --- | --- | --- | --- |
| **RF01** | Profit / cash-flow divergence | `CFO / net profit < cash_conversion_min` | `0.80` |
| **RF02** | Receivables outgrowing revenue | `AR growth > 0.20` **and** `AR growth − revenue growth > 0.20` | `0.20 / 0.20` |
| **RF03** | Inventory outgrowing revenue | `inventory growth > 0.20` **and** `inventory growth − revenue growth > 0.20` | `0.20 / 0.20` |
| **RF04** | Excessive accruals | `(net profit − CFO) / average total assets > accrual_ratio_max` | `0.10` |
| **RF05** | Gross-margin anomaly | `|latest margin − historical mean| > max(0.05, 2σ)` | `0.05 / 2.0` |
| **RF06** | Profit / revenue growth divergence | `profit growth > 0.30` **and** `profit growth − revenue growth > 0.30` | `0.30 / 0.30` |
| **RF07** | Multi-year cash-conversion deterioration | last 3 years `CFO / net profit` **strictly decreasing** and latest `< 0.80` | `3y / 0.80` |

### Risk levels

| Red flags | Risk | Production signal |
| --- | --- | --- |
| 0–1 | `low` | `clear` |
| 2–3 | `medium` | `watch` |
| ≥ 4 | `high` | `review` |
| coverage < 60% | `insufficient_data` | `unknown` |
| financial industry | `not_applicable` | `not_applicable` |

Risk level grades **evidence strength**, not expected return. It is not investment advice.

---

## 2. Quick start

Python 3.11.

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt

export PANDA_DATA_USERNAME=...
export PANDA_DATA_PASSWORD=...
export PANDA_DATA_BASE_URL=http://pandadata.pandaaiquant.com   # optional
```

```bash
# one company
python scripts/build.py --as-of 20251231 --symbols 600519.SH

# full SH/SZ market
python scripts/build.py --as-of 20251231 --all-sh-sz \
    --cache-dir output/panda-cache --request-interval 1.2 --workers 8 \
    --json-output output/red-flags.json \
    --parquet-output production/database.parquet

# offline synthetic demo (NOT real results)
python scripts/build.py --as-of 20251231 --all-a --provider fixture

# validate
python scripts/validate.py output/red-flags.json
python scripts/validate.py production/database.parquet

# research backtest
python scripts/backtest.py --signal-dates 20221230 20231229 20241231 \
    --all-sh-sz --output output/backtest.json
```

The validator **re-derives** every aggregate and every risk level. Hand-edited, truncated or version-mismatched artifacts fail.

`--provider fixture` uses synthetic demo data: results carry `requires_live_validation=true` and cannot be written to the canonical `production/database.parquet`.

---

## 3. Output contract

### JSON

Top-level run metadata plus one record per company, including `flags` (three-state), `flag_details` (value / threshold / reason), `evidence`, `announcement_dates`, `missing_reasons` and `rule_version`.

### Parquet (production factor table)

Keyed by `(trade_date, factor_id, symbol)`; re-runs upsert instead of duplicating.

Columns: `factor_value`, `score`, `rank`, `signal`, `confidence`, `risk_level`, `status`, `evidence_json`, `run_metadata_json`, `rules_version`, `rule_config_hash`, `schema_version`, `run_id`, `source_snapshot`, `data_sdk_version`.

---

## 4. Data source and boundaries

- Single source: **PandaData** (`panda_data` SDK); credentials come from environment variables only and are removed from the process environment after loading.
- Fields used: `is_revenue`, `is_oper_cost`, `is_n_income_attr_p`, `cfs_net_cash_operating`, `bs_net_accts_receive`, `bs_inventory`, `bs_total_assets`, plus `date` (announcement), `quarter`, `if_adjusted`.
- **V1 uses annual (q4) reports only.** At q4 PandaData reports full-year cumulative statements.
- A missing adjustment flag is recorded as missing evidence, never assumed.

See `references/methodology.md`, `references/data_guide.md`, `references/source_boundary.md`.

---

## 5. Structure

```
accounting_red_flags/
  config.py models.py metrics.py rules.py service.py util.py
  point_in_time/{reports,universe}.py
  providers/{base,pandadata,fixture}.py
  materialization/{json_writer,parquet_writer}.py
  research/{forward_returns,diagnostics,backtest}.py
config/rules.yaml
scripts/{build,validate,backtest}.py
tests/               148 tests
references/          methodology / data guide / source boundary
production/SKILL.md  production deployment contract
```

---

## 6. Tests

```bash
python -m pytest -q
```

Covers threshold boundaries, missing-value handling, point-in-time selection, same-day revision conflicts, financial exclusion, coverage fail-closed, risk-classification boundaries, Parquet upsert, and adversarial validator tests.

---

## 7. Explicitly out of scope

No investment advice. No return promises. No synthetic data presented as real performance. No future data. No `null` treated as `false`. No `insufficient_data` treated as `low`. V1 excludes quarterly reports, footnote text, audit opinions and related-party transactions.

---

## 8. License

GPL-3.0, see [`LICENSE`](LICENSE).
