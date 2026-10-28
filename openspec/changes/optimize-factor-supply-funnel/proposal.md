# Proposal: optimize-factor-supply-funnel

## Why

因子供给漏斗两段损耗的实测归因（2026-10-28，`data/factor_research/harvest_state.json` / `candidates.json` / `factor_library.db`）：

- **转译段**：2,213 提案 → 2,115 已处理，`untranslatable` 1,534（72.5%）。构成（互斥分桶）：中间层复杂度超限 618（40.3%，AST 上限未前移给生成器）、语法 19、**横截面算子缺口 769（50.1%）**、epsilon 未给定 64、条件/NaN 与高阶矩等 64。
- **闸门段**：563 进闸 → 12 准入（条件准入率 2.1%）。拒收 551 次中闸② 410（74%；被拒 |ICIR| 中位数 0.204，**27% 落在 0.25~0.30 贴线区**——估计标准误 ≈0.063，成败噪声主导）、闸④ 85（15%；现役池 10 只全为量/流动性/高低价差族，动量反转与波动率 0 只——红海撞墙）、闸⓿ 23、闸⑤ 19、闸① 14、闸③ 0。

两个损耗各有独立旋钮且互不替代：压转译率只放大进入量（数量效应），不改变候选成色；提通过率要改的是候选构成（避开红海）与估计噪声，不是门槛。

## What Changes

本变更是**程序级跟踪 delta（umbrella）**：把本轮归因确定的杠杆收进一份任务清单逐项落地。带行为契约的项在实施时于本目录补 `specs/` delta 或另立 change-id——本 delta 自身不直接修改行为代码。

1. **部署判效跟踪**：#509（add-feedback-quota-fairness）/ #510（feed-dsl-contract-into-proposal-prompt）已全绿未部署；部署门与 10-31 判效读数归档挂在其各自 tasks.md，本 delta 只做同屏跟踪。
2. **中间表示改造**（最大单项杠杆）：LLM 直出本系统 DSL 或为 Qlib 映射加横截面桥——解锁 769 条横截面型拒译；同时改善闸④（横截面因子与全量价池 IC 相关性天然低，通过率高于 2.1% 基线）。
3. **风格缺口定向生成**：active 池风格画像（8 量价 + 2 高低价差、动量 0、波动率 0）与 per-family 拒收计数回喂生成器，让提案避开红海——**本 delta 唯一承载的实现项**（specs/factor-proposal），机制形态沿用 #482/#510 已确立的载荷契约模式。
4. **批次去同族化**：闸③ BH-FDR 按批计算，同族变体同批互踩；收割投批按族分散或家族代表制（当前 FDR 零死亡只是因为多数死在闸②之前，属前瞻布局）。
5. **拒绝池复评通道**：贴线区（|ICIR| ∈ [0.25, 0.30)）随面板加深自动重评，把"抽签失败"的回收（与 add-rejected-factor-pool 后续 delta 合并考虑，可拆出独立 change）。
6. **政策拍板**：epsilon 默认值（64 条）；多 horizon（T+5/T+20）是否评估（统计负担 ×3、FDR 收紧、与产品 T+20 持有语义对齐）。
7. **等待项跟踪**：准入面板 500→750 日（快照库现有 519 个交易日，2027 年初自然解锁，估计 SE 0.063→约 0.052）；年轻因子 IC 满窗观察（因子 9 等，2026-11 中下旬满 60 点）。

## 非目标（明确不做）

- 降低闸② |ICIR| 0.3 门槛（0.3 与单窗噪声 ≈0.09 校准过；放 53% 贴线噪声进池，衰减监控会成批打回）。
- 放宽转译纪律（猜 epsilon、近似窗口语义）——转译正确性是闸门判据的地基。
- 用验证段调参 / 挑 horizon（直接泄漏，闸⑤形同虚设）。

## Capabilities

- **New Capabilities**: 无。
- **Modified Capabilities**: `factor-proposal`（ADDED 风格缺口定向回喂——本 delta 唯一实现项）。B1 中间表示改造 / B3 批次去同族化 / B4 复评通道各含独立行为契约，实施时**另立 change-id** 承载（B4 复评涉及 `rejected` 终态迁移，须先过状态机设计）。

## Impact

- 代码（随任务确定）：`factors/harvest.py`、`factors/qlib_translate.py`、`factors/feedback.py`、`prompts/factor_transcription.md`、`prompts/factor_proposal.md`。
- 指标台账：转译率 / 条件准入率 / 入库率三方同屏进 `docs/evals/metrics.md`，以 active 池风格构成与准入后 ICIR 持续性收口（防 Goodhart——任何单一指标改善而池子质量不动，只算拧了一个旋钮）。
- 数据：无 schema 变更；快照库自然增长。
