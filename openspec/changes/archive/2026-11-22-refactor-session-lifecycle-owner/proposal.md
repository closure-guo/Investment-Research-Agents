# Proposal: refactor-session-lifecycle-owner

## Why

会话状态流转（clarifying→running→completed/failed/interrupted）的规则写在 **4 个 owner**：api.py 8 处 `update_session_status` 手写 transition + 6 处 `update_session_for_clarify` + pipeline_runner/stream_registry 各自处置 + 前端 streamStore phase 映射。openspec `session-streaming` 把终态语义写成一段契约，实现里拆成 4 份——一处改、三处漏的风险面 ×4（interrupted 三源判别家族的根因）。另有两个启动流程（`screening_analyze` 与 `events_watchlist_analyze`）约 30 行近乎逐行重复。终态落库兜底（terminal_pending）写侧在 session_store、补写在 scripts/repair——没有 module 端到端拥有它。

## What Changes

- 新增 `src/finance_agent/sessions/lifecycle.py`，interface 五个动作：
  - `begin_analysis(session, source) -> session_id`——create_session + append_chat + set_pipeline_anchor + clarify→running 一段式启动（含 session_created 事件），两个重复启动流程收编
  - `mark_running / mark_complete / mark_failed / mark_interrupted(session_id, **ctx)`——统一流转入口（内部委托 session_store 原子 setter + 事件写入语义不变）
- api.py 14 处流转调用改走 lifecycle；pipeline_runner / stream_registry 的状态读写不动（registry 读 `status=="interrupted"` 的既有行为保留——它是订阅侧恢复语义，非流转 owner）
- terminal_pending 兜底的写入语义不变（session_store 承载），lifecycle 不新增兜底逻辑——本 delta 只收「流转 + 启动」两段
- 前端零变化（继续消费同一事件流）；SSE 事件类型不增不减

**非目标**：前端 streamStore phase 映射（消费侧，事件流不变则不动）；terminal_pending 跨端到端 owner 化（写/补分属两层是既有结构，另立）；多 worker 场景。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: session-streaming（ADDED requirement：状态流转单一 owner）、session-persistence（ADDED requirement：启动流程单一实现）

## Impact

- `src/finance_agent/sessions/lifecycle.py`（新增）
- `src/finance_agent/api.py`（14 处流转调用 + 2 个启动流程收编，预计 −150 行）
- `src/finance_agent/screening/pipeline.py`、`events/` 启动调用方如有同步引用一并收编
- **交互类变更判别**：涉及会话状态流转 → §3 完整管线（E2E 门禁 + 人工验证）
