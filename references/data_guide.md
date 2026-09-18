# 数据指南 · Data Guide

本项目**唯一**数据源是 PandaData（`panda_data` SDK）。本文件记录字段口径、fallback 顺序、
缓存与溯源机制，以及实盘验证过的行为。

---

## 1. 鉴权

```python
import panda_data
panda_data.init_token(username=..., password=..., base_url="http://pandadata.pandaaiquant.com")
```

凭证来源（按优先级）：

1. 构造参数 `PandaDataProvider(username=..., password=..., base_url=...)`；
2. 环境变量 `PANDA_DATA_USERNAME` / `PANDA_DATA_PASSWORD` / `PANDA_DATA_BASE_URL`。

读取环境变量后立即 `pop`，从进程环境中移除。凭证**不会**写入缓存、清单、日志或产物。
缓存命名空间只包含账号的 SHA-256 前 16 位。

---

## 2. 使用的接口

| 接口 | 用途 | 关键参数 |
| --- | --- | --- |
| `get_fina_reports` | 财报 | `symbol`（≤20/批）、`start_quarter`、`end_quarter`、`date=as_of`、`is_latest=False`、`fields` |
| `get_stock_detail` | 沪深 A 股全集 | `status=None` |
| `get_industry_constituents` | 申万一级成分 | `stock_symbol`（≤100/批）、`level="L1"` |
| `get_industry_detail` | 申万一级名称 | `level="L1"` |
| `get_stock_daily` | 最新收盘价 | `symbol`（≤50/批）、`start_date`、`end_date` |
| `get_stock_daily_post` | 后复权价（回测收益） | `symbol`（≤50/批）、`start_date`、`end_date` |

**约束**：

- `get_fina_reports` 单次区间 ≤ 5 年（20 个季度）。provider 自动按 5 年窗口切片。
- 所有调用都需要显式 `date`/`as_of`，不存在"取最新"的隐式行为。
- `is_latest=False` 用于取**历史版本**，配合 `announcement_date` 做时点筛选。

---

## 3. 字段映射

`accounting_red_flags/config.py::REPORT_FIELDS` 是字段契约的一部分，参与缓存命名空间与
`source_snapshot` 计算。

| 内部字段 | PandaData 字段 | 说明 |
| --- | --- | --- |
| `symbol` | `symbol` | `600519.SH` / `000001.SZ` |
| `quarter` | `quarter` | `2024q4` |
| `announce_date` | `date` | **公告日**，时点筛选依据 |
| `if_adjusted` | `if_adjusted` | 0/1；缺失会 fail closed |
| `revenue` | `is_revenue` → `is_total_revenue` | 营业收入 |
| `operating_cost` | `is_oper_cost` → `is_total_cogs` | 营业成本 |
| `net_profit` | `is_n_income_attr_p` | 归母净利润 |
| `operating_cash_flow` | `cfs_net_cash_operating` | 经营活动现金流净额 |
| `accounts_receivable` | `bs_net_accts_receive` → `bs_notes_accts_receiv` | 应收账款（含应收票据 fallback） |
| `inventory` | `bs_inventory` | 存货 |
| `total_assets` | `bs_total_assets` | 总资产 |

### fallback 规则

`_value(row, primary, *fallbacks)` 返回**第一个有限值**。若全部缺失或非有限 → `None`。

**不 fallback 到 0**。字段缺失即证据缺失。

### 实盘已验证的口径

- `get_fina_reports` 返回宽表，实测约 322 列。
- q4 行为**全年累计**口径（例如贵州茅台 2023q4 毛利率 ≈ 0.92，与公开年报一致）。
- 银行股 `is_oper_cost` 为 `NaN` —— 这正是金融业需要排除、而不是把缺失当 0 的原因。

---

## 4. 缓存与可复现性

`configure_runtime(cache_dir=...)` 启用磁盘缓存：

- 每个响应用 `parquet` + `json` 清单成对原子写入（先写临时文件再 `replace`）。
- 缓存键 = `sha256(api + kwargs + 上下文)`，上下文包含：
  - `sdk_version`（`panda_data` 版本）
  - `base_url_hash`、`account_hash`
  - `contract_hash`（`REPORT_FIELDS` 的哈希）
- 读取时校验 `frame_sha256`；不匹配则重新请求。
- 缓存失败**不会**让一次成功的调用变成失败。

### 溯源

`source_provenance()` 返回：

```json
{ "response_count": 12, "response_manifest_hash": "<sha256>" }
```

`response_manifest_hash` 是本次运行实际使用的所有响应的 `(缓存ID, 内容哈希)` 排序后拼接的
SHA-256。它进入 `dataset_version`，因此**换数据就会换版本号**。

### 限速与重试

- `min_request_interval`：全局最小请求间隔（串行化令牌）。
- `max_workers`：财报请求并发度；并发只作用于网络等待，不改变结果。
- 限流错误（`500010` / `请求次数超限`）自动退避重试，最多 8 次。

---

## 5. 时点筛选

```
visible = 对每个 (symbol, quarter)，取 date <= as_of 的最后一个版本
conflicts = 同 (symbol, quarter) 且同一最新日期但值不同的情况
```

- 若 `conflicts` 命中，该股票的对应季度被标记，整只股票 fail closed 为 `insufficient_data`。
- `annual_rows` 只保留 q4，按年升序，最多 `history_years` 年。

---

## 6. 行业归属

- 申万一级（`level="L1"`），按 `in_date <= as_of < out_date` 取生效记录。
- 同一股票在 `as_of` 存在多个生效记录 → 归属歧义 → 丢弃 → `missing_industry` → fail closed。
- 金融业代码：`801780`（银行）、`801790`（非银金融）；另有名称模式
  `银行|保险|证券|非银金融|信托|多元金融` 兜底。

---

## 7. 离线演示

`--provider fixture` 提供 4 只合成公司（干净 / 红旗 / 银行 / 数据稀疏），用于：

- CLI 与 JSON/Parquet 契约演示；
- 测试与文档示例。

它的 `name` 含 "synthetic"、`requires_live_validation=True`，且 `scripts/build.py`
**拒绝**用它写入 `production/database.parquet`。

---

## 8. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `AuthenticationError` | 未设置环境变量或凭证失效 | 检查 `PANDA_DATA_USERNAME/PASSWORD` |
| 大量 `insufficient_data` | 覆盖率 < 60%，常见于上市时间短或行业缺失 | 属预期 fail-closed 行为 |
| `missing_adjustment_flag` | `if_adjusted` 为 null | 不做假设，保持 fail closed |
| 银行股 `is_oper_cost` 为空 | 金融报表结构不同 | 返回 `not_applicable` |
| 缓存目录很大 | 全市场 × 多窗口 | 定期清理，或按 `as_of` 分目录 |
