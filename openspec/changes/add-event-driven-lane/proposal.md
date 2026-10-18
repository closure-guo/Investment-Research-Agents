# 业绩预告事件线（add-event-driven-lane）

## Why

2026-10-18 调研结论：事件驱动（业绩预告窗口）是「个人容量优势」与「本系统能力（读懂公告、排雷、溯源）」的交集区——比的是信息解读而非手速，容量不受限；且与现有 T+20 观点线收益来源不重叠。当前系统没有任何业绩预告的结构化数据（`akshare_client.py` 零命中），也没有独立于 T+20 的短周期记账口径。本变更是该程序的第 3 项（顺序见调研备忘录 §4.2）。

## What Changes

- **新 capability「earnings-event-lane」**：
  - **业绩预告数据落库**：公开数据源抓取（code/报告期/预告类型/净利润变动幅度上下限/公告日期/来源），公告日期作为可得时点；幂等 upsert；时点纪律（回填与判断只用公告日可得信息）；失败重试与如实留痕。
  - **日批扫描与 LLM 分诊**：新日批 job 扫描新增预告 → 构造分诊输入 → LLM 输出合同 `verdict ∈ {outperform, in_line, risk}` + `reason ≤ 50 字` + 证据引用；单条失败降级 `in_line` + warning 不阻断批次；结果落库含模型与 prompt 指纹。prompt 权威源 `src/finance_agent/prompts/event_triage.md`，发布走 `deploy_prompts.py`。
  - **事件观察清单与深度分析接线**：`outperform` 进清单（落库+只读端点+页面）；清单条目可发起既有深度分析队列（复用 `screening/queue.py`，来源标记 `event`；MUST NOT 另建并发通道）。
  - **事件独立记账与结算**：独立表 `event_journal`（入场=次一交易日开盘口径、可成交性沿用执行链门禁语义、horizon 默认 T+5 可配置、到期结算）；读数注册进 strategy-scorecards 注册表（family=`event`）；MUST NOT 混入 T+20 观点统计。
- **frontend ADDED「选股页事件观察区块」**：清单列表（预告要素 + 分诊结论三态 + 证据理由 + 发起深度分析入口 + 排队状态）。
- **口径登记**：`docs/evals/metrics.md` §1 新增小节。

## Impact

- 代码：`data/`（业绩预告抓取，新）、`outcome/events/` 或等价模块（清单 + 记账 + 结算 job）、`outcome/scheduler.py`（新日批 job）、`api.py`（清单端点）、`frontend`（选股页区块）、`src/finance_agent/prompts/event_triage.md`（新 prompt）。
- 数据：新库/新表（事件清单 + `event_journal`），路径按既有 `*_DB_PATH` 范式；E2E 配置注入隔离路径。
- 行为：纯增量——不改变筛选/决策/执行链；事件线不产生订单（v1 为研究与观察通道）。
- 非目标：不进执行链目标持仓（不产生订单）；不做事件线专用基准对照（v1 只记收益）；不做转债条款事件（后续变更 `add-convertible-bond-coverage`）。
