# Investment Research Agents — Domain Context

## Purpose

面向 A 股市场的多 Agent 投研分析系统。模仿 TradingAgents (arXiv:2412.20138) 的 5 层架构：4 个分析师并行 → Bull/Bear 辩论 → Trader 决策 → Risk Management 辩论 → Fund Manager 批准。最终产出综合分析报告，包含交易建议（买入/持有/观望/卖出）。

> **合规声明**：报告由 AI 系统基于公开数据自动生成，仅供参考研究，不构成投资建议。

## Language

### Analysis Architecture

5 层架构，参考 TradingAgents (arXiv:2412.20138)。

#### Layer I: Analyst Team（4 个并行分析师）

**Macro Analyst Agent**（宏观分析师）:
解读宏观经济环境、货币政策、行业政策对标的的影响。输入：宏观指标（CPI/PMI/M2/LPR）+ 政策动态。输出：宏观环境评估。
_Avoid_: 宏观经济 Agent

**Fundamental Analyst Agent**（基本面分析师）:
向后看：诊断公司过去 3-5 年的财务健康状况 + 相对估值。基于确定的事实（报表数字），不推测。职责 = 原 Financial Analysis（四维度+杜邦）+ 相对估值（PE/PB 同业对比 + GARP）。输出：健康度评分 + 风险点清单 + 估值结论。
_Avoid_: 财务分析 Agent、投资分析 Agent（已废弃，融入基本面分析师）

**Technical Analyst Agent**（技术面分析师）:
分析价格走势、成交量、技术指标、支撑压力位。输入：日 K 线序列（OHLCV）。输出：技术分析判断（趋势/超买超卖/关键价位）。
_Avoid_: 行情分析 Agent

**Sentiment Analyst Agent**（舆情分析师）:
追踪新闻舆情、社交媒体情绪、重大事件影响。输入：个股新闻 + 财联社快讯 + 关键事件（Key Event）。输出：舆情监控 + 事件影响分析。
_Avoid_: 新闻 Agent、情绪 Agent

#### Layer II: Researcher Team（Bull/Bear 辩论）

**Bull Researcher**（看多研究员）: 基于分析师报告构建看涨论点，挖掘积极信号和增长潜力。
**Bear Researcher**（看空研究员）: 基于分析师报告构建看跌论点，专注风险点和负面信号。
**Research Manager**（研究主管）: 综合 Bull/Bear 辩论结论，给出推荐。

辩论轮数：2 轮（立论 + 反驳）。

#### Layer III: Trader（交易员）

基于 Research Manager 的推荐 + 历史数据，做出交易决策（买入/卖出/持有/观望），确定交易时机和仓位建议。输出 `trader_investment_plan`。

#### Layer IV: Risk Management Team（风险管理辩论团队）

3 个风险辩论者 + 1 个裁决者，压力测试 Trader 的交易计划：

| 角色 | 风险偏好 | 职责 |
|------|---------|------|
| Aggressive Debator | 激进 | 优先增长，挑战保守观点 |
| Conservative Debator | 保守 | 优先资本保全，识别高风险因素 |
| Neutral Debator | 中性 | 平衡上下行，主张分散化 |
| Risk Judge | 裁决 | 综合辩论 + PREP 风控指标（回撤/波动率/Beta），产出 `final_trade_decision` |

辩论轮数：2 轮。输入：Trader 的 `trader_investment_plan` + 4 份分析师报告 + PREP 风控指标。

#### Layer V: Fund Manager（基金经理）

审阅 `final_trade_decision`，批准/拒绝/退回修改。退回修改最多 1 次（防死循环）。批准后写入决策日志，生成最终报告。

### Graph Topology

LangGraph 拓扑（静态展开，无循环边）：

```
START → check_cache → [fetch_data →] validate → compute_metrics
  → Send([macro, fundamental, technical, sentiment])  ← 4 路并行
  → Send([bull_r1, bear_r1])                          ← Round 1 并行
  → Send([bull_r2, bear_r2])                          ← Round 2 并行（读对方 r1 反驳）
  → research_manager
  → trader
  → Send([aggressive_r1, conservative_r1, neutral_r1]) ← Round 1 并行
  → Send([aggressive_r2, conservative_r2, neutral_r2]) ← Round 2 并行
  → risk_judge
  → fund_manager
  → [trader（如果退回）] 或 generate_report
  → END
```

- Bull/Bear 辩论：2 轮，每轮 Bull 和 Bear 用 Send 并行产出，不串行等待
- Risk Management 辩论：2 轮，每轮 3 个辩论者用 Send 并行产出
- Fund Manager 退回：条件边，`return_count < 1` 时回到 trader，否则强制 approve/reject
- State 结构：混合 —— PREP 字段保持扁平（兼容现有代码），agent 输出用嵌套（`analyst_reports: dict[str, AnalystReport]` / `debate_history: list[DebateMessage]` / `trade_decision: TradeDecision`）

### Deprecated Terms

| 旧术语 | 状态 | 替代 |
|--------|------|------|
| Financial Analysis (FA) | 已废弃 | 职责并入 Fundamental Analyst Agent |
| Investment Analysis (IA) | 已废弃 | 估值部分并入 Fundamental Analyst Agent |
| Comprehensive Analysis | 已废弃 | 5 层架构是默认模式，不再有"综合"特殊类型 |
| fa_analyze / ia_analyze 节点 | 已废弃 | Analyst Team 4 个 Agent 替代 |
| Risk Control Agent（并行分析师） | 已废弃 | 改为 Risk Management Team，移到 Trader 之后 |

### Fundamental Analysis Framework: Four Dimensions + DuPont

| Dimension              | Chinese    | Core Question    | Metrics                                                                          |
| ---------------------- | ---------- | ---------------- | -------------------------------------------------------------------------------- |
| Solvency               | 偿债能力   | 公司会不会破产？ | 资产负债率、流动比率、速动比率、利息覆盖倍数、净债务/EBITDA                      |
| Profitability          | 盈利能力   | 公司赚不赚钱？   | 毛利率、净利率、ROE、ROA、ROIC                                                   |
| Operational Efficiency | 运营效率   | 资产周转快不快？ | 存货周转率、应收账款周转率、总资产周转率、应付账款周转率                         |
| Cash Flow Health       | 现金流健康 | 利润是不是真钱？ | 经营现金流/净利润、FCF、资本支出/折旧、现金流覆盖比率、FCF收益率、留存现金流比率 |

**DuPont Decomposition**（杜邦分析法）: 3 层递归拆解 ROE 变动原因。

### Valuation Framework

| Method             | Chinese  | What It Does     | Model          | MVP   |
| ------------------ | -------- | ---------------- | -------------- | ----- |
| Absolute Valuation | 绝对估值 | 这家公司值多少钱 | DCF（FCF折现） | ❌ v2.0 |
| Relative Valuation | 相对估值 | 相比同行贵不贵 | PE、PB 同业对比 | ✅     |

**GARP**（Growth at a Reasonable Price）: PE < 行业平均 ∧ 净利润增长率 > 15% ∧ ROE > 15% ∧ 负债率 < 60%

### Technical Analysis Framework

| Dimension        | Core Question     | Metrics                              |
| ---------------- | ----------------- | ------------------------------------ |
| Trend            | 趋势方向          | MA（5/10/20/60）、MACD               |
| Momentum         | 超买超卖          | RSI、KDJ                             |
| Volatility       | 波动边界          | 布林带（BOLL）                       |
| Support/Resistance | 关键价位        | 前高前低、筹码密集区                  |

### Risk Framework

| Dimension      | Chinese  | Source                                     |
| -------------- | -------- | ------------------------------------------ |
| Operating Risk | 经营风险 | 行业竞争格局、收入集中度                   |
| Financial Risk | 财务风险 | 杠杆率、利息覆盖倍数（复用四维度偿债指标） |
| Market Risk    | 市场风险 | Beta、波动率、最大回撤                     |
| Policy Risk    | 政策风险 | 监管变化、行业政策（复用宏观分析结论）     |

### Scoring Model

| Term          | Definition                                                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Traffic Light | 红黄绿灯。每个指标通过双重阈值评判：绝对值水平(优良/关注/警告) + 同比变化率(稳定/<20% / 波动/20-50% / 异动/>50%)。最终 = max(绝对值灯, 变化率灯) |
| Health Score  | 健康度评分。四维度各 25 分，🟢=满分 🟡=半分 🔴=零分。85-100=健康，60-84=关注，<60=警告                                                           |

### Report

综合分析报告 + 交易建议。5 层架构产出：4 分析师并行 → Bull/Bear 辩论 → Trader → Risk Management → Fund Manager → 报告。
_Avoid_: Financial Report（8 章版，已废弃）、Investment Report（7 章版，已废弃）

**报告原则**：Markdown 文本 + 表格 + ECharts 交互图表（前端）+ matplotlib PNG 图表（docx/pptx 导出）。图表数据来自 PREP 阶段的结构化财务数据，一次收集两处渲染。

**结构化输出**：Layer I 的 4 个 Analyst Agent 各输出 Pydantic 结构化对象（而非自由 Markdown），用于 Agent 间信息传递。Layer II-V 的辩论/决策也输出结构化对象。最终合并渲染为报告。

**交易建议**：报告包含交易建议（买入/持有/观望/卖出），由 Trader 提议、Risk Management 压力测试、Fund Manager 批准。附带免责声明。

**三模式设计**：

| 模式 | 工具集 | max_iterations | 产出 | 状态 |
|------|--------|---------------|------|------|
| 深度模式（默认） | search_stock, run_deep_analysis, web_search | 10 | 完整 5 层架构 -> 10 章报告 | ✅ v1.0 |
| 快速模式 | web_search | 3 | 精简分析 | ✅ v1.1 |
| 追问模式 | web_search | 3 | 基于已有报告的问答 | ✅ v1.2 |

三种模式均由 ReAct Agent（Agent Harness）统一编排。模式不再决定代码路径，而是影响 Agent 的工具集、迭代上限和 system prompt。深度模式将 5 层管线封装为一个流式工具（run_deep_analysis），Agent 通过工具调用触发管线，管线进度通过流式事件实时推送。快速模式和追问模式不暴露深度分析工具，从 schema 层面杜绝意外触发。

深度模式的入口遵循对话流：用户输入自然语言查询后，Agent 先调用 `search_stock` 解析标的；若标的不明确或意图不完整，Agent 以普通助手消息反问，用户在主输入框回答；信息足够后 Agent 调用 `run_deep_analysis`。澄清阶段属于 Session 生命周期的一部分。

**报告章节结构**（分析师主导，线性流程）：

| 章 | 标题 | 内容来源 | 字数 |
|----|------|---------|------|
| 1 | 封面 | 标的名称、日期、评级 | - |
| 2 | 执行摘要 | Fund Manager 的最终决策 + 关键理由 | 300-500 |
| 3 | 宏观环境分析 | 宏观分析师输出 | 500-800 |
| 4 | 基本面分析 | 基本面分析师输出（四维度+杜邦+估值） | 1000-1500 |
| 5 | 技术面分析 | 技术面分析师输出（趋势+指标+关键价位） | 500-800 |
| 6 | 舆情与事件分析 | 舆情分析师输出（新闻+事件+情绪） | 500-800 |
| 7 | 多空辩论摘要 | Bull/Bear 辩论核心论点 + Research Manager 结论 | 500-800 |
| 8 | 交易建议 | Trader 计划 + Risk Management 评估 + Fund Manager 批准 | 500-800 |
| 9 | 风险提示 | Risk Management 辩论中的风险点 + PREP 风控指标 | 300-500 |
| 10 | 免责声明 | AI 生成 + 仅供参考 + 不构成投资建议 | 固定 |

### Key Event

关键事件。已发生且对经营有持续影响的非财务事实（提价、渠道变革、管理层变动等）。作为 Sentiment Analyst Agent 的输入之一。

### Session

会话。一次股票深度分析的完整工作单元，**从用户首次输入自然语言查询开始**（包含可能的意图澄清阶段），经过 5 层分析，最终生成报告，并支持后续追问。每个会话独立存储于后端 SQLite，包含：最终报告 Markdown、chart_data、4 份 AnalystReport 完整 JSON、Layer II-V 中间输出（辩论/决策/风控/基金经理）、analyst summary 摘要、完整的对话历史（含澄清阶段）。用户可在侧边栏新建/切换/搜索/重命名/删除会话。不支持多会话并行（一次只跑一个 pipeline）。

会话状态流转：

- `clarifying`：Agent 正在等待用户回答澄清问题（标的不明确或意图不完整）
- `running`：5 层分析管线正在执行
- `completed`：报告已生成
- `failed`：执行失败

_Avoid_: Conversation（泛指对话，不精确）、Chat Thread

> **与 Langfuse Session 的区别**：同名不同义。Langfuse Session 不是存储单元，是 Langfuse 里附加在 Trace 上的分组属性（`session_id`），用于把多个 Trace 聚合成一次会话回放。本项目里 Langfuse `session_id` = 本 Session 的 ID（1:1 映射），一个 Session 对应 N 个 Langfuse Trace（深度分析 1 个 + 每次追问 1 个 + 澄清阶段可能产生 1 个）。不要把"一次 Session = 一条 Langfuse Trace"。

### Trace

Langfuse Trace。一次用户交互的完整追踪单元 = 一次 `react_agent.py` 调用。深度分析交互产生 1 个 Trace（ReAct 思考 + search_stock/web_search 工具调用 + run_deep_analysis 5 层管线全部在同一 Trace 内）；每次追问产生独立的 1 个 Trace。同一 Session 的所有 Trace 通过 `session_id` 聚合为 Langfuse Session。

_Avoid_: 把整个 Session（深度分析 + 多轮追问）塞进单条 Trace（会无限增长、丧失按交互计费/评分能力、退化 Langfuse Session 的多 Trace 分组语义）。

### Span

Langfuse Span。Trace 内的嵌套观测节点。本项目 span 层级：Trace(user-turn) → span(run_deep_analysis) → span(prep) / span(macro_analyst) / span(fundamental_analyst) / ... / span(bull_r1) / span(bear_r1) / ... / span(trader) / span(risk_judge) / span(fund_manager)。LangGraph `Send` 并行的 4 个分析师 / Bull-Bear / 3 个风险辩论者互为兄弟 span。

### Generation

Langfuse Generation。LLM 调用观测，挂在所属 Agent 的 span 下，记录 input/output/model/usage。一个 span 可含多个 generation（如辩论者多轮）。Generation 上附带 `prompt_name` + `prompt_version`，使"这份报告由哪版 prompt 产出"可追溯。

_Avoid_: 用 `start_observation` 创建孤立 generation（不建立父子上下文，5 层结构会塌成扁平列表）-- 必须用 `start_as_current_observation` 或 `@observe`。

### Langfuse Prompt

Langfuse 托管的 prompt 资源。本项目采用 **P-2 混合+兜底**：[prompts/](src/finance_agent/prompts) 下 14 个 `.md` 全部迁入 Langfuse，同时本地文件作为兜底基线保留并 commit。

- 权威源：配置 Langfuse 时以 Langfuse 的 `production` label 版本为准；未配置或拉取失败时回退本地 `.md`，回退时打 `WARN` 日志（漂移可见但不阻断）。
- 版本控制：改 prompt 不重新部署，在 Langfuse UI 把新版打 `production` label 即上线；旧版保留可回滚。
- 类型：全部 `text` 类型（type 不可逆，chat 类型留待未来多轮 prompt）。
- 模板语法：`{{var}}`，`prompt.compile(**vars)` 渲染（与本地 loader 语法一致）。
- 版本挂载：Generation 观测附带 `prompt_name` + `prompt_version`。

_Avoid_: 把线上真实 prompt 的真相仍归于 git（启用 Langfuse 后 git 里 `.md` 是兜底基线，可能滞后；真相在 Langfuse + trace 上的 prompt 版本记录）。把 Langfuse 设为硬依赖（违背本地开发与离线场景）。

### Langfuse Score

Langfuse 评估打分。本项目分三层部署：

- **L0（立即）**：`verify_citations` 节点的 `citation_pass: bool` 通过 `langfuse.score(trace_id=, name="citation_pass", value=, data_type="boolean")` 上报，挂 **trace 级**。`citation_report` 明细（每条 claim 的 PASS/FAIL/UNVERIFIABLE）作为 metadata 附在 `verify_citations` span 上，供下钻定位。兑现 ADR-0010 第 124 行"幻觉率成为可监控指标"的承诺。
- **L1（中期）**：确定性维度（citation/杜邦分层/勾稽）用 **Code Evaluator**（复用 `metrics/` 纯函数）；主观维度（分析深度/逻辑连贯/表达）用 **LLM-as-a-Judge**。两者分工--不让 LLM 判数字对错（citation 的活），只判主观质量（呼应 ADR-0010 第 126 行）。
- **L2（长期）**：标的输入快照进 **Dataset**，prompt 改版跑 **Experiment** 对比新旧版，CI 卡回归。

_Avoid_: 让 LLM-as-Judge 判数字对错（评审 LLM 自身会幻觉，ADR-0010 已明令禁止）。把 citation score 挂 generation 级（校验对象是分析师 span 输出，非单次 LLM 调用）。

### Follow-up

追问。报告完成后的后续提问。ReAct Agent 配置之一：max_iterations=3，工具集 = [web_search]。上下文 = 报告 Markdown（前 6000 字符）+ 4 个分析师 summary + 之前的 chat_history（从 SQLite session 恢复），注入 Agent 的初始 context。不暴露 run_deep_analysis，避免意外重新触发 5 层管线。追问回复逐 Token 流式输出。

### Streaming

流式输出。ReAct Agent 的所有事件（思考、工具调用、管线进度、工具结果、最终回答）通过单一 SSE 通道推送。流式工具（如 run_deep_analysis）在执行期间 yield PROGRESS 事件，实时推送管线节点状态。前端通过 Fetch Stream 逐 chunk 读取并渐进渲染。

### Natural Language Input

自然语言输入。用户可输入股票名称（"宁德时代"）、6 位股票代码（"300750"）或自然语言指令（"分析茅台"）。后端 ReAct Agent 调用 `search_stock` 工具解析为股票代码；若 LLM 不认识或存在歧义，则工具 fallback 到 AKShare `stock_info_a_code_name` 模糊匹配。

当用户输入存在歧义（如"光模块龙头"对应多个候选）或意图不完整（仅给出股票名称未说明分析方向）时，Agent 以对话消息反问，用户在主输入框回答。澄清后的关注点（focus）注入 5 层分析管线，用于调整各分析师的侧重点和报告章节排序。

### Quick Mode

快速模式。ReAct Agent 配置之一：max_iterations=3，工具集 = [web_search]。不暴露 run_deep_analysis，从 schema 层面杜绝触发 5 层管线。LLM 依赖自身知识回答，信息不足时自主发起 Tavily 搜索补充实时信息。搜索过程通过 SSE 事件流推送给前端，前端展示可折叠搜索横幅（类 Kimi）：搜索中显示"正在搜索：{query}"，搜索完成后显示"搜索了 N 个网页"，点击展开显示网页列表（标题 + URL + 摘要）。

### Data Sources

| Source     | Type              | Usage                             | TTL              |
| ---------- | ----------------- | --------------------------------- | ---------------- |
| AKShare    | 免费 A 股数据 API | 三大报表、行情、K线、行业分类、PE/PB、宏观、新闻 | 按数据类型差异化 |
| 巨潮资讯网 | 年报 PDF          | MD&A、风险披露（pdfplumber 解析） | 到下个财报季     | ❌ v2.0 |
| Tavily     | 网页搜索 API      | 快速模式 web search、行业新闻、趋势、政策              | 1天              | ✅ v1.1 |
| Chroma     | 向量数据库        | 研报 embedding，RAG 语义检索      | 永久（知识库）   | ❌ v2.0 |

### Architecture Terms

| Term                | Definition                                                                                                                                                                                                                                                                   |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data Prep Sub-graph | 数据准备子图。路由前的前置子图，包含 check_cache→[fetch_data→]validate_financials→compute_metrics。全权负责所有数据拉取和计算，4 个 Analyst Agent 的所有数据（含 K 线、宏观、新闻）均在此拉取并计算，一次性全量注入各分析师的 prompt context。 |
| Analyst Agent       | 分析师 Agent。Layer I 的 4 个并行 Agent 之一（宏观/基本面/技术面/舆情）。深度模式下数据由 PREP 一次性注入，无 tool calling；输出 Pydantic 结构化对象。风控不在 Layer I，已移至 Layer IV Risk Management Team。 |
| Layer 1             | 基础公共数据。三大报表 + 行情 + 行业归属 + 日 K 线序列 + 沪深 300 K 线，始终拉取。 |
| Layer 2             | 分析导向数据。按维度选择性拉取（宏观指标、新闻舆情、预计算指标、行业 PE）。 |
| Layer 3             | 衍生计算。pandas 从原始数据计算：四维度 20 指标 + 杜邦 + 红黄绿灯 + 同业对比 + GARP + 技术指标（MACD/RSI/布林带/KDJ）+ 风控指标（回撤/波动率/Beta/VaR）+ 宏观指标趋势 + 舆情统计，无 API 调用。情感分析由舆情 agent 自行完成（LLM 推断），PREP 不做情感打分。DCF 定量计算计划于 v2.0。 |
| Cross-Validation    | 勾稽校验。在指标计算前校验三张报表的内在一致性。4 条规则：①试算平衡（资产=负债+权益，硬等式）②利润表内部勾稽（净利润≈利润总额-所得税费用）③现金流量表内部勾稽（经营+投资+筹资=净变动）④留存收益勾稽（期末留存=期初+净利润-分红）。硬等式失败→终止；软等式失败→写 warning 继续。 |

> 注：规则 2 原始设计为「营业收入-营业成本-期间费用≈营业利润」，因营业外收支导致大量误报，实际实现改为「净利润≈利润总额-所得税费用」，更准确可靠。
