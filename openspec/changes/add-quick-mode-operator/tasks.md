# Tasks: add-quick-mode-operator

## P1：分析派单

- [x] `tool_registry.py` 新增 `start_stock_analysis` 与读/写分类集合；工具名单一来源测试更新
- [x] 从 `api.py` fast path 抽取共享派单服务，HTTP fast path 与 operator 工具共用；既有 fast path 行为回归测试
- [x] `start_stock_analysis` 工具工厂：6 位代码校验、回执字段、同 Agent 实例重复派单幂等、失败显式报错
- [x] `search_stock` 在快速模式复用深度模式同一实现
- [x] 快速模式 Agent 注册两个工具，`operator_enabled` / `QUICK_OPERATOR_TOOLS` 开关生效；`max_iterations=6`
- [x] capability 门禁：前端透传 probe capability；端点读 `tool_call` 决定注册；不可用时提示词披露
- [x] `quick_mode.md` 增加派单政策；提示词发布
- [x] AG-UI / E2E：工具事件序列、回执、管线事件不出现在快速通道
- [x] 验证报告 `tests/validation/2026-10-21-add-quick-mode-operator-validation.md`

## P2：只读系统查询

- [x] `tool_registry.py` 注册 7 个只读工具名并加入 `READ_TOOLS` / `QUICK_OPERATOR_TOOLS`
- [x] `operator/reads.py`：`get_screening_pool` 紧凑摘要（池 ≤20、队列 ≤10、stale/批次状态）
- [x] `operator/reads.py`：`get_track_record_overview`（胜率/超额/组合/回避/paper 损耗，样本不足如实）
- [x] `operator/reads.py`：`get_broker_overview`（资金/市值/总资产/浮盈 + 持仓 ≤20，缺价 null + 标注）
- [x] `operator/reads.py`：`get_daily_ops_status`（调度器状态 + 26 个任务下次触发/最近运行，无 history 全量）
- [x] `operator/reads.py`：`get_memory_clauses`（默认 pending，≤20）
- [x] `operator/reads.py`：`get_factor_library`（≤20，名称/状态/ICIR/最近闸门）
- [x] `operator/reads.py`：`get_factor_research_status`（运行/陈旧/进度/control/agent）
- [x] 只读工具单测：成功整形、空数据、异常、缺价/样本不足披露（8 项）

## P3：受控写操作

- [x] `operator/confirmation.py`：预览 token（TTL 300s、session/action/params 绑定、单次使用、用户确认词校验、token 可省略时按 session+action+params 匹配）
- [x] `operator/writes.py`：`set_factor_research_running`（预览/确认、honor/pending/ETA、409 原样披露）
- [x] `operator/writes.py`：`run_ops_job`（JOB_IDS 白名单、后台执行、already_running/cohort_disabled 明确错误）
- [x] `operator/writes.py`：`review_memory_clause`（confirm/reject、预览含条款文本、未知 ID 报错）
- [x] `operator/writes.py`：`start_pool_analysis`（预览池大小、空池报错、复用 AnalyzeQueue）
- [x] 写工具单测：预览无副作用、无确认词拒绝、参数篡改拒绝、token 过期、跨 run 匹配、成功执行一次、409 披露（11 项）
- [x] `operator_write` 结构化审计日志（session/action/params/result，不含密钥/token）

## 接线与验证

- [x] `agent_factory.py` 快速模式注册 7 读 + 4 写工具；`current_user_message` 注入工具闭包
- [x] AG-UI 端点与 `/api/chat` 传 `current_user_message`
- [x] QuickThread 回执卡片：`start_stock_analysis` 回执渲染「打开分析会话」按钮；App 接线 `onOpenSession`
- [x] `quick_mode.md` 增加只读/写操作政策与确认协议；Langfuse staging version 31
- [x] E2E：分析派单（含打开会话按钮）+ 日批只读查询（2 passed）
- [x] 定向 108 项 pytest + `ruff` + `mypy` 绿
- [x] 前端 `tsc -b` + `vitest run` 889 passed
- [ ] 全量 `uv run pytest` 在 CI/带 Langfuse 环境补跑（本机 30+ 分钟未完成）
- [ ] 人工验证报告更新（已更新；owner 主观抽验待做）
- [x] `openspec validate add-quick-mode-operator --strict` 通过
