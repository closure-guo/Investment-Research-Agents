# Proposal: enforce-decision-contract-at-execution

## Why

决策层（trader/risk_judge/FM）产出的执行约束字段在执行引擎被全程丢弃，导致 2026-11-12/13 三笔 buy 全部违约执行（issue #593 实证）：300927 条件入场 trigger_high=22.35 从未触发却被 21.68 建仓（浮亏 -4.1%）；三单目标权重均按等权×单票上限 20% 打满，与计划申报的档位无关（600780 明文「light 档下沿约 10%」被执行 2 倍金额）；601066 的 exit_schedule 首批 70% + 双向触发被压扁成一次 100% 卖出。亏损归因于执行层违约，不是决策逻辑错误——决策层 #586 buy 契约止步于决策层。

数据链路断点（比行为断点更早）：

1. `outcome/store.py` `decision_log` 表无 `trigger_high`/`exit_schedule` 列，`position_size REAL` 列对档位名（"light"）归一失败落 NULL——结构化约束在落库时已丢失，仅存于 `rationale_snapshot` 冻结 JSON；
2. `execution/driver.py` `_decision_payload` 只提取 symbol/confidence/action/FM 结论 4 个字段；
3. `_plan_long_entries` 用 `aggregate_targets(method="equal")` 等权聚合，与决策档位无关。

## What Changes

- **信号链路**：`Signal` 契约扩展 `trigger_high` / `position_tier` / `exit_first_batch_ratio`（及 entry_price 透传）；`_decision_payload` 从 `rationale_snapshot` 解析；字段缺失时退化为既有行为并留痕（向后兼容旧信号）
- **条件入场**：申报了 trigger 的 buy，执行日 bar 未满足触发条件 MUST NOT 建仓，以结构化原因留痕（`TRIGGER_NOT_MET`），禁止静默
- **仓位档位**：`position_tier` ∈ {light, moderate, heavy} → 目标权重按下沿锚 10%/30%/50% 映射（替代对 long 的等权聚合）；缺档位退化为等权（既有行为）
- **分批退出**：`exit_schedule` 申报首批比例的 exit，首个执行日仅卖出该比例；首批执行状态持久化防次日重复；schedule 不可解析时退化为全额卖出（既有行为）并留痕
- **对账留痕**：执行日批摘要新增 `plan_deviation` 段（成交价 vs entry_price、目标权重 vs 档位声明、分批进度 vs schedule），偏差可回查

## Capabilities

- **Modified Capabilities**: 无
- **New Capabilities**: `broker-execution` 追加 5 条 ADDED Requirement（条件入场门 / 档位权重映射 / 分批退出 / 信号契约扩展 / 执行对账留痕）

## Impact

- `src/finance_agent/execution/`（signals.py / driver.py / portfolio.py / store.py）
- `src/finance_agent/outcome/store.py`（可选：decision_log 加列或在信号阶段解析 snapshot，设计期定）
- 不改 trader/risk_judge prompt（`entry_cap` 结构化申报留待后续 delta；Phase 1 靠对账留痕暴露超限成交）
- 纯后端逻辑变更，不触发 E2E 门禁（§3.5 不适用）
- paper broker 生产行为变化：下一执行日批起，buy 必须过触发门、权重按档位、exit 按首批比例
