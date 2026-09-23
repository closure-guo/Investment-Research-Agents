# Design: update-decision-settlement-contract

## Context

现行结算链路：`api.py` 两处挂点 → `track_record/ingest.py` 落 predictions（entry_price 取 quote 优先/最新收盘兜底，horizon_days 不传、全走表默认 252）→ 每日 16:00 `job.py` 结算（`track_record/judgment.py` horizon 语义）→ 16:30 盯市 → Score 回写。回测腿 `evals/backtest/replay.py` 却调用旧 `outcome/settle.py`（止损>目标>超期、MAX_HOLD_DAYS=20）——生产与回测两套结算语义并存，违反 `decision-backtest`「结算语义 SHALL 复用 decision-outcome、SHALL NOT 另造一套」。主规范 `decision-outcome`「事后行情追踪」早已写明 horizon 判定取代止损/目标路径，`settle.py` 属于实现未跟上规范的存量漂移。

## Goals / Non-Goals

**Goals:**

- 让「胜率」「超额收益」在口径上可定义、可在数月内积累出读数：默认窗口 252→20
- forward 与回测两腿入场价/判定语义完全同源，满足互证前提
- 89% 的 neutral 决策从评估盲区变为可计分的辅助信号（回避判定），且不污染主胜率
- 消除双结算引擎漂移

**Non-Goals:**

- 不给 TradeDecision 加 LLM 申报的 horizon 字段（D1）
- 不改 Trader 决策姿态、不动 prompt（#134 解耦）
- 不做回避指标的前端展示与 overview API 暴露（后续增量；本 delta 只保证落库与统计函数可用）
- 不修 #57 遗留项（position_size 列等）

## Decisions

**D1 固定窗口，不让 LLM 申报 horizon。**
备选方案是给 TradeDecision 加 horizon 字段（spec 已有「观点自带 horizon_days 以其为准」钩子）。否决理由：① confidence 扎堆 0.4–0.6 的先例（metrics.md 待决策 #4）表明 LLM 自报数值信息量低、易扎堆，逐决策可变窗口会让聚合口径碎裂；② 申报 horizon 需改 trader/risk_judge prompt（v28→v29）+ deploy + 校验回路，成本与噪声都高；③ 评估协议要的是固定可比窗口。保留「自带以其为准」语义不动（向前兼容），只改默认值并让 ingest 显式写入。

**D2 入场价 = 决策日收盘（收盘后决策 → 次一交易日收盘）。**
备选：申报 entry_price（限价语义，可能永不成交，且 watch 无申报导致口径不齐）或实时 quote（不可复现，与 created_at 强耦合）。决策日收盘是可复现、可审计、两腿天然一致的标准化口径（replay.py 本就用决策日收盘）。申报价不丢——冻结进 rationale_snapshot，未来可做「申报价 vs 实际价」偏差观察（本 delta 不实现该指标）。

**D3 回避判定写独立字段 avoidance_status，不复用 resolved_*。**
若复用 resolved_* 状态列，win_rate、API 状态过滤、前端状态标签（命中=绿等）全部被语义污染，且违反「neutral 不进胜率分子分母」的既有统计契约。独立列 + 独立统计函数，主链路零感知。回避带沿用全局 ±2% 中性带配置，不新增配置项。

**D4 存量处置：不追溯、不重算。**
存量已结算行（252 口径）保持原判定；存量 open 观点保持原 horizon_days（append-only + 冻结哲学，horizon 是落库时确定的判定参数）。切点日起新观点走 20。战绩分段按 `track-record-versioning`「战绩分段不混算」机制登记口径版本，总览统计跨切点分段展示或标注。当前存量 settled <10、open 极少（predictions 干净行 ~45），追溯重算的收益趋零、风险（改写已冻结判定）为实。

**D5 结算引擎同源方式：抽取共享判定函数，回测离线调用。**
`judgment.py` 的 horizon/超额/带判定逻辑抽为可离线调用的纯函数（输入：观点行 + 日 K 序列；输出：判定结果），生产 job 与回测 replay 共同调用。`settle.py` 标 deprecated：只读 decision_log API（`GET /api/decisions*`）与存量数据保留（spec「决策查询 API」需求不动），不再有写入方。备选「回测继续用 settle.py 但对齐参数」被否——两套引擎的存在本身就是漂移的根源，对齐参数只是一次性止血。

**D6 Score 上报收窄到 long/short。**
decision_hit/return/excess 的方向符号化语义（sell 取负）对回避判定不成立（回避的「对」= 标的跑输）。与其发明 avoidance_hit Score，不如不上报——回避统计走 overview/统计函数通道，Langfuse Score 保持「方向性决策的对错」单一语义。

## Risks / Trade-offs

- [T+20 窗口下胜率对 regime 敏感] → 超额口径对冲 beta；`metrics.md` 收口报告强制 regime 分段披露（Δ1 收口纪律）
- [存量 open 观点 252/20 混存造成统计口径混杂] → 统计函数按 horizon 实际值结算，不混算；总览标注切点；存量 open 数量极小（个位数）
- [avoidance_status 加列触碰「写入后冻结」契约的边界] → 明确其为系统计算的判定结果字段（与 resolved_* 同类，允许状态流转），冻结清单（rationale_snapshot/direction/entry_price/created_at）不变
- [收盘后决策的「次一交易日收盘」入场在长假前产生跨假期持仓] → 结算按交易日计数（horizon_days 本就是交易日语义），自然处理；口径在预登记披露
- [settle.py deprecated 影响未知调用方] → 全仓 grep 确认调用面（replay.py + job 旧路径 + 只读 API），deprecation 以显式 warning 日志 + spec 不动的只读 API 保留兜底

## Migration Plan

1. 迁移脚本：predictions 加 avoidance_status 列（幂等，可空）
2. 配置项 OUTCOME_DEFAULT_HORIZON_DAYS=20 上线（env 可覆盖）
3. ingest/judgment/metrics/job 改造 + replay 切同源，一次 delta 内完成
4. 切点登记：`track-record-versioning` 口径版本行 + `metrics.md` §2 时间线行（引用 Δ1 的 §1.8 口径）
5. 回滚：配置项改回 252 即恢复旧默认（已落库行不受影响）；avoidance_status 列可空、无破坏

## Open Questions

- 无（窗口值 T+20 的 owner 终裁挂在 Δ1，本 delta 以配置项承接裁决结果）
