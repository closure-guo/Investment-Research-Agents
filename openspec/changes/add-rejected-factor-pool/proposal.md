## Why

因子收割五道闸拒收的候选当前不落库（无 `factors` 行、无事件），只存在于收割状态文件与生成侧反馈 payload 中——**没有任何机制持续跟踪被拒因子的 IC**。但闸门判决自带时效：闸④（池增量）拒收依据的是"与当时池内因子的相关性"，池是动态的，池成员一变被拒因子可能立即产生增量；闸②③⑤的拒收也随数据窗口演进而可能翻转。生成侧重提是唯一的隐式复评路径，但属于"碰运气"而非保证（依赖 RD-Agent 是否再提）。要让未来的"IC 复苏自动复评"（后续 delta）有地基，必须先把被拒候选持久化并纳入 IC 日批跟踪。

## What Changes

- **拒绝池持久化**：五道闸拒收（闸①–⑤，已过转译、带 DSL 表达式的候选）在 `admit_candidates` 单点持久化为 `factors` 表 `status='rejected'` 行，携带表达式/假设/类别/来源/`ast_json`，并落 created 事件（detail 含拒收闸口、原因码、判定读数）；结构同构的重复拒收按 upsert 更新既有行，不重复插入。
- **拒绝池不进查重范围**：结构层查重范围维持 `candidate/active/degraded`（`retired` 允许重提的既有语义不变），`rejected` 行不参与查重——重提路径不受影响。
- **拒绝池对既有读路径不可见**：因子库只读列表不返回 `rejected` 行；builtin 注册的"库内已存在同表达式条目即跳过"判定不把 `rejected` 行视为已存在（被拒的 builtin 因子次日仍可重试）。
- **拒绝池 IC 日批跟踪**：衰减监控日批在 active 因子之外，额外对全部 `rejected` 行执行同口径的缺口回填式 IC 记录（复用 `record_ic` 幂等 upsert）；`rejected` 行不参与衰减判定与任何状态迁移；运行摘要新增拒绝池读数。
- **存量回填**：提供一次性回填脚本，从收割状态文件把既有 gate-rejected（带 DSL 表达式）候选按原裁决导入拒绝池；`untranslatable` / `transcription_error`（无 DSL）不导入。

**明确不在本 change 范围**：IC 复苏触发自动重评（后续 delta）、复评配额防刷（后续 delta）、拒绝池的前端/接口暴露（保持只读列表不返回，无 UI 变更）、untranslatable 族的任何复评路径。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `factor-library`: 新增三个 requirement——拒绝池持久化、拒绝池 IC 日批跟踪、存量拒绝候选回填；并为既有「五道闸准入管线」「衰减监控与自动降级」「builtin 因子注册路径」补拒绝池交互场景（拒绝行不进查重/不参与衰减判定/不被视为已注册）。

## Impact

- `src/finance_agent/factors/library.py`：`rejected` 状态入状态机（终态）、拒绝池写入/查重/upsert 辅助、按状态枚举。
- `src/finance_agent/factors/validate.py`：`admit_candidates` 拒收路径持久化（单点，三入口 harvest/builtin/monthly 自动生效）。
- `src/finance_agent/factors/builtin.py`：跳过判定排除 `rejected` 行。
- `src/finance_agent/factors/monitor.py`：日批扩扫 rejected 行记 IC（无降级判定）。
- `scripts/`：一次性回填脚本（部署后人工执行）。
- 因子库只读 API / 前端：**无变更**（列表过滤逻辑若当前未按状态过滤则补过滤，行为对外不变）。
- 衰减监控日批耗时：rejected 行量级为数百，逐条面板求值预计增加分钟级，仍在日批窗口内；配额治理留后续 delta。
