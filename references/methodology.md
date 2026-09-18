# 方法论 · Methodology

本文档定义七条红旗规则的精确语义、边界条件、缺失值处理与风险分级。任何阈值都来自
`config/rules.yaml`，本文只解释语义。

---

## 0. 证据基座

| 概念 | 定义 |
| --- | --- |
| `as_of` | 决策日（`YYYYMMDD`）。只有 `announcement_date <= as_of` 的报告可见。 |
| `visible` | 每个 `(symbol, quarter)` 在 `as_of` 前最后一个公告版本。 |
| `annual` | `visible` 中的 q4 报告，按年升序。q4 = 全年累计口径。 |
| `latest` | `annual` 最后一行。 |
| `prior` | `latest` 的前一年。**必须年份连续**，否则视为缺失。 |
| 覆盖率 | `有效规则数 / 7`，`null` 不计入有效。 |

### 三态语义

| 状态 | 含义 |
| --- | --- |
| `true` | 证据完整，且触发规则。 |
| `false` | 证据完整，且未触发规则。 |
| `null` | 证据缺失，**不允许**下结论。 |

> `null` 与 `false` 绝不等价。`null` 会拉低覆盖率，可能把结果推向 `insufficient_data`。

### 增长率口径

`growth_rate(current, previous)`：

- `previous > 0` → `current / previous - 1`
- `previous == 0` 且 `current == 0` → `0.0`（确实持平）
- `previous == 0` 且 `current != 0` → `null`（基数无意义）
- `previous < 0` → `null`（百分比符号有歧义，不猜）

---

## 1. RF01 利润与现金流背离

**问题**：账面利润没有经营现金流支撑，可能来自应收账款、存货或一次性损益。

**判定**：`CFO / 净利润 < cash_conversion_min`（默认 `0.80`）。

**边界**：

- `净利润 <= 0`：规则不适用（条件依赖正利润），返回 `false`，并在证据里写
  `note = "nonpositive_net_profit"`，同时记录 `cash_flow_negative`，供下游自行判断。
- `CFO` 或净利润缺失 → `null`（`missing_cash_flow_evidence`）。
- 严格小于：比率正好等于 `0.80` 不触发。

---

## 2. RF02 应收账款增速超收入

**问题**：收入增长靠赊销堆出来，回款能力恶化。

**判定**：`AR增速 > receivable_growth_min` **且** `AR增速 - 收入增速 > receivable_gap_min`
（默认 `0.20 / 0.20`）。

**边界**：

- 需要 `prior`，否则 `null`（`insufficient_annual_history`）。
- 任一基数非正 → `null`（`nonpositive_previous_base`）。
- 两个条件都是**严格大于**，等于阈值不触发。

---

## 3. RF03 存货增速超收入

与 RF02 同构，字段换成 `bs_inventory`，阈值 `inventory_growth_min / inventory_gap_min`。
存货堆积可能是滞销或提前备货，规则只标记异常，不判断原因。

---

## 4. RF04 应计利润过高

**问题**：净利润与经营现金流差额相对总资产过大，利润质量低。

**判定**：`(净利润 - CFO) / 平均总资产 > accrual_ratio_max`（默认 `0.10`）。

**平均总资产** = `(latest.total_assets + prior.total_assets) / 2`。

**边界**：

- 缺 `prior` → `null`（`insufficient_annual_history`）。
- 平均总资产缺失或 `<= 0` → `null`（`missing_average_total_assets`）。
- 严格大于，等于 `0.10` 不触发。

---

## 5. RF05 毛利率异常

**问题**：毛利率相对公司自身历史出现异常跳动（成本结构、收入确认或产品结构变化）。

**判定**：`|最新毛利率 - 历史均值| > max(gross_margin_abs_change_min, z_min × σ)`
（默认 `max(0.05, 2σ)`）。

**历史基线**：

- 取 `latest` 之前**连续**的最多 `gross_margin_max_history`（默认 5）年，且至少
  `gross_margin_min_history`（默认 3）年。
- 均值和标准差用**总体口径**（`ddof = 0`）。
- 阈值取"绝对变动下限"与"z 分数 × 标准差"的较大者：历史越稳定，越容易被小变动触发；
  历史越波动，越需要大变动才触发。

**边界**：

- 历史不足或含缺失 → `null`（`insufficient_gross_margin_history`）。
- 历史标准差为 0 → 阈值退化为绝对下限 `0.05`。
- 严格大于，等于阈值不触发。

---

## 6. RF06 利润与收入增速背离

**问题**：利润增速远超收入增速，可能来自非经常性损益、费用递延或会计估计变更。

**判定**：`利润增速 > profit_growth_min` **且** `利润增速 - 收入增速 > profit_revenue_gap_min`
（默认 `0.30 / 0.30`）。

---

## 7. RF07 现金转化多年恶化

**问题**：`CFO / 净利润` 连续多年下滑，利润含金量系统性下降。

**判定**：最近 `deterioration_years`（默认 3）年 `CFO / 净利润` **严格递减**，
且末年值 `< deterioration_latest_max`（默认 `0.80`）。

**边界**：

- 窗口内任一年 `净利润 <= 0` → `null`（`nonpositive_net_profit_in_window`）。
- 窗口不完整 → `null`（`insufficient_annual_history`）。
- "严格递减"：相邻两年相等即不满足。

---

## 8. 风险分级

```
if global_reasons 非空:      insufficient_data
elif red_flag_count >= 4:    high
elif red_flag_count >= 2:    medium
else:                        low
```

**`global_reasons` 来源**（任一出现即 fail closed）：

| 原因 | 触发条件 |
| --- | --- |
| `financial_industry_not_applicable` | 金融行业（单独分支） |
| `missing_industry` | 无法确定申万一级行业 |
| `conflicting_latest_revisions` | 同一 `(symbol, quarter)` 同日多个不同版本 |
| `missing_adjustment_flag` | `if_adjusted` 为 null |
| `invalid_adjustment_flag` | `if_adjusted` 不是 0/1 |
| `no_visible_annual_reports` | 无可见年报 |
| `insufficient_annual_history` | 可见年报少于 `min_annual_reports`（默认 2） |
| `non_contiguous_annual_history` | 年报年份不连续（如缺 2023） |
| `insufficient_coverage` | 覆盖率 < `coverage_min`（默认 0.60） |

注意：`non_contiguous_annual_history` 只在存在 `prior` 但年份不连续时加入；它会强制
`insufficient_data`，因为 RF02/RF03/RF04/RF06 的"上一年"证据实际上不可用。

---

## 9. 已知局限

1. **仅年报**：季报、TTM、中报未纳入 V1。
2. **仅报表数字**：不含附注、审计意见、关联交易、股权质押、商誉明细。
3. **行业中性化缺失**：RF02/RF03 未做行业基准，地产、建筑等应收账款天然高的行业可能被误伤。
4. **RF05 依赖自身历史**：上市不足 4 年的公司通常 `null`，进而降低覆盖率。
5. **不做退市/停牌处理**：回测收益只做复权价计算，未考虑涨跌停与流动性。
6. **回测样本量**：小样本分组统计不具备统计显著性，只能用于管线验证。

---

## 10. 版本

- `RULES_VERSION = 1.0.0`：七条规则 + 覆盖率 fail-closed。
- `SCHEMA_VERSION = 1.0.0`：JSON 与 Parquet 字段契约。

阈值变更 → `rule_config_hash` 变更 → 旧产物校验失败，需要显式迁移。
