# Production Contract · Accounting Red Flag Detector

本文件定义**生产运行**的契约。任何写入 `production/database.parquet` 的运行都必须满足全部条款。

---

## 1. 允许的生产运行

```bash
export PANDA_DATA_USERNAME=...
export PANDA_DATA_PASSWORD=...
export PANDA_DATA_BASE_URL=http://pandadata.pandaaiquant.com

python scripts/build.py \
    --as-of 20251231 \
    --all-sh-sz \
    --provider pandadata \
    --cache-dir output/panda-cache \
    --request-interval 1.2 --workers 8 \
    --json-output output/red-flags-20251231.json \
    --parquet-output production/database.parquet
```

**强制条件**（`scripts/build.py::run` 会硬性拒绝违规写入）：

| 条件 | 违规结果 |
| --- | --- |
| `--all-sh-sz` 全市场 | 部分股票池写 `database.parquet` → `ValueError` |
| `--provider pandadata` | `fixture` 合成数据写 `database.parquet` → `ValueError` |
| 凭证来自环境变量 | 不得硬编码、不得提交 |
| 校验器 `PASS` | 必须先校验再发布 |

---

## 2. 生产验收清单

```bash
# 1. 生成
python scripts/build.py --as-of <AS_OF> --all-sh-sz \
    --cache-dir output/panda-cache --request-interval 1.2 --workers 8 \
    --json-output output/red-flags-<AS_OF>.json \
    --parquet-output production/database.parquet

# 2. 校验 JSON 与 Parquet（两者都必须 PASS）
python scripts/validate.py output/red-flags-<AS_OF>.json
python scripts/validate.py production/database.parquet

# 3. 检查关键指标
python - <<'PY'
import json
r = json.load(open("output/red-flags-<AS_OF>.json", encoding="utf-8"))
print("counts       ", r["counts"])
print("status       ", r["status_counts"])
print("universe     ", r["universe_size"])
print("coverage mean", r["diagnostics"]["evaluated_coverage_mean"])
print("reasons      ", r["diagnostics"]["insufficient_reason_counts"])
print("source       ", r["source_response_count"], r["source_snapshot"][:16])
print("dataset      ", r["dataset_version"])
PY
```

**验收标准**：

- `requires_live_validation == false`
- `data_source == "PandaData"`
- `validate.py` 对 JSON 与 Parquet 都返回 `PASS`
- `universe_size` 与当日沪深 A 股数量同量级
- `insufficient_data` 占比有合理解释（见 `diagnostics.insufficient_reason_counts`）
- `source_snapshot` 非空且长度 64

---

## 3. 产物契约

### Parquet 主键与 upsert

主键 `(trade_date, factor_id, symbol)`。重复运行是 upsert：同键旧行被替换，其他日期保留。
若 schema 变化（列集合不同），写入被拒绝，需要显式迁移后使用 `--replace-production`。

### 必填列

`trade_date`、`asset_type`、`symbol`、`factor_id`、`factor_name`、`factor_value`、`score`、
`rank`、`signal`、`confidence`、`risk_level`、`status`、`red_flag_count`、
`available_rule_count`、`coverage_ratio`、`evidence_json`、`run_metadata_json`、
`data_source`、`data_version`、`rules_version`、`rule_config_hash`、`run_id`、
`source_snapshot`、`data_sdk_version`、`runtime_versions_json`、`schema_version`、`update_time`。

### 语义

- `factor_value`：红旗数量。**仅 `evaluated` 行有值**，其他为 null。
- `score`：红旗数 / 有效规则数。非 `evaluated` 为 NaN。
- `rank`：`evaluated` 行内按红旗数降序排名。非 `evaluated` 为 null。
- `signal`：`review` / `watch` / `clear` / `unknown` / `not_applicable`，必须与 `risk_level` 一致。
- `confidence`：覆盖率，直接暴露证据充分程度。

---

## 4. 版本与迁移

| 变更 | 影响 |
| --- | --- |
| 阈值变更 | `rule_config_hash` 变更 → 旧产物校验失败 |
| 字段契约变更 | `SCHEMA_VERSION` 提升 → 需要 schema 迁移 |
| 规则逻辑变更 | `RULES_VERSION` 提升 → 需要重跑全部历史 |
| 数据字段变更 | `REPORT_FIELDS` 变更 → 缓存命名空间变更，自动失效 |

**禁止**：修改产物内容、手工修补计数、混用不同 `rules_version` 的行。

---

## 5. 可追溯性

任一生产行都可以反查到：

- `run_id` → 一次具体运行
- `dataset_version` → `as_of` + `schema_version` + `rule_config_hash` + `universe_hash` + `source_snapshot`
- `source_snapshot` → 本次运行实际使用的所有 PandaData 响应的内容哈希
- `runtime_versions_json` → `panda_data` / `pandas` / `numpy` / `pyarrow` 版本
- `evidence_json` → 逐股完整证据（含 `flag_details` 的 value / threshold / reason）

---

## 6. 失败处理

| 失败 | 处理 |
| --- | --- |
| `AuthenticationError` | 检查环境变量；不要重试到锁定 |
| 限流（`500010`） | 提高 `--request-interval`，降低 `--workers` |
| 缓存损坏 | provider 校验 `frame_sha256`，不匹配自动重取 |
| 校验 `FAIL` | **不得发布**；定位到具体 `errors` 后重跑 |
| `insufficient_data` 异常升高 | 检查 `insufficient_reason_counts`，通常是行业数据缺失 |

---

## 7. 不做什么

- 不自动发布、不自动推送外部系统。
- 不把 `insufficient_data` 降级成 `low` 来"提高覆盖率"。
- 不用 fixture 数据生成生产产物。
- 不提交 `output/`、缓存、凭证或全市场 Parquet 到公开仓库。
