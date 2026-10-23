# Investment Research Agents

A 股全链路 AI 投研系统：从数据获取、选股与因子研究，到多 Agent 辩论分析、交易决策、报告导出与战绩跟踪，一站式覆盖。| End-to-end AI investment research for A-shares: data, screening, factor research, multi-agent debate, trade decisions, report export, and track record — all in one.

> **免责声明**：本项目仅供研究，不构成投资建议。战绩与 paper 仿真数据为研究性口径，不保证准确性，据此操作风险自负。

## 架构

<a href="docs/assets/system-flow.html">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/system-flow.dark.png">
    <img src="docs/assets/system-flow.png" alt="研究到战绩的全链路一图流（含研报数据面与分析师事后检验）">
  </picture>
</a>

> 全链路一图流由 [Archify](https://github.com/tt-a1i/archify) 生成（图源 [system-flow.json](docs/assets/system-flow.json)，交互版 [system-flow.html](docs/assets/system-flow.html) 支持缩放、搜索、明暗主题与导览路径）。用户输入与选股日批（15:40 出精选池，18:00 批量入链）汇入同一张 LangGraph 图：PREP 数据准备 → L1 四分析师并行 → L2 多空辩论×2 → L3 Trader（价位门禁）→ L4 风控辩论×2 → L5 基金经理审批产出报告；决策落库观点池后 D+1 开盘 paper 撮合，收盘结算入战绩，记忆层回灌下一次分析。因子供给线（RD-Agent → 转译 → 五道闸 → 因子库）与 28 个日批/月批任务的触发时刻标在图上，九个 SQLite 库、多源数据面与 LLM 网关治理见说明卡；研报消费链（五类元数据/评级变动/一致预期/投喂 PDF → 分析师事后检验/策略观点→宏观/关联标的触发分析/外资快讯只读）也已标在图上；节点级完整拓扑的文字版见 [graph.mmd](docs/assets/graph.mmd)。

五层架构：

| 层级     | 选型            | 职责                                                              |
| -------- | --------------- | ----------------------------------------------------------------- |
| L1 前端  | React 18 + Vite | 自然语言输入 + SSE 流式渲染 + 报告展示 + 文件下载 + 会话管理      |
| L2 Agent | LangGraph + ReAct Harness | 5 层架构 + 多 Agent 辩论 + Send 并行派发 + 三模式编排     |
| L3 数据  | pandas + SQLite | AKShare 拉取 + 指标计算 + K 线/宏观/新闻 + 报表持久化 + 行情缓存  |
| L4 LLM   | LLM Provider Gateway | ProfileResolver 唯一解析入口 + 能力探测门禁（两级判定）+ LiteLLM 收口适配 + fallback 链执行（typed error 按链自动切换 profile，每跳 trace 可审计）+ 纯文本输出合同守卫（思考独白泄露/截断/空正文不进交付物）；默认 opencode zen/go 网关 deepseek-v4.1-flash |
| 可观测性 | Langfuse        | LLM 调用链路追踪 + Prompt 版本管理 + 引用校验评分                 |

> L2 Agent 5 层：4 分析师并行 -> Bull/Bear 辩论 -> Trader -> Risk Management 辩论 -> Fund Manager（详见 [ADR-0011](docs/adr/0011-five-layer-architecture.md)）

## 功能特性

### 分析决策
- **三模式分析**：支持股票名称、代码或自然语言指令；深度分析（5 层完整管线 → 10 章报告）/ 快速搜索（Tavily Web 搜索，精简回答）/ 追问（基于已有报告的上下文问答）；标的不明确时 Agent 主动反问澄清（ADR-0017）
- **五层多 Agent 管线**：4 分析师并行（宏观/基本面/技术面/舆情）→ Bull/Bear 辩论 + Research Manager → Trader → 风控辩论（3 辩手 + 裁决）→ 基金经理审批；SSE 实时推送节点状态、耗时与 ETA
- **决策可执行**：Trader 方案强制填写 entry/stop/target，价位校验器拦截缺位、越界和非价格记号；决策滞回吸收 LLM 采样翻转，同一证据下不反复改判
- **引用与幻觉治理**：Claim 6 类分类法 + computational 公式重算 + 术语/期次一致性校验；行内引用上标 + hover 预览卡；纯文本输出合同守卫拦截思考独白泄露、截断和空正文
- **报告交付**：Markdown 渲染 + ECharts 交互图表 + Word/PPT/PDF 导出（md 图片 base64 内嵌自包含）；`/downloads` 下载中心支持类型筛选、搜索和增量加载

### 选股与因子研究
- **选股日批**：15:40 生成精选池、18:00 批量入链；硬约束过滤 + 排序 + LLM 候选 + 池分析；`/screening` 选股池页内置事件观察与市场温度计
- **因子供给线**：RD-Agent 提案 → LLM 转译 DSL → 五道闸准入（代码裁决，LLM 无写权）→ 因子库；`/factor-research` 监控采集器与看护状态，`/factors` 查看 candidate/active/IC 台账与风格暴露
- **市场温度计与事件观察**：regime 日批输出拥挤度/温度计；事件线采集、分诊、结算，重要公告与事件进入观察区
- **研究结论可交易**：ST/停牌/次新/一字板/流动性等硬约束在选股与执行两侧复用，避免「研究看好但无法下单」

### 数据与研究
- **多源数据面**：腾讯/东财/新浪/AKShare 行情与快照回退，东财不可用时自动切换；数据源健康监控与告警
- **财务与指标**：三表勾稽、偿债/盈利/运营/现金流、杜邦/GARP、行业阈值覆盖、技术指标（MA/MACD/RSI/BOLL/KDJ）与风控指标（回撤/波动率/Beta/VaR）
- **研报数据面**：研报、评级变动、盈利预测、分析师与投喂目录落库，供分析师引用和事后核验
- **同业与可转债**：点名多标的时注入 peer 财务/估值并生成报告同业对比段；可转债主表、行情、条款事件与转股状态覆盖

### 交易执行与跟踪
- **纸面执行**：决策落库观点池 → D+1 开盘 paper 撮合 → 订单/持仓/资金台账；`/broker` 执行台账页展示门禁、订单和持仓
- **结算与战绩**：止损/目标/超期规则结算；`/track-record` 提供净值曲线、五大指数区间对比、组合 β/α、观点日志状态 tab、策略族成绩单、预测详情与校准页
- **记忆层**：月度记忆提取 + `/memory` 确认/驳回 + 回灌下一次分析，让历史判断进入后续上下文

### 交互与治理
- **流式与会话**：SSE 渐进渲染（分层时间轴/思考摘要/工具调用横幅），终态事件优先送达 + SQLite 瞬断重试 + 看门狗兜底；会话新建/切换/搜索/重命名/删除，刷新或切换后断点续传
- **LLM 网关**：多 profile + ProfileResolver + 能力探测门禁 + fallback 链 + 输出合同守卫；`/settings` 提供 LLM/缓存/会话/评估运维面板
- **可观测与评估**：Langfuse 链路追踪、Prompt 版本管理和引用校验评分上报；judge 校准、版本对比、消融、claim 基准和决策回测（详见下节「评估体系」）
- **效率与外观**：浅色/深色/跟随系统三态主题、Cmd/Ctrl+K 命令面板、全局快捷键
- **日批运维**：`/ops` 展示当日各批运行记录与步骤级状态，支持失败一键补跑；26 个日批/月批任务统一调度

## 质量保障

- 后端 427 个 pytest 测试文件（6,442 个用例，含 `tests/llm_contracts/` provider 合同套件）、前端 100 个 Vitest 测试文件（888 个用例）、55 个 Playwright E2E spec；CI 四套配置合计 152 例（默认 91 + timeline 36 + track-record 20 + eval-ops 5，其中默认套件含 7 个 `@live` 用例，无真实模型凭证时跳过）
- `evals/` 评估框架（详见下节「评估体系」）：judge 评分与人工校准 / 版本对比 / 消融实验 / claim 验证基准 / golden set / 决策回放显著性检验

## 评估体系

为多 Agent 决策系统建立的四层评估体系，覆盖「校验器自身可信度 → 产出质量 → 决策回放 → 架构归因」：

### 1. 校验器自评估层（确定性命中的准度门禁）

- **对抗基准集**：`evals/claim_benchmark/data/benchmark_v12.jsonl`（81 条，构造即标签、免 LLM 标注）——含容差边界近失配（±{0.3,0.5,0.7,1}% 四档）、语义错位对抗子集、v3 新校验路径样本（取整感知容差 / comparative 双端）
- **CI 回归门禁**（`ci.yml`）：F1 ≥ 0.90 且相对冻结基线退步 > 0.02 即阻断合并；注入式故障演练验证（容差 0.5%→5% → F1 1.0→0.58、exit 1、还原全绿）
- **golden set v1.0**（13 条，已冻结）：assertion 级 deterministic 判定（拒答声明 / 合规红线 / 事故回归 claim 对照），零 token 进 CI（`evals/golden/gates.py`）
- 校准故事：61% 契约病误诊 → 分桶归因 → 修复四类契约 → 三标的 FAIL 3.5%/0%/0%

### 2. 产出质量层（LLM 实验回归）

- Langfuse 实验追踪 + `evals/compare.py` 配对 bootstrap（B=10,000）显著性门禁（CI 含 0 只写「无显著差异」）
- judge 评分体系（relevance/debate_quality/decision_grounding/consistency）+ 跨模型校准（含评分者漂移披露）；**人工校准闭环**——round5 未达标 → 材料/rubric 多轮修复 → round7 盲标 41 对全维度达标（MAE 0.342 / 方向一致率 97.6%）→ round8 维护者代裁审计无虚高（MAE 0.122 / 方向 100%），judge 判可用

### 3. 决策回测层（防前视偏差的历史回放）

- `evals/backtest/`：数据时点截断（财报按披露截止日）、分层市场状态抽样、与规则基线（Buy-and-Hold/MACD/KDJ/RSI）对比、block bootstrap 置信区间
- 基线按系统持有窗口切片（horizon 对齐）；空 K 线显式报错防静默污染

### 4. 架构归因层（数据对齐消融）

- `evals/ablation.py`：三变体（analysts / +辩论 / 完整五层）× 同 state 快照 × 配对 bootstrap；n=10 权威版 90 run（1,788 次调用 / 1,260 万 token）——辩论层与完整层的 judge 维度增量 95% CI 全含 0，即**在 3 标的、有效 n=3、judge 未校准的条件下未获统计支持**（见 [消融 n10 权威结果](docs/evals/2026-09-03-消融n10权威结果.md)；该报告的有效 n 与适用口径以其「适用口径披露」为准）
- **成本梯度（n10 实测，相对上一层）**：辩论层 +7.9%、决策+风控层 +30.1%；相对 analysts 整体 +40.3%（裁到 analysts 省 28.7%）
- **是否裁剪管线：未决**。「增量价值未获统计支持」与「无价值」是两个命题——有效 n=3 的分辨率不足以支持裁剪决策，需等 v2 消融（因果主张登记 + 注入法 + 单元级判定）裁决
- judge 维度按变体适用性过滤（避免评「不存在的层」）由 `_applicable_dims` 承担，**库侧已生效**；n10 批次（2026-09-03）跑在其落地之前，故该批次的 plus_debate 行含 grounding/consistency 伪影分（报告已标注，勿引用）

> **诚实边界**：消融/回测为通路验证级初步证据（3 标的 × 10 重复；层增量的配对单元是标的，有效 n=3）；pilot 的「完整层 decision_grounding 显著退步」在修复评估缺陷（#111/#112）后证实为评估伪影，其报告已标 `superseded`；n10 权威批次自身仍带两处未披露过的缺陷——#112 伪影存活（plus_debate 的 grounding/consistency）与 judge 未校准（跑批早于 round7 达标 10 天）——已在其报告中补披露；确定性指标（citation_pass/coverage/门禁）全程可信。基线说明见 [docs/evals/](docs/evals/)。

## 快速开始

### 方式一：Docker 一键部署（推荐）

**环境要求**：Docker Desktop

```bash
# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY（必填）

# 一键启动全部服务（前端 + 后端 + Langfuse 可观测性）
docker compose up -d --build
```

启动后访问：

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端 | http://localhost:5173 | React 应用 |
| API 文档 | http://localhost:8000/docs | FastAPI Swagger |
| Langfuse | http://localhost:3000 | LLM 调用追踪（首次需注册） |

```bash
# 常用命令
docker compose down              # 停止（保留数据）
docker compose down -v           # 停止 + 清空数据
docker compose logs -f backend   # 查看后端日志
docker compose ps                # 服务状态
```

### 方式二：前后端分离开发

**环境要求**：Python >= 3.12, uv, Node.js >= 18

```bash
# 后端（FastAPI，端口 8000）
uv sync
uv run uvicorn finance_agent.api:app --host 127.0.0.1 --port 8000 --reload

# 前端（Vite，端口 5173，另开终端）
cd frontend
npm install
npm run dev
```

## 环境变量配置

首次运行前需要配置 LLM API Key，否则会报错。

```bash
# 复制模板并填入你的 API Key
cp .env.example .env
```

### 变量一览

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_API_KEY` | **是** | - | LLM API Key；也可用 `DEEPSEEK_API_KEY` 作为回退 |
| `LLM_MODEL` | 否 | `openai/deepseek-v4.1-flash` | 深度模式模型名，litellm 格式 |
| `LLM_QUICK_MODEL` | 否 | `openai/deepseek-v4.1-flash` | 快速/追问模式模型名 |
| `LLM_BASE_URL` | **条件必填** | -（无代码默认，见 `.env.example` 模板值） | API 端点。**与 `LLM_API_KEY` 搭配使用时必填**——配了 key 缺端点启动即报 `IncompleteLLMConfigError`（有意收紧，见 5.1-B2）；只配 `LLM_MODEL` 不配 key 时按模型前缀走官方端点，可省略 |
| `LLM_THINKING` | 否 | `enabled` | 思考模式 `enabled` / `disabled` |
| `LLM_REASONING_EFFORT` | 否 | `max` | 思考强度 `low` / `high` / `max` |
| `TAVILY_API_KEY` | 否 | - | Tavily 搜索 API Key，快速模式 Web 搜索需要 |
| `REPORTS_DIR` | 否 | `reports` | 报告输出目录 |
| `EVENT_SOURCE` | 否 | `auto` | 事件数据源 `builtin` / `web` / `auto` |
| `LANGFUSE_PUBLIC_KEY` | 否 | - | Langfuse 公钥，配置后启用 LLM 调用追踪 |
| `LANGFUSE_SECRET_KEY` | 否 | - | Langfuse 密钥 |
| `LANGFUSE_HOST` | 否 | `https://cloud.langfuse.com` | Langfuse 服务地址（自托管填本地地址） |
| `LLM_DROP_PARAMS_STRICT` | 否 | 未设置 | 应急回滚开关（仅运维）：`=1` 恢复「全局静默丢弃不支持参数」的旧行为。**必须在首次 LLM 调用前设置**——该开关只在 adapter 首次初始化时读取（`ensure_litellm_runtime`），进程起来后再设不生效；默认行为是白名单显式剔除 + trace warning，关键参数不支持时显式报错 |

### 示例

**默认配置（opencode zen/go 网关 + deepseek-v4.1-flash）**：

```bash
# .env
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=openai/deepseek-v4.1-flash
LLM_QUICK_MODEL=openai/deepseek-v4.1-flash
LLM_BASE_URL=https://opencode.ai/zen/go/v1
```

**直连 DeepSeek**：

```bash
# .env
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=deepseek/deepseek-v4-pro
LLM_BASE_URL=https://api.deepseek.com
```

**使用其他 OpenAI 兼容服务（通过 litellm 格式）**：

```bash
# .env
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=openai/gpt-4o
LLM_BASE_URL=https://api.openai.com/v1
```

## 项目结构

```
src/finance_agent/
├── api.py                # FastAPI 应用 (SSE 流式接口 + 会话管理)
├── graph.py              # LangGraph 主图 (build_5layer_graph)
├── state.py              # AnalysisState TypedDict (含 Annotated reducers)
├── models.py             # 结构化输出模型 (AnalystReport/DebateMessage/TradeDecision)
├── routing.py            # 路由函数 + Send 并行派发
├── react_agent.py        # ReAct Agent (三模式编排入口)
├── agent_factory.py      # Agent 工厂 (按模式创建工具集 + system prompt)
├── pipeline_runner.py    # 5 层管线运行器 (独立线程 + 进度快照持久化)
├── session_store.py      # 会话持久化 (SQLite, WAL 模式)
├── stream_registry.py    # per-session 生成任务与订阅者管理
├── citation.py           # 确定性引用校验器 (Claim/CitationReport/verify_claims)
├── citation_coverage.py  # 正文数字普查 (citation recall 的确定性近似)
├── metric_vocab.py       # 指标词表 + 期次/数值归一化 (术语一致性校验)
├── app_search.py         # 股票搜索（模糊匹配）
├── web_search.py         # Tavily Web 搜索封装
├── charts.py             # 图表数据收集 + matplotlib PNG 生成
├── timeline_builder.py   # 流式时间线构建
├── langfuse_tracing.py   # Langfuse 追踪集成
├── llm/                  # LLM Provider Gateway（防腐层）
│   ├── resolver.py       # ProfileResolver：配置唯一解析入口 (请求级→preset→环境变量)
│   ├── router.py         # PolicyRouter：按用途能力过滤 + fallback 链
│   ├── probes.py         # 模型能力探测 (tool_call/json_output/stream 等，两级判定)
│   ├── contracts.py      # 结构化输出合同 (extract → validate → repair)
│   ├── output_guard.py   # 纯文本交付物输出合同守卫 (泄露独白/截断/空正文拦截)
│   ├── registry.py       # provider 静态能力表
│   └── adapters/litellm_adapter.py  # 唯一允许 import litellm 的位置
├── nodes/                # 图节点
│   ├── cache.py          # check_cache
│   ├── fetch.py          # fetch_data
│   ├── validate.py       # validate_financials (勾稽校验)
│   ├── compute.py        # compute_metrics (含技术指标 + 风控)
│   ├── analysts.py       # Layer I: 4 个分析师
│   ├── debate.py         # Layer II: bull/bear debater
│   ├── research_manager.py # Layer II: research_manager
│   ├── trader.py         # Layer III: trader
│   ├── risk.py           # Layer IV: 3 debaters + risk_judge
│   ├── fund_manager.py   # Layer V: fund_manager
│   ├── citation_node.py  # 引用校验节点 (分桶聚合 + 定向重试)
│   ├── report.py         # 5 层报告生成
│   ├── output.py         # generate_file (Word/PPT/PDF/Markdown)
│   ├── _llm_utils.py     # 共享 LLM 工具 (parse_json_response)
│   └── _timing.py        # 节点耗时追踪
├── harness/              # ReAct Agent Harness (工具循环引擎)
│   ├── loop.py           # Agent 主循环 (think -> act -> observe)
│   ├── llm_client.py     # LLM 客户端接口
│   ├── litellm_client.py # LiteLLM 实现 (经 gateway 收口)
│   ├── stub_llm_client.py # 测试用 stub
│   ├── tool_manager.py   # 工具注册与调度
│   ├── context.py        # Agent 上下文管理
│   ├── hooks.py          # 生命周期钩子 (流式事件推送)
│   ├── permissions.py    # 工具权限控制
│   └── types.py          # Harness 类型定义
├── metrics/              # 指标计算（纯函数）
│   ├── validate.py       # 勾稽校验 4 规则
│   ├── solvency.py       # 偿债 5 指标
│   ├── profitability.py  # 盈利 5 指标
│   ├── efficiency.py     # 运营 4 指标
│   ├── cashflow.py       # 现金流 6 指标
│   ├── dupont.py         # 杜邦 3 层分解
│   ├── traffic_light.py  # 红黄绿灯 + 健康度评分
│   ├── relative.py       # 相对估值
│   ├── garp.py           # GARP 筛选
│   ├── technical.py      # 技术指标 (MA/MACD/RSI/BOLL/KDJ)
│   └── risk.py           # 风控指标 (回撤/波动率/Beta/VaR)
├── data/                 # 数据层
│   ├── akshare_client.py # AKShare API 封装
│   └── cache.py          # SQLite 持久化 + 缓存
├── events/               # 关键事件获取 (L1 预设库 → L2 Web 搜索 → L3 兜底)
├── outcome/              # 决策结果跟踪 (决策落库 + 日批结算 + APScheduler 调度 + cohort 前向纸面批 + ops 运维接口)
│   └── track_record/     # 战绩仪表盘 (净值/跑赢指数对比/组合 β/α/观点日志/夏普 rf 中债1Y)
├── agui/                 # AG-UI 协议通道 (quick 模式对话，双轨隔离)
├── export/               # 报告导出 (docx/pptx/pdf/md 四格式，service.py 统一收口)
└── prompts/              # LLM prompt（Langfuse 托管，本地 .md 兜底，20 个模板）

frontend/src/
├── App.tsx               # 主应用 (会话/分析视图/设置面板)
├── chat/                 # 双通道对话 (深度分析 Thread + AG-UI quick Thread)
├── pages/downloads/      # 下载中心 (/downloads 独立路由)
├── stores/streamStore/   # 自建 SSE 流式状态管理 (断点续传 + 并发防护)
├── components/           # ReportSidePanel + shadcn 风格 UI 组件
├── PipelineTimeline.tsx  # 分层管线时间轴 (节点状态/耗时/ETA)
└── hooks/useHotkeys.ts   # 全局快捷键注册表
```

## 实施阶段

- [x] **P0 骨架** - 脚手架 + 空图 + 前端表单 + stub happy path
- [x] **P1 数据层** - AKShare + 20 指标 + 缓存
- [x] **P2 分析层** - prompt 工程 + 投资报告
- [x] **P3 输出层** - 综合分析 + Word/PPT 导出 + UI 打磨
- [x] **P4 5 层架构重构** - 多 Agent 辩论 + 交易决策 + 引用校验 + 技术指标/风控 (ADR-0011)
- [x] **P5 会话流式与自然输入** - SSE 流式 + 会话管理 + 自然语言输入 (ADR-0012)
- [x] **P6 快速模式 Web 搜索** - Tavily 集成 + 快速/追问模式 (ADR-0013)
- [x] **P7 Agent Harness 编排** - ReAct Agent 统一编排三模式 (ADR-0014)
- [x] **P8 Langfuse 可观测性** - LLM 追踪 + Prompt 管理 + 评分上报 (ADR-0015/0016)
- [x] **P9 意图澄清对话流** - 标的不明确时 Agent 反问澄清 (ADR-0017)

> P9 之后的演进以 OpenSpec delta 为单位管理（`openspec/changes/archive/` 已归档 222 个变更）。近期主题：LLM Provider Gateway 防腐层、Kimi 风格前端 UX（命令面板/下载中心/深色模式/报告侧栏）、引用校验语义覆盖强化（术语/期次一致性 + 分桶定向重试 + 期次键消歧）、决策结果跟踪与价位申报必填化（含误拦治理与打回反馈可执行化）、决策滞回与前向战绩仪表盘（跑赢指数对比/组合 β/α/观点日志状态 tab/观点池完整性）、LLM 治理续章（能力探测两级判定/kimi reasoning_effort 温度契约/纯文本输出合同守卫）、事件投递韧性五联及续章（运维出口/终态有界送达）、报告 K 线升级、评估体系 judge 人工校准与 rubric 判例迭代。

## 文档

- [docs 索引](docs/README.md) - docs/ 全量文档一张图（2026-09-08 新建）
- [PRD](docs/PRD.md) - 产品需求文档（初始设计稿，后续演进见 ADR）
- [架构设计](docs/architecture.md) - 系统架构详细设计
- [领域上下文](CONTEXT.md) - 术语表和分析框架定义
- [ADR](docs/adr/) - 架构决策记录（0001-0020 起，编号递增；agent 可新建/编辑，改结论须追加修订记录）
- [专题设计](docs/design/) - LLM Provider Gateway、E2E 方案、评估体系等专项设计档案
- [评估体系](evals/) - 评估框架（judge/对比/消融/claim 基准），基线说明见 [docs/evals/](docs/evals/)
- [项目工作流](docs/project-workflow.md) - OpenSpec + Superpowers 双框架实施指南
- [事故记录](docs/incidents/) - 系统性问题与解决方案（001-045，45 个编号 / 47 份事故文档，另含索引）
- [AGENTS.md](AGENTS.md) - Agent 工作指南（任务路由、契约红线、测试约束）
- [OpenSpec](openspec/specs/) - 系统行为规范（唯一真相来源，79 个 capability）

## 思路来源

本项目的多 Agent 架构和引用校验机制借鉴了以下研究与开源项目：

| 来源 | 用途 | 说明 |
|------|------|------|
| [TradingAgents (arXiv:2412.20138)](https://arxiv.org/abs/2412.20138) | 5 层架构 | 4 分析师并行 -> Bull/Bear 辩论 -> Trader -> Risk Management 辩论 -> Fund Manager 的整体流程参考 |
| [MarketSenseAI 2.0 (arXiv:2502.00415)](https://arxiv.org/abs/2502.00415) | 多源信号→综合 agent | 同团队单 LLM 版→agent 化的自然升级路径；组合级前瞻验证见其续作 [Signal or Noise (arXiv:2604.17327)](https://arxiv.org/abs/2604.17327)（无单一 agent 主导、各 agent 日期级 IC 均不显著，超额来自互补组合涌现，+25.2% 超额 / 蒙特卡洛 99.7 分位） |
| [Should we be going MAD? (arXiv:2311.17371)](https://arxiv.org/abs/2311.17371) | 辩论层选型的反面校准 | 标准多 agent 辩论不能可靠优于单 agent 自一致性采样、且 API 成本更高（全文已核）——本项目坚持对辩论层做两代消融实测的动机 |
| [Why Do Multi-Agent LLM Systems Fail? (arXiv:2503.13657)](https://arxiv.org/abs/2503.13657) | 多 agent 失效模式意识 | MAST 分类学：协调/规范失效主导——本项目结构化通信与图状态通道契约测试（incident 027/035 治理）的动机 |
| [FinGround (arXiv:2604.23588)](https://arxiv.org/abs/2604.23588) | 引用校验 | Claim 6 类分类法 + computational 公式重算机制（见 [citation.py](src/finance_agent/citation.py)） |
| [LangChain qa_sources](https://python.langchain.com/docs/how_to/qa_sources/) | 引用结构 | 结构化 Citation 对象设计参考 |

> 数据层（AKShare）、指标计算（四维度 + 杜邦 + 技术指标 + 风控指标）和报告结构为自主设计，详见 [CONTEXT.md](CONTEXT.md) 和 [ADR](docs/adr/)。
>
> **架构选型的证据现状**（完整调研见 [docs/evals/2026-10-05-多Agent对单Agent选型调研.md](docs/evals/2026-10-05-多Agent对单Agent选型调研.md)）：学界对「多 agent 是否优于单 agent」并无定论——值得注意的是 TradingAgents 原文并未设单 LLM 基线也未做消融（已核正文），其证明的是多 agent 优于规则策略。证据最强的两个子命题是**信息源分解**（多窗口并行深挖，本项目四分析师）与**专职否决层**（本项目风控辩论+基金经理，自有消融 v2 结论级盲评 19:1 支持）；对抗辩论层本身的 judge 维度增量在本仓消融 v1 中未获统计支持（有效 n=3，如实报告）——这正是本项目不照搬架构、而是持续用两代消融实测其核心假设的原因。

## License

MIT
