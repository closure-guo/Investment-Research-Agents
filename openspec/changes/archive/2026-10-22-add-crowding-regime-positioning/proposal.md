# 市场拥挤度与总仓位系数（add-crowding-regime-positioning）

## Why

2026-10-18 调研结论：小资金最大的结构性优势是「可空仓」——机构受赎回、排名与最低仓位约束不能空仓，而当前执行链只有静态上限（单票 20% / 总仓 80%，`execution/portfolio.py`），没有任何按市场状态调节总仓位的机制。「拥挤—踩踏」周期已在 2024-01（万得微盘最大回撤 49.63%）与 2026-05~07（再跌 21.34%）两次现实化；且后续「小票通道」变更以本能力作为前置护栏（顺序见调研备忘录 §4.2，顺序不可反）。

## What Changes

- **新 capability「crowding-regime」**：
  - **指标日批**：每交易日快照完成后计算并落库四指标（小市值组成交额占比 / 涨跌家数 / 涨停跌停家数 / 全市场成交额）及各自历史分位（250 交易日窗口）；同日重跑幂等。
  - **regime 判定**：确定性纯函数（配置驱动、无 LLM）：`state ∈ normal|elevated|danger` + 总仓位系数（默认 1.0 / 0.5 / 0.0）；配置缺文件回落 + WARN、损坏 fail loud（与撮合配置同范式）。
  - **执行链消费（MODIFIED trade-execution「组合仓位聚合」）**：目标权重乘系数；系数 0 → 全 flat 目标；留痕 `REGIME_SCALE`；数据缺失/陈旧 → 系数按 1.0 并留痕 `no_regime_data`（披露式降级）。
  - **只读端点**：`GET /api/regime`（状态 / 系数 / 指标 / 历史 / 陈旧标记）。
- **frontend ADDED「选股页市场温度计区块」**：状态徽章、系数、四指标（当日值 + 分位）、近一年曲线、理由一行；只读（无手动覆盖）；缺数据/陈旧如实披露。
- **口径登记**：`docs/evals/metrics.md` §1 新增小节。

## Impact

- 代码：`outcome/regime/`（指标计算 + 判定 + 存储，新增）、`outcome/scheduler.py`（新日批 job）、`execution/portfolio.py`（系数参数 + 调整码）、`execution/driver.py`（读取最新 regime 并传参）、`api.py`、`frontend`（选股页区块）、`config/regime.json`（新增）。
- 数据：新库 `data/regime.db`（`REGIME_DB_PATH` 可覆盖）；E2E 全配置注入隔离路径。
- 行为：paper 执行链目标总仓位可被系数缩放（含 0 清仓目标）；深度分析/决策层不受影响。
- 非目标：不做自动择时交易信号；不做手动覆盖开关（v1 只读展示）；不改单票上限语义；不回填历史分位以外的历史区间（起步后逐日累积）。
