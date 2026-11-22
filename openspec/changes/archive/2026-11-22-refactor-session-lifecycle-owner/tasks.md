# Tasks: refactor-session-lifecycle-owner

- [x] sessions/lifecycle.py：begin_analysis / mark_running / mark_complete / mark_failed / mark_interrupted + update_clarify 透传，单测先红后绿（8 例，fake store 测完整序列）
- [x] api.py 流转调用收编（status 直调 9 处含多行形态 + clarify 6 处 + import 清理）
- [x] screening_analyze 与 events_watchlist_analyze 启动流程归一 begin_analysis（各 −22 行逐行重复）
- [ ] E2E spec 已覆盖核心交互场景（streaming.spec 既有场景回归 + 启动流程归一后的入口路径；本地默认套件需停生产容器且处红线窗口——由 PR CI stub 套件执行门禁）
- [x] 人工验证报告落 tests/validation/2026-11-22-refactor-session-lifecycle-owner-validation.md（状态机单测+实机流转记录；双入口实机批观察转 11-24 常规跟踪）
