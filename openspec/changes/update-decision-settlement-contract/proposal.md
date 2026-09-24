# Proposal: update-decision-settlement-contract

## Why

outcome 收益评估的口径（`add-outcome-profitability-protocol`）要求 T+20 结算窗口、标准化入场价与 watch/hold 回避计分，但现行契约与之有三处脱节：① `predictions.horizon_days` 默认 **252 交易日**且 ingest 不显式写入——照此口径第一批生产读数要等一年；② 判定收益基于参考价（实时 quote 优先/最新收盘兜底）计算——不可复现、盘中决策含决策前已发生走势，且与回测腿（决策归属日收盘）不可比，双腿无法互证；③ neutral（watch/hold）观点无回避判定语义且被按 short 符号误判并计入胜率（实现缺口）——生产流量 89% 的决策是评估盲区。另有两处实现漂移：回测腿仍走旧 `settle.py`（止损/目标/超期引擎），与 `decision-outcome` 主规范「horizon 判定取代止损/目标路径」及「结算语义 SHALL NOT 另造一套」不符；track-record 判定链路无 Score 上报（既有 spec 要求在现行生产链路的空缺）。

## What Changes

- **默认判定窗口 252 → 20 交易日**：新配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`（默认 20），ingest 显式写入 horizon_days；「观点自带 horizon_days 以其为准、上限 1 年」语义保留。**BREAKING（对内口径）**：战绩页/总览 API 的胜率语义随之变为 T+20 短窗口，切点按 `track-record-versioning`「战绩分段不混算」登记，存量已结算行不重算、存量 open 观点保持原 horizon 不追溯
- **结算入场价与参考价分离**：新增 `settle_entry_price` 判定产出列——判定任务按标准化口径从行情派生（**决策归属日收盘**；收盘后决策取次一交易日收盘；非交易日归属其后首个交易日），`raw_return`/`excess_return` 以它计算并冻结落库；`entry_price` 保留参考价语义（展示与盯市，冻结）。TradeDecision 申报的 entry/stop/target **冻结进 rationale_snapshot**（审计），不参与任何结算计算——与回测腿（replay 决策归属日收盘）天然同口径
- **neutral 回避判定**：watch/hold 观点 horizon 到点按 long 口径超额与 ±2% 带判 avoidance_win / avoidance_loss / avoidance_neutral，写独立字段 `avoidance_status`，**不写 resolved_*、不进主胜率**；回避正确率 = avoidance_win/(avoidance_win+avoidance_loss) 作独立辅助统计返回（展示门槛与胜率一致）
- **Score 上报收窄 + 补齐**：track-record 判定链路对 long/short 观点上报 decision_hit/decision_return/decision_excess（既有 spec 要求在现行生产链路的补齐）；neutral 观点 SHALL NOT 上报（避免方向符号化语义混入回避语义）；旧链路对 hold/watch 同步排除
- **结算引擎同源**：回测 replay 的结算切换到与生产 track-record 判定规则同源的 offline 实现（horizon/超额/±2% 带）；旧 `settle.py` 止损/目标/超期引擎标 deprecated（只读 decision_log API 与存量数据保留，不再承载评估流量）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `decision-outcome`: 「决策落库」（参考价/结算入场价分离 + horizon 显式写入 + 申报价冻结）、「事后行情追踪」（默认 horizon T+252→T+20 + 派生入场基准确认）、「决策效果 Score 反向上报」（仅 long/short 上报；neutral 排除）
- `track-record`: 「观点数据模型」（horizon 默认 20、avoidance_status + settle_entry_price 列、申报价入快照）、「判定规则」（默认窗口 T+20 + 派生入场基准 + 中性回避判定 + long/short Score 上报）、「基础统计」（胜率仅计 long/short + 回避正确率独立辅助统计 + 切点分段）
- `track-record-calibration`: 「置信度校准分桶」（neutral 回避终态按 avoidance_status 映射命中值，替代按 0.5 一律命中）
- `track-record-segments`: 「四维切片指标」（切片胜率/平均超额人口限 long/short 三态，`avoidance` 终态行不进二者、只计样本数）

## Impact

- 代码：`src/finance_agent/outcome/track_record/`（model 迁移加列 / ingest / judgment / metrics 统计 / job 分支 / Score 上报）、`src/finance_agent/api.py`（overview 辅助字段，additive）、`evals/backtest/replay.py`（结算调用切同源实现）、`src/finance_agent/outcome/settle.py`（deprecated 标注）、`src/finance_agent/outcome/job.py`（旧链路 hold/watch 排除上报）
- 数据库：predictions 加 `avoidance_status` 与 `settle_entry_price` 列（可空，迁移幂等）；存量行不回填不重算
- 前端：战绩页**仅标签/类型映射小改**（新增回避终态 status 的「回避」标签与 `PredictionStatus` 联合类型成员——避免已结算 neutral 行渲染空白；回避指标数值展示仍留待后续增量）。**本 delta 属交互类变更**（触及前端 UI）→ `docs/project-workflow.md` §3.5 E2E 门禁与 §3.6 人工验证**适用**（统一收口轮 2026-09-24 补执行：`decisions.spec.ts` 3 passed 回归 + 真实浏览器实测 avoidance 行「回避」标签渲染，证据见 `tests/validation/2026-09-23-update-decision-settlement-contract-validation.md`「E2E 门禁与人工验证（交互类）」）。跨口径（T+252 存量 vs T+20）胜率语义不失真仍需 owner 以真实数据核对。
- prompt / LLM 契约：**零变更**（固定窗口方案不给 TradeDecision 加 horizon 字段，理由见 design D1）
- 协调：#57（outcome 硬化遗留）第 2 项 position_size 恒 NULL——本 delta 富化 rationale_snapshot 时**不顺手修**（独立摘取）；#134（Trader watch 姿态）不受影响——本 delta 让 watch 可计分，不改 watch 产出分布
- 依赖：实现顺序在 `add-outcome-profitability-protocol`（口径先行）之后；`add-forward-paper-trading-cohort` 与 `add-backtest-leakage-controls` 依赖本 delta 的窗口与入场价口径
