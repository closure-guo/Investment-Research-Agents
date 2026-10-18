# Design: add-crowding-regime-positioning

## 数据流

```
market_snapshot_daily（既有）→ regime 日批（新 job，快照后）
  ├─ 取数：spot 抓取（与快照任务同源，含流通市值字段；market_snapshot 表不加列）
  ├─ 计算：四指标 + 250 日窗口分位
  └─ 落库：data/regime.db（REGIME_DB_PATH 可覆盖）regime_daily 表，trade_date 唯一
regime 判定（纯函数：最新行 + config/regime.json）→ 端点 GET /api/regime / 前端区块
execution/driver.py（组合聚合前）→ 读取最新行 + 判定 → 传入 aggregate_targets 系数
```

## 指标口径（与 metrics.md §1 登记一致）

| 指标 | 口径 |
|---|---|
| 小市值组成交额占比 | 按当日流通市值升序取前 30%（`SMALL_CAP_QUANTILE`）标的成交额之和 ÷ 全市场成交额；停牌/零成交剔除；市值取数链 = 快照同源 spot（EM 原始列）优先 → **腾讯行情批量兜底**（`qt.gtimg.cn` 批量报价含流通市值字段；约 5.5k 标的按批切分、限速拉取）；采用兜底源时指标行 `cap_source=tencent` 留痕；两源皆不可用 → 不产行 + `cap_data_missing` |
| 涨跌家数 | 按 `pct_chg` 符号统计涨/跌/平 |
| 涨停/跌停家数 | 按 `limit_up_sealed` / `limit_down_sealed` 计数 |
| 全市场成交额 | Σ`amount` |
| 历史分位 | 当日值在最近 250 交易日（含当日）窗口内的百分位排名；样本 < 60 → null + `insufficient_history` |

## regime 判定 v1（确定性、可解释）

- 触发指标：小市值占比分位、全市场成交额分位（Breadth 类进 reasons/展示，不参与 v1 触发——已知简化，后续可扩）。
- 规则：任一触发指标分位 ≥ `danger` 阈值（默认 0.9）→ danger；否则 ≥ `elevated` 阈值（默认 0.8）→ elevated；否则 normal。
- 系数映射：normal 1.0 / elevated 0.5 / danger 0.0；全部可配置。
- 陈旧口径：`as_of` 距判定时刻 > 7 自然日（可配置）→ stale。

## `config/regime.json`（缺文件回落内置默认 + WARN；损坏 fail loud，与 `execution/config.py` 同范式）

```json
{
  "thresholds": {"elevated": 0.8, "danger": 0.9},
  "multipliers": {"normal": 1.0, "elevated": 0.5, "danger": 0.0},
  "small_cap_quantile": 0.3,
  "history_window": 250,
  "min_history": 60,
  "stale_days": 7
}
```

## 执行链接线

- `execution/portfolio.py::aggregate_targets` 新增可选参数 `regime_multiplier`（默认 1.0）与 `regime_state`：在总仓缩减之后、输出之前对全部权重乘系数；系数 ≠ 1.0 时逐票追加调整码 `REGIME_SCALE`；默认调用行为逐字节不变（既有测试必须全绿）。
- `execution/driver.py`：聚合前查询 regime 最新行并判定；缺失/陈旧 → `regime_multiplier=1.0` + 执行摘要 `no_regime_data`；正常 → 传系数与状态。查询失败（库损坏）→ 同缺失处理并留痕（MUST NOT 使日批失败——但须在摘要与前端可观测）。

## 存储与运维

- 新库 `data/regime.db`，表 `regime_daily(trade_date PK, small_cap_share, up_count, down_count, flat_count, limit_up_count, limit_down_count, total_amount, small_cap_share_pctile, total_amount_pctile, computed_at)`。
- 新日批 job 注册进 `outcome/scheduler.py`（排在 `market_snapshot_daily` 之后）并进 ops 任务清单；`.gitignore` 补 `data/regime.db*`。
- E2E：全配置与本地默认注入 `REGIME_DB_PATH` 隔离路径（与既有 `*_DB_PATH` 隔离惯例一致）。

## 备选与取舍

- **备选（弃）**：把系数逻辑放进 `risk_gate.py`。门禁语义是「拒绝非法订单」，系数是「缩减目标」——放聚合层与既有 `TOTAL_CAP_SCALE_DOWN` 同层，语义一致。
- **备选（弃）**：小市值分组用成交额代理。会改语义（流动性≠市值），与调研口径不符；改为同源取市值字段。
- **v1 不做**：手动覆盖开关、多指标加权模型、反转信号——先要「确定性 + 可解释 + 可回滚」。

## 已知边界

- 分位需要历史积累：上线后前 60 个交易日分位为 null（诚实空值），期间判定的触发指标不可用 → 保持 normal（在 `reasons` 注明 `insufficient_history`）。
- 系数只缩放目标，不改变单票上限语义；系数 0 的清仓目标是**目标**（订单仍逐单过门禁与撮合）。
- 陈旧判定锚定「判定时刻」（真实 now），非 `as_of`：手工对旧日 `as_of` 补跑时可能用上比补跑日更新的 regime 行（look-ahead 边角；回填需另立口径，v1 接受并在此登记）。
- 市值兜底源（腾讯批量）为真实市值字段的源切换，不属替代口径；每次取数在指标行留痕 `cap_source`，便于数据源审计。
