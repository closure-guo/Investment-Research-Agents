# Proposal: update-view-min-survival

## Why

一条方向性观点活不过 2 天，T+20 主指标因此永远产不出读数。

生产实测（2026-11-04）：

| 事实 | 数值 |
|---|---|
| 同标的相邻分析间隔 | 中位 **1 个交易日** |
| 方向翻转率（相邻两次分析 action 不同） | **31.6%** |
| 方向性观点 → 下一次同标的分析的间隔 | 中位 **2 天** |
| 方向性观点活过 20 天 | **0.0%**（活过 5 天仅 3.4%） |
| 已闭仓方向性观点 | 56 条，**全部 `superseded`**，无一条 `expiry` |
| `excess_return` 非空 | 337 条中 15 条；5 条已结算全 null |

根因之一在 `should_supersede`：它把「**目标价不同**」也当提前结算条件。于是同标的每日重分析时，只要目标价微调（几乎必然发生），一条连续看空观点就被结算重开一次——观点被切成 1~2 天的碎片，永远走不到 T+20。

## What Changes

- `should_supersede` 只按**方向翻转**判定：同标的方向相同（含仅调整目标价）→ 不结算旧观点。
- 目标价仍随各自观点独立落库、独立判定，不丢失。
- neutral 的 supersede 语义不变（本来就不走 superseded，走完整 horizon 回避判定）。

## Capabilities

- **Modified Capabilities**: `track-record`（观点提前结算条件）

## Impact

- 代码：`src/finance_agent/outcome/track_record/judgment.py::should_supersede`；调用点唯一（`job.py` 日批）。
- 数据：不回填、不重算。**只影响今后**新观点是否触发旧观点结算；历史行状态不变。
- 预期效果：同标的同向重复不再杀死旧观点，方向性观点存活期延长；活到 T+20 的比例从 0% 开始上升（需观察 2~3 周）。
- 副作用：同标的可能同时挂多条同向观点 → 由 `update-track-record-symbol-aggregation`（按标的聚合）在净值侧吸收，不放大单票权重。
- 不解决的部分：方向翻转仍会结算（合理），所以即使 A1 上线，仍有大量观点以 `superseded` 收尾——**T+20 的「满窗读数」需要更长的观察期，短期读数仍依赖 superseded 结算**。
