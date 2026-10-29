# Proposal: add-rejected-factor-revival

## Why

拒绝池 191 个被拒结构每天记录 IC（add-rejected-factor-pool 日批跟踪），但「记录 → 无消费者」是断头路：其中约 27% 落在闸②贴线区（|ICIR| ∈ [0.25, 0.30)，估计标准误 ≈0.063——当年拒收可能是抽签失败而非无信号），且随时序推进部分因子的滚动 IC 表现已越过闸②线。没有自动复评通道，这些「复苏」结构只能等 LLM 生成器碰巧重提（记录在案的惯性行为，不可靠）。

## What Changes

- 新增拒绝池复苏日批 `factor_revival_daily`（mon-fri 17:10，**零 LLM**）：筛选满足复苏静默线的 rejected 行（库内 IC ≥ 60 点、近 60 点滚动 |ICIR| ≥ `MIN_ABS_ICIR`、冷却期外），经外部导入同一路径（预检 → 闸⓿~⑤，`ledger_source="revival"`）重新提交准入，单次运行上限 `REVIVAL_DAILY_CAP`（按 |滚动 ICIR| 降序取前 N）。
- **rejected 保持终态，状态机零改动**：过闸 → 新 candidate 行入库；再拒 → 既有 `re_rejected` upsert 落事件（冷却自动重置，天然防刷）。
- 常量入 `factors/constraints.py`：`REVIVAL_COOLDOWN_DAYS` / `REVIVAL_DAILY_CAP`（静默线复用闸②的 `MIN_ABS_ICIR`，窗口复用 `IC_WINDOW`——单一真相源不另立阈值）。
- 预筛不是裁决：静默线只决定「是否值得花一次准入」，五道闸仍是唯一准入权威（含闸②复核、闸④撞池、闸⑤样本外）。

## 非目标

- 不改状态机（不新增 `rejected → *` 迁移边；rejected 行除既有 `re_rejected` 事件外不落新事件类型）。
- 不改五道闸判据/阈值（闸② 0.3 不动，与 optimize-factor-supply-funnel 非目标一致）。
- 不做面板加深（500→750 是 2027 年自然解锁项，届时本通道自动受益——贴线区噪声收窄）。
- 不给 rejected 行加任何新列/新表（复用 `factor_ic` / `factor_events`）。

## Capabilities

- **New Capabilities**: 无。
- **Modified Capabilities**: `factor-library`（ADDED：拒绝池复苏重评通道）。

## Impact

- 代码：`factors/constraints.py`（两常量）、`factors/revival.py`（新模块）、`outcome/ops/jobs.py`（JOB_IDS/JOBS/LABELS/SCHEDULES + 钉数测试 29→30）、`outcome/scheduler.py`（包装 + 注册）。
- 调度：17:10 插在日批窗口尾部（外资快讯 17:05 之后、诊断 17:15 之前）；面板仅在存在可复苏候选时构建（复用 external 路径的 `_panel_from_snapshot`）。
- 零 LLM 调用；纯后端数据流，免 E2E 门禁。
