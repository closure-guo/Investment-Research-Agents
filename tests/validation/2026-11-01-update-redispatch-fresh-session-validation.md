# 验证报告: update-redispatch-fresh-session（issue #546）

**日期**: 2026-11-01
**执行人**: agent（ZCode 会话，用户「继续，不用停」指令授权）
**关联 delta**: openspec/changes/update-redispatch-fresh-session/

## 变更定性

后端编排语义修复（B 类，行为契约修订）：失败行重派从「复用终态会话（死路）」改为「全新会话」。
无新增交互面——不改前端 UI、不改 SSE 事件契约（反而消除复用导致的 after_seq 混流风险）、
不改会话切换；队列四态徽标语义不变。E2E 门禁由 PR CI 既有 stub 套件回归覆盖（无新增 spec 需要）。

## 验证结果

| 验证项 | 命令/证据 | 结果 |
|---|---|---|
| 红测试（复现死路） | `pytest test_pool_analysis.py::test_dispatch_failure_creates_fresh_session_on_retry` + `test_report_trigger.py::TestCapsAndIdempotency::test_dispatch_failed_retry_creates_fresh_session` | 修复前 2 failed（旧代码复用旧 session_id）→ 修复后 passed |
| 定向回归 | pool_analysis + report_trigger 26 passed；受影响面（job/api_screening/report_trigger_api/daily_brief/ops_jobs/ops_catchup/scheduler）186 passed | 全过 |
| Lint/Format | `ruff check` + `ruff format --check` | 全过 |
| 类型 | `mypy` 98 errors = 既有基线（#544 记录值），无新增 | 基线持平 |
| OpenSpec | `openspec validate update-redispatch-fresh-session --strict` | 通过 |

## 行为契约要点（对齐 delta spec）

1. `queued` / `completed` 组合的幂等语义不变（不重复建会话、不重复烧分析）。
2. `failed` / `dispatch_failed` / `session_failed` 行重派：创建**全新会话**入队；旧会话原样保留
   （终态与历史事件不变，测试断言 `old["status"] == "failed"`）；运行行/触发行 `session_id`
   由幂等 upsert 更新为新会话。
3. 研报触发侧顺带修复：旧复用路径成功派发后触发行不回写 `queued`（行永远停在
   `dispatch_failed` 被无限重派）——新路径经 upsert 回写 `queued`（测试断言
   `run2["status"] == "queued"`）。

## 生产背景佐证

10-28 opencode 宕机恢复实操（issue #546 记录）：9 只复用会话入队→starter 拉起→队列终态轮询
首次读会话即判死→零事件产出；当天人工处置口径（清空 session_id 重派）即为本修复后行为，
无数据迁移需要。

## 结论

- [x] 全部通过，可 archive
