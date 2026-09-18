# 来源边界 · Source Boundary

本文件明确说明本项目**声称什么**、**不声称什么**，以及每条结论的证据等级。

---

## 1. 声称（可以对外说的）

| 声明 | 证据等级 | 依据 |
| --- | --- | --- |
| 规则引擎是确定性的、可复现的 | 已用真实数据验证 | 165 个测试；同一输入产生同一 `dataset_version` |
| 缺失或陈旧的证据不会被当成低风险 | 已用真实数据验证 | 覆盖率低于 60% 或最新年报超过 `max_evidence_age_years` 一律 fail closed 为 `insufficient_data` |
| 产物被篡改后无法通过校验 | 已用真实数据验证 | 校验器从 `annual_history` 重跑引擎并逐字段比对；诊断、`score`/`rank`/`confidence`、数据来源一致性均在覆盖范围内 |
| 时点筛选不使用未来信息 | 已用真实数据验证 | `select_visible_revisions` 按 `announcement_date` 过滤；测试覆盖未来报告排除 |
| 缺失数据不会被当作 0 | 已用真实数据验证 | 三态旗标 + 覆盖率 fail-closed；对抗性测试 |
| 输出可被独立校验 | 已用真实数据验证 | `scripts/validate.py` 重新推导全部聚合与风险等级 |
| 生产 Parquet 是幂等 upsert | 已用真实数据验证 | `(trade_date, factor_id, symbol)` 主键测试 |
| 全市场跑通 | 已用真实数据验证（子集） | 真实 PandaData 登录 + 20 只样本端到端 + 3 个信号日回测 |

---

## 2. 不声称（禁止对外说的）

| 禁止声明 | 原因 |
| --- | --- |
| "能预测股价 / 有超额收益" | 回测样本量过小，分组统计无统计显著性 |
| "高红旗组收益更低" | 仅在极小样本上观察到，不足以成为结论 |
| "合成数据结果代表真实表现" | fixture 是演示数据，明确标记 `requires_live_validation` |
| "覆盖全部会计舞弊类型" | V1 只有 7 条规则，不含附注文本、审计意见、关联交易、股权质押 |
| "可以直接用于实盘下单" | 无交易成本、流动性、涨跌停、停牌建模；不构成投资建议 |
| "季报 / TTM 已覆盖" | V1 只用年报（q4） |

---

## 3. 真实数据验证记录

以下验证在真实 PandaData 上执行（凭证来自环境变量，未落盘）：

| 验证项 | 结果 |
| --- | --- |
| `panda_data.init_token` 鉴权 | 成功 |
| `get_fina_reports` 字段探测 | 返回约 322 列，q4 为全年累计口径 |
| 贵州茅台 2023q4 毛利率 | ≈ 0.92（与公开年报一致） |
| 银行股 `is_oper_cost` | 为 `NaN`（验证金融业排除的必要性） |
| 4 只样本时点筛查 | 茅台 `low`、宁德时代 `low`、工商银行/平安银行 `not_applicable` |
| 20 只样本 + JSON + Parquet | 校验器均 `PASS`（14 `evaluated` / 6 `not_applicable`） |
| 3 个信号日回测管线 | 端到端跑通，收益覆盖率 100% |
| 全市场股票池发现 | `get_stock_detail` 返回 **5182** 只沪深 A 股（`as_of=20251231`） |
| 300 只随机样本时点筛查 | `evaluated` 279 / `insufficient_data` 13 / `not_applicable` 8；平均覆盖率 **0.9324**；`low` 199 / `medium` 74 / `high` 6 |
| 300 只样本缺失原因分布 | `missing_industry` 9、`insufficient_coverage` 4、`missing_inventory_evidence` 3、`insufficient_gross_margin_history` 1、`missing_receivable_evidence` 1 |

### 缺失原因的读法

上表中 `nonpositive_net_profit_in_window`（94）与 `nonpositive_previous_base`（55）是**旗标级**
缺失原因，出现在 `flag_details` 里，不必然导致 `insufficient_data`——只要整体覆盖率仍 ≥ 60%，
结果仍是 `evaluated`。真正把股票推向 `insufficient_data` 的是**全局**原因
（`missing_industry`、`insufficient_coverage`、`insufficient_annual_history` 等）。
这正是三态设计的目的："某一项算不出来"与"整体不可判断"是两回事。

### 网络健壮性

全市场 `get_stock_detail` 负载较大，实测出现过 `IncompleteRead` 与超时。provider 对
瞬时网络错误（`IncompleteRead` / timeout / connection reset）做指数退避重试，并把
该接口的超时提高到 180 秒、只请求 `symbol / listed_date / de_listed_date` 三列。

### 样本量警示

20 只样本、3 个信号日的回测中，`high` 组只有 **2 个观测**。
任何基于它的收益结论都不具备统计意义。回测模块的定位是**管线验证与未来研究脚手架**，
不是策略绩效证明。

---

## 4. 已知数据限制

1. **仅申万一级行业**：更细的行业中性化未实现。
2. **应收账款 fallback**：`bs_net_accts_receive` 缺失时用 `bs_notes_accts_receiv`
   （含应收票据），口径略有差异，已在字段表中记录。
3. **复权标记**：`if_adjusted` 缺失时 fail closed，而不是假设未调整。
4. **退市股票**：回测未处理退市清算价值。
5. **财报重述**：只取 `as_of` 前最后可见版本；重述前的原始版本不保留（PandaData
   通过 `is_latest=False` 提供历史版本，但同一期只能选一个）。

---

## 5. 合规与伦理

- 本项目**不构成投资建议**，不提供买卖信号，不承诺收益。
- 风险等级是**证据强度**分级，不是收益预期。
- 所有输出必须附带 `requires_live_validation`、`data_source`、`dataset_version`，
  以便下游区分真实运行与演示运行。
- 使用第三方数据须遵守其服务条款。

---

## 6. 与参考项目的关系

工程规范参考了 `skill-buffett-moat-screener-lavine-version`（量枢院 Q44）的实践：
版本化规则契约、严格 JSON/Parquet 契约、可校验产物、缓存与溯源、CI。

**代码未复制**：本项目有独立的规则集、独立的数据模型、独立的模块划分与独立的测试集。
两者的业务目标不同（Q44 是护城河筛选，本项目是会计红旗侦测）。
