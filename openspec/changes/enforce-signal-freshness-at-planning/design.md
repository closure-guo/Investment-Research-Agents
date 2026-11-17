# Design: enforce-signal-freshness-at-planning

## Approach

规划前过滤层（`run_execution_daily` 内、contexts 载入后、flat/long 分流前）：

```
pending_signals（既有：无订单信号）
  │ ① 来源状态重验：decision_id ∈ open_prediction_ids？
  │    否 → skipped_details += {symbol, reason: STALE_SOURCE, decision_id}
  ▼
valid_signals
  │ ② newest-wins：per symbol 保留 created_at 最大者
  │    旧者 → skipped_details += {symbol, reason: SUPERSEDED_BY_NEWER,
  │                                stale_signal_id, winner_signal_id}
  ▼
plannable_signals → 既有 flat/long 分流 → 两个 planner（零改动）
```

- `open_prediction_ids`：复用 `list_predictions(status="open", limit=_PREDICTION_SCAN_LIMIT, db_path=db_path)` 的既有关键字（与信号创建段同一扫描口径，无新查询函数）
- 新原因码沿用 `skipped_details` 通道（#595 已在生产），无摘要结构变更

## Alternatives Considered

- **MODIFIED 既有需求**：原文 400+ 字逐字命中才可 archive（漂移陷阱），ADDED 新需求零风险——选择 ADDED
- **flat 信号时效窗（N 日过期）**：来源重验 + newest-wins 已覆盖全部实证危害（resolved 来源地雷、旧信号击穿新仓）；来源 open 且为最新决策的 flat 等待是**正确**行为（决策在站就应执行）。过期窗是多余机制——拒绝（YAGNI），本设计披露
- **信号生成期校验**（① 阶段过滤）：创建时来源必为 open（既有护栏），地雷产生于「创建后来源状态翻转」——只能在规划期重验

## Risks

- **风险 1：open 集合扫描上限 1000 截断导致误杀**。现状 open≈50；若逼近 1000，信号创建段同样失真（既有边界），非本 delta 引入；`_notes`/warning 可观测
- **风险 2：newest-wins 对「同日多决策」语义**。signal_id 每标的一日一条（`{symbol}:{date}`），同日重跑幂等覆盖——不存在同日多信号；跨日比较即覆盖全部冲突形态
- **风险 3：决策层无新决策时的 flat 等待被误读为缺陷**。来源 open + 最新决策的 flat 执行是既有需求（flat 平仓场景）语义，本 delta 不改变
