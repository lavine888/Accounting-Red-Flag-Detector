# Changelog

## 1.4.0 - 2026-09-19

**主题：可读报告渲染（report renderer）。** 让产物自己解释自己，且不新增任何
不可校验的推断。

### 新增：`scripts/report.py` + `accounting_red_flags/reporting.py`

- 将**已校验**的 JSON 结果渲染为确定性 Markdown：概览（风险/状态计数、平均覆盖率、
  规则命中、缺失/排除原因）、待核查清单（按风险与代码排序；每条命中规则给出
  `value` vs 阈值、关键证据、行业相对分位）、证据不足清单、可追溯性（数据集版本 /
  rules / schema / 哈希 / run_id）。
- **只读视图**：不新增、不丢弃、不重写任何证据。缺失值一律渲染为 `—`，绝不渲染成 `0`；
  `insufficient_data` 单独成段，并显式标注“不代表安全”。
- **fail closed**：`scripts/report.py` 先调用严格校验器，未通过则拒绝渲染并返回非零退出码。
- `--min-risk {high,medium,low}`（默认 `medium` = 高+中）、`--limit N`、`--output FILE`。
- 合成数据结果会在报告顶部显式标注 `requires_live_validation=true`。

### 版本

- `__version__` / `skill.json.version` `1.3.0` → `1.4.0`。
- `RULES_VERSION` `1.2.0`、`SCHEMA_VERSION` `1.3.0` **不变**：本次不改变引擎输出，
  也不改变 JSON/Parquet 字段契约，因此 `dataset_version` 不变。

### 测试

- 新增 13 个测试（`tests/test_reporting.py`）：概览/清单/可追溯性、合成数据标注、
  `min_risk` 过滤、`limit` 截断、证据不足清单、金融业不入清单、缺失值不渲染为 `0`、
  RF05 绝对值展示、`peer_context` 展示、非法 `min_risk` 报错、确定性、CLI 成功写入、
  CLI 拒绝未校验产物。测试 176 → 189。

## 1.3.0 - 2026-09-19

**主题：行业相对证据（peer context）。** 回应“行业中性化缺失”的已知局限，但以
**只增不减、不改变判定**的方式落地。

### 新增：`peer_context`（附加行业相对证据）

- RF02/RF03/RF04/RF06 都是与公司自身上年比较，地产、建筑、To-G 等行业的应收
  账款、存货、应计天然偏高，绝对阈值可能误伤。每条记录现在附带 `peer_context`：
  该公司在**申万一级同行业**中的 `peer_count` / `peer_median` / `peer_percentile`。
- 参与比较的指标：`receivable_gap`、`inventory_gap`、`accrual_ratio`、`growth_gap`。
  `peer_percentile` = 同行业中严格小于本公司取值的样本占比。
- 样本池为所有非金融、且该指标有限的公司；金融业 `not_applicable` 不入池。行业样本
  < `peer_min_sample`（默认 5）时该指标直接缺省，绝不猜测。
- **不改变任何旗标、`risk_level` 或 `signal`**。系统性造假往往整条产业链同时异常，
  用同行“洗白”会掩盖工具本该暴露的案例；`peer_context` 只供调查者解读。
- 校验器从**重算证据**独立复算 `peer_context`，篡改即 FAIL。

### 修复：顶层 `revenue_growth` 的证据完整性

- 此前若应收账款缺失（RF02 无法计算）但存货/利润可用，顶层
  `evidence.revenue_growth` 会错误地读作 `null`。现在跨 RF02/RF03/RF06 取首个
  非空值，不再因单一规则缺证据而丢失已可得的收入增速。此修复不改变任何判定。

### 版本

- `RULES_VERSION` `1.1.0` → `1.2.0`（引擎输出变化：顶层证据跨规则回退）。
- `SCHEMA_VERSION` `1.2.0` → `1.3.0`（每条记录新增 `peer_context`）。
- `dataset_version` 前缀随之变化，旧产物需用新代码重跑。

### 测试

- 新增 11 个测试：`peer_contexts` 纯函数（中位数/分位、最小样本、非有限值、金融与
  无行业排除、确定性）、服务层（分位上报且可校验、不改变旗标、样本不足时缺省）、
  校验器拒绝篡改 `peer_context`、`revenue_growth` 跨规则回退。测试 165 → 176。

## 1.2.0 - 2026-09-19

**主题：证据新鲜度 fail-closed。** 新增一条 fail-closed 护栏，并相应提升规则与 schema 版本。

### 修复：陈旧证据被当成低风险

- 此前，只要覆盖率达标，一家最新可见年报已过去多年的公司（例如 `as_of=20251231`
  而最新年报为 FY2021）会被评为 `evaluated / low`。停止披露、长期停牌、严重延迟
  申报的公司因此被错误地标记为"干净"。
- 新增全局 fail-closed 原因 `stale_annual_evidence`：当
  `as_of` 年份 − 最新年报年份 > `max_evidence_age_years`（默认 2）时，状态与风险
  等级均为 `insufficient_data`。这是覆盖率下限的"新鲜度"对偶。
- 证据年龄写入 `evidence.evidence_age_years`，下游可审计。
- 阈值 `max_evidence_age_years` 位于 `config/rules.yaml`，可配置；金融业分支不受影响。

### 版本

- `RULES_VERSION` `1.0.0` → `1.1.0`（新增一条 fail-closed 规则语义）。
- `SCHEMA_VERSION` `1.1.0` → `1.2.0`（`evidence` 新增 `evidence_age_years`）。
- `dataset_version` 前缀随之变化，旧产物需用新代码重跑。

### 测试

- 新增 5 个测试：陈旧证据 fail-closed、年龄边界（恰好 2 年通过、3 年拒绝）、
  `max_evidence_age_years` 可配置、金融业不参与新鲜度检查、校验器拒绝把陈旧记录
  改写为 `evaluated`。测试 160 → 165。

## 1.1.0 - 2026-09-19

**主题：审计完整性（audit integrity）。** 本版不改变任何规则阈值，`RULES_VERSION`
仍为 `1.0.0`；但产物的可校验强度显著提升，`SCHEMA_VERSION` 提升至 `1.1.0`。

### 校验器从"比对聚合"升级为"重跑引擎"

- 校验器现在使用每条记录自带的 `annual_history`、`industry`、`conflicting_quarters`
  与顶层 `thresholds` **重新运行七条规则引擎**，逐字段重建 `status`、`risk_level`、
  `flags`、`flag_details`、`evidence`、`red_flag_count`、`available_rule_count`、
  `coverage_ratio`、`latest_fiscal_year`、`missing_reasons` 等 21 个字段。
- 即使同步篡改记录与顶层聚合，只要不能完整复现引擎输出，校验仍然失败。
- JSON 校验新增：`diagnostics.risk_level_counts`、`industry_coverage`、
  `financial_excluded`、`evaluated_coverage_mean` 的重推导；`requires_live_validation`
  与 `data_source` 的一致性（PandaData 是唯一 live 数据源）；`source_snapshot` 非空。
- Parquet 校验新增：按 `trade_date` 重建完整 result 并调用 JSON 校验器；
  重算 `factor_value`、`score`、`rank`、`confidence`；校验 `available_rule_count`、
  `coverage_ratio`、`evidence_json` 与行值一致；检测同一 `trade_date` 内 run 元数据不一致。

### 可追溯性

- `dataset_version` 现在同时绑定 `rules_version`：规则逻辑版本变化（即使阈值不变）
  会产生新的 `dataset_version`，不再出现"不同引擎、同一版本号"。
- `rules.yaml` 中的 `rules_version` / `schema_version` 不再被静默忽略；与运行时不一致
  直接报错，杜绝"配置文件声称的版本"与"实际执行的版本"脱节。

### 健壮性

- `evaluate_symbol` 对 `annual` 做防御性时间排序，不再依赖调用方传入有序历史。
- `scripts/build.py` 的 JSON 输出改用原子写入（先写临时文件再 `replace`），
  与 Parquet 写入语义一致。
- 移除 `service.py` 中未使用的 `_serialize_thresholds` 死代码。

### 文档

- 新增 `CHANGELOG.md` 与 `ROADMAP.md`。
- 更新 `README.md` / `README.en.md` / `references/methodology.md` /
  `references/source_boundary.md` / `production/SKILL.md` 的版本号、`dataset_version`
  公式与测试数量。

### 迁移

- `SCHEMA_VERSION` 由 `1.0.0` 提升至 `1.1.0`，`dataset_version` 前缀随之变化。
  旧产物会因 `schema_version does not match runtime` 而校验失败，属于预期的显式迁移：
  使用新代码重跑并重新校验即可。

## 1.0.0

- 首个版本：七条会计红旗规则、三态旗标、覆盖率 fail-closed、金融业排除、
  点时筛选、PandaData provider（鉴权 / 缓存 / 限速 / 重试 / 溯源）、
  JSON + Parquet 产物契约、严格校验器、前瞻收益研究回测与 148 个测试。
