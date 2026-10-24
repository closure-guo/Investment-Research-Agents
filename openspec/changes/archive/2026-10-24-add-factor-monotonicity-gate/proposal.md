# Proposal: add-factor-monotonicity-gate

## Why

对标文档 `docs/evals/2026-10-15-量化投研设计文档对标与差距分析.md` §2.1 指出外部最佳实践的 G2（分层回测单调性）是五道闸唯一缺失的检验维度：现有闸②只看「因子值与次日期望收益的秩相关均值」（RankIC/ICIR），**不看收益是否沿因子值分位单调分布**。一个 ICIR 达标但只有端点两三个分位贡献、中段乱序的因子，横截面解释力脆弱；分位单调性正是「IC 均值」之外的第二条独立证据线。补上它把闸门与对标基准补齐（G1–G4 全覆盖）。

## What Changes

- 闸②新增附加判据**分层单调性**（不新增闸编号，拒收仍记 `rejected_gate=2`，原因码 `WEAK_MONOTONICITY`）：在准入面板上按因子值十分位构建等权组合，判定 = |分位序与分位累计收益的 Spearman| ≥ 0.6（常量 `MIN_DECILE_MONOTONICITY`）**且** Spearman 符号与 IC 均值一致（负向因子按其方向单调同样通过；端点差 Q10−Q1 降为披露字段——实施期 sanity 锚实证：生产 10 因子 IC 均值全负、其中 7 个强负向单调，按「正相关」口径会被整体误杀）。
- 全样本口径与闸②同一窗口（准入面板全段），不引入新切分。
- 对**新候选**生效；库内存量 active 因子不回溯翻案，详情接口展示其单调性读数为**观测项**（不参与任何处置）。
- 研究面值因子（`add-analyst-revision-factors` 并行在飞）同样适用：分位在其有值票上构建，广度不足日按其既定口径跳过。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `factor-library`: 「五道闸准入管线」闸②增加分层单调性附加判据与 `WEAK_MONOTONICITY` 原因码；「因子库只读查询」详情增加单调性观测读数（存量不裁决）。

## Impact

- 后端：`src/finance_agent/factors/constraints.py`（常量）、`validate.py`（闸②分支 + detail 记 `monotonicity`）、`library.py`/`api.py`（详情读数）。
- 无数据库迁移（读数记入闸口 `detail` JSON 与事件史，随既有结构）。
- 与在飞变更的同 requirement 冲突：`add-factor-monotonicity-gate` 与 `add-analyst-revision-factors` 同改「五道闸准入管线」——后归档方须合并重写 MODIFIED 块（既有惯例）；建议本 delta 先于 revision-factors 归档（判据先立，值因子首提即按新判据评估）。
