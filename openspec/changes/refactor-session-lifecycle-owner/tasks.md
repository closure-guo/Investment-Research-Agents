# Tasks: refactor-session-lifecycle-owner

- [ ] sessions/lifecycle.py：begin_analysis / mark_running / mark_complete / mark_failed / mark_interrupted，单测先红后绿（内存 fake session_store 即可测完整状态机）
- [ ] api.py 14 处流转调用收编（2 处 status 直调×路径 + 6 处 clarify + 终态组）
- [ ] screening_analyze 与 events_watchlist_analyze 启动流程归一 begin_analysis
- [ ] E2E spec 已覆盖核心交互场景（streaming.spec 既有场景回归 + 启动流程归一后的入口路径）
- [ ] 人工验证报告落 tests/validation/（会话流转状态机 + 双入口启动路径实机抽查）
