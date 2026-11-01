# Tasks: update-redispatch-fresh-session

- [x] `pool_analysis.prepare_pool_analysis_batch`：failed/dispatch_failed/session_failed 行重派
      一律创建全新会话（不携带旧 session_id），模块与函数 docstring 同步改
- [x] `report_trigger.prepare_report_analysis_batch`：dispatch_failed 行重派同样全新会话 +
      成功派发经 upsert 回写 `queued`；docstring 同步改
- [x] TDD 先红后绿：池分析复用断言翻转（旧会话终态下重派得新会话）+ 研报触发新增重派用例
      （修复前 2 failed 复现死路 → 修复后 passed）
- [x] `uv run ruff check` / `ruff format --check` / `uv run mypy`（基线对比）/ 定向测试全过
      （26 + 186 passed；mypy 98 = 既有基线）
- [x] `openspec validate update-redispatch-fresh-session --strict` 通过
- [x] 人工验证报告落 `tests/validation/`（2026-11-01-update-redispatch-fresh-session-validation.md；
      无新增交互面，复用既有 E2E 套件 CI 回归）
