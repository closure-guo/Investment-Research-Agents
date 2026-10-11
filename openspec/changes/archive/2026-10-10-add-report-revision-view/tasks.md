# Tasks: add-report-revision-view

- [x] `update_session_report` 两处调用点持久化 `final_trade_decision`（并入 `agent_process` JSON 或新增列）+ 单测 —— 实现择「新增列」（复用既有 ALTER 迁移清单，`final_trade_decision TEXT` 可空列；无终稿落 NULL），api.py fast path 与 agent_factory ReAct 工具路径均传参
- [x] `session_store` 按 stock_code 回溯最近 completed 会话的只读查询（排除 running/failed/自身）+ 单测 —— `get_previous_completed_session`，返回 session_id/created_at/final_trade_decision（解析后）/kpi 子集；DB 异常降级 None 不阻断管线
- [x] 报告头「距上次报告」增量摘要渲染（方向/置信度/触发位/现价/PE 五维度，「无变化/未申报」纪律，首份不渲染）+ 单测 —— `_format_revision_summary`，全维度结构化 diff，零 LLM 调用
- [x] 「多空辩论结论」分歧卡渲染（结构化评级 + 置信度 + 结论首句；缺字段降级纯文本）+ 单测 —— `_format_divergence_card`，取 research_manager_rating/confidence + 结论文本首句
- [x] 同一标的连跑两份报告端到端验证 diff 正确（含「无变化」与「未申报」两种形态）—— `TestRevisionEndToEnd`：真实 DB 落库 → 回溯查询 → state 注入 → generate_report 渲染全链，两形态各一
- [x] 人工验证报告落 `tests/validation/`（重读用户视角抽查增量摘要准确性）—— `2026-10-10-add-report-revision-view-validation.md`：本地后端+副本 DB 真实 LLM 连跑两份 601066，实跑暴露 AnalysisState 未声明键被图丢弃 + TradeDecision pydantic 两处口径共三缺陷（已修+回归测试钉住），复跑五维度 diff 数字全部可溯源、PE 缺失如实「未申报」、分歧卡与落库 JSON 正确
