# Proposal: add-market-snapshot

## Why

- 当前数据层只支持单票按需拉取（`data/akshare_client.py`），因子横截面打分与选股回测需要全市场（5000+ 标的）日频快照，逐票循环拉取在耗时与限频上均不可行。
- 现有链路无统一复权口径：AKShare 默认行情与前后复权数据混用会使动量/反转类因子计算结果错误，且错误是静默的。
- 无历史成分与历史状态快照：回测选股时按"当前股票池"取数引入幸存者偏差，按"当前 ST/停牌状态"过滤引入前视——二者都会让后续所有统计门禁（IC/ICIR、消融 bootstrap）失真。

## What Changes

- 新增全市场日行情快照表（SQLite，WAL 模式，复用 `data/cache.py` 基础设施）：字段含开高低收量额、**前复权与不复权双口径收盘价**、当日 ST/停牌/一字涨跌停状态、上市日期。
- 新增历史成分快照：按交易日记录当时可交易股票全集（含已退市标的），禁止"以今论古"。
- 新增 APScheduler 收盘后日批任务：批量拉取全市场日线并写入快照；任务失败时写 ERROR 日志并在前端全局横幅展示「行情快照未更新（数据日期: YYYY-MM-DD）」，禁止静默使用旧数据。
- 快照读取接口对空数据显式报错，沿用 evals/backtest 的「空 K 线显式报错防静默污染」纪律。

非目标（Out of scope）：

- 不做分钟级/Tick 级数据；不接除 AKShare 以外的新数据源。
- 不改现有单票分析链路（`fetch_data`/`compute_metrics`）的取数路径。
- 不在本 change 实现因子计算与选股逻辑（后续 delta 消费本快照）。

## Capabilities

### New Capabilities
- `market-snapshot`: 全市场日频行情/成分/状态快照，因子挖掘与选股的统一数据地基。

### Modified Capabilities
- `cache-management`: 新增快照表的存储契约（WAL、TTL 豁免——快照为不可变历史数据）。

## Impact

- **后端**：`src/finance_agent/data/snapshot.py`（新增）、`data/cache.py`（扩表）、`outcome/` 同级新增日批调度注册点。
- **前端**：全局横幅组件复用现有通知体系，新增快照过期态。
- **测试**：复权口径单测（含分红除权日拼接用例）、历史成分时点单测（含退市标的用例）、日批失败告警单测。
- **验证**：对 2024-01-01 至 2025-12-31 区间随机抽 20 个交易日在快照与 AKShare 原始接口间逐字段对账，报告落 `tests/validation/`。
