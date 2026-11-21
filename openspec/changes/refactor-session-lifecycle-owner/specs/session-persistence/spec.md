# Delta for session-persistence

## ADDED Requirements

### Requirement: 分析启动流程单一实现

多入口分析的启动编排（create_session → append_chat → set_pipeline_anchor → clarify→running → session_created 事件）SHALL 为单一实现（`sessions/lifecycle.begin_analysis`），各入口 SHALL NOT 保留逐行拷贝。入口差异（来源标注、队列注册）SHALL 以参数表达。落库语义（SESSIONS_DB_PATH、append-only 事件流、状态冻结字段）SHALL 不变。

#### Scenario: 两个自动分析入口共享同一启动实现

- **GIVEN** screening_analyze 与 events_watchlist_analyze 发起分析
- **WHEN** 各自调用 `begin_analysis`
- **THEN** 落库与会话状态结果 SHALL 与各自原有逐行实现完全一致（行为不变，实现归一）
