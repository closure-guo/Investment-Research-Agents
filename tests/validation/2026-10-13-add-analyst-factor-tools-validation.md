# 人工验证报告: add-analyst-factor-tools

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-analyst-factor-tools/`（specs：`analyst-data-sources` / `causal-ablation`）
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–3（只读工具 → 注册与追踪 → 消融变体 + 最小实跑烟测）
**实现提交**: `a56374e9`(T1) / `254bc5f1`(T2) / `529a6b92`(T3)；本报告为 docs 收口（零 src 改动）

> **前置事实校正（重要，影响 A2 读法）**：计划 A2 声称存在「技术分析师 ReAct 工具集」并
> 「复用既有工具追踪机制」。实际代码中四分析师（`nodes/analysts.py`）是**单次 LLM 节点**，无工具
> 循环；仓库唯一的工具执行机制是外层 ReAct 聊天 Agent 的 harness `ToolManager`
> （`agent_factory.build_agent`：quick/deep/follow-up）。分析师层既有的「工具」先例是
> **toolize / 上下文注入**（`calc_derived_series`、`calc_price_levels` → state → 分析师 context）。
> 因此 T2 按既有先例接线：① `_trace_tool` 承担 trace/耗时/用途标签；② 工具输出由技术分析师节点
> 注入自身上下文。**未新造执行机制**——节点级 LLM function-calling 循环属新执行机制，且本仓库
> ADR 为人工维护（AGENTS.md：agent 不得新建）。**该偏差为本轮显式决策，交人工终审。**

## 0. 命令证据（当场跑、逐字记录）

### 0.1 计划指定测试命令与相关子集

```
$ uv run pytest tests/factors tests/evals -q
1 failed, 1578 passed, 5 skipped, 6 warnings in 155.65s
（唯一失败：tests/evals/test_fm_decision_live.py::test_fm_decision_live_report —— @live 用例，
 归因见 §2「宿主 .env 泄漏」，与本 change 无关）

$ uv run pytest tests/evals -q
1 failed, 1298 passed, 5 skipped in 77.16s        # 仅 tests/evals 目录内独立复现同一失败

$ uv run pytest tests/evals/test_fm_decision_live.py -q
1 skipped in 0.03s                                 # 干净进程（无 LANGFUSE_*）→ @live 跳过

$ uv run pytest tests/factors tests/evals -q -m "not live"
1574 passed, 4 skipped, 6 deselected in 50.89s      # 门禁口径（排除 @live）全绿

$ uv run pytest tests/ -q -m "not live"
1 failed, 5392 passed, 6 skipped, 12 deselected, 62 warnings in 983.23s (0:16:23)
（唯一失败：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
 ——计划预告的既有 llm env flake；隔离复跑 1 passed in 0.08s；宿主 .env 泄漏 LLM_MAX_CONTEXT 所致，
 与本 change 无关：本 change 三个提交合计仅 8 文件，src/finance_agent/llm/ 零改动）

$ uv run pytest tests/factors/test_factor_tools.py -q
19 passed in 1.33s
$ uv run pytest tests/factors -q
280 passed in 21.98s
$ uv run pytest tests/nodes/test_analyst_factor_tool.py tests/test_agent_factory.py \
    tests/nodes/test_analysts.py tests/nodes/test_trace_tool.py -q
80 passed in 10.11s
$ uv run pytest tests/evals/test_ablation_factor_tool.py tests/evals/test_ablation.py -q
73 passed in 4.62s
```

新增用例 **49 例**（T1 19 + T2 16 + T3 14）；既有测试**零修改**——diff 证据：
`tests/test_agent_factory.py` 为 165 增 / 0 删（纯追加，`git diff 254bc5f1~1 254bc5f1` 可证），
其余测试文件均为新增（T1 369+0、T2 147+0、T3 200+0）。

### 0.2 Lint 与类型检查

```
$ uv run ruff check src/finance_agent/factors/tools.py src/finance_agent/agent_factory.py \
    src/finance_agent/nodes/analysts.py evals/ablation.py tests/factors/test_factor_tools.py \
    tests/nodes/test_analyst_factor_tool.py tests/evals/test_ablation_factor_tool.py \
    tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.py
All checks passed!
$ uv run ruff format --check <同一批文件>
8 files already formatted

$ uv run mypy                      # 全 src（本 change 工作区 HEAD 529a6b92）
Found 78 errors in 20 files (checked 161 source files)
$ uv run mypy                      # 基线（本 change 的 src 改动 git stash 后）
Found 83 errors in 21 files (checked 161 source files)
（逐条 diff：唯一同点错误 `agent_factory.py ... Name "wrapped" already defined` 为既有
 no-redef（仅行号位移）；其余为 graph.py NodeInputT 一类既有投诉——本 change **不新增 mypy
 错误**，错误数 -5 系工厂构造节点在 add_node 处不再触发该既有类型投诉）

$ uv run mypy evals/ablation.py
Found 5 errors in 2 files（evals/extract.py、evals/judges.py —— pyproject 注记的 evals 存量
 债务；ablation.py 自身 0 错误）
```

### 0.3 最小实跑烟测（计划 A4 / T3 Step 5）

凭据现状（实测）：项目标准 `LLM_API_KEY` / `.env` 未配置；宿主环境里 `DEEPSEEK_API_KEY`、
`OPENCODE_API_KEY_DEEPSEEK_V4_FLASH` 两把密钥对各自端点实测 **401 无效**；`ZHIPU_API_KEY`
对 `https://open.bigmodel.cn/api/paas/v4` 实测有效（OpenAI 兼容）。故烟测以 **env 显式注入**
运行（未改任何配置文件）：

```
$ LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4 LLM_API_KEY=$ZHIPU_API_KEY \
  LLM_MODEL=openai/glm-4.6 JUDGE_MODEL=openai/glm-4.6 \
  uv run python -m evals.ablation --tickers 600519 --repeats 1 --judge-repeats 1 --suite factor-tool
… 数据准备完成（AKShare 部分信源降级：行业 PE / 解禁 / 大宗为空，管线照常）
… 10 次 LLM 调用成功
finance_agent.llm.errors.RateLimitError: 限流：litellm.RateLimitError: RateLimitError:
  OpenAIException - 您的账户已达到速率限制，请您控制请求频率
During task with name 'fundamental_analyst' …
```

**烟测结论（如实记录）：未完成。** 被供应商限流中断（第一臂 `fundamental_analyst`），无配对结果
artifact；失败点在供应商侧（并发分析师请求触发 glm-4.6 账户限流），非本 change 代码缺陷——
同链路单调用探针（下）在同一凭据下跑通。**未伪造任何批次结果**；n=10 批次按 A4 不跑（§3）。

随后以**单调用探针**替代验证本 change 新链路端到端（真实 LLM，每次 1 次调用；
`tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.py`）。四次 live 调用如实并列
（**不挑选有利读数**）：

| 次 | 因子库段 value/z/IC | LLM 产出 | citation 桶 | 说明 |
|---|---|---|---|---|
| 1 | True/True/True | 解析成功，16 claims | 16×PASS | 首次成功；artifact 因脚本 blocked 分支覆盖缺陷（**已修**：blocked 落兄弟文件）丢失，仅有命令输出留痕 |
| 2 | True/True/True | **解析降级**（非 JSON 输出） | —（0 claim） | 端点/模型输出契约方差（glm-4.6 经中转，非项目生产端点） |
| 3 | True/True/True | 解析成功，18 claims | 17×PASS + **1×FAIL** | 脚本当时未记逐条明细（已补 `claims_detail`），无法归因该条 |
| 4 | True/True/True | 解析成功，15 claims | **15×PASS** | 最终 artifact（含逐条 status/bucket/field_ref）：0 条 FAIL、0 条因子相关 claims |

最终 artifact 逐字（第 4 次）：

```
$ LLM_BASE_URL=… LLM_API_KEY=… LLM_MODEL=openai/glm-4.6 \
  uv run python tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.py
[verdict] ok
因子库段含 value/zscore/IC: True/True/True
claims=15 citation_buckets={'PASS': 15}
因子相关 claims: 0
artifact: tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.json
```

注入技术分析师上下文的因子库段（artifact 逐字；合成面板 + 库内 active 动量因子，真实 LLM 上下文）：

```
因子库（工具 query_factor_library 输出：active 因子当前取值 / 全市场截面 z-score / 近 60 日 IC；缺失值记 null，直接引用不臆测）:
{"symbol": "600519", "as_of": "2026-10-13", "factors": [{"factor_id": 1, "expression": "delta(close,20)",
 "category": "动量", "value": 60.0, "zscore": 1.1094003924504583, "ic_mean_60d": 0.12, "icir_60d": 4.0}]}
```

**探针结论**：4/4 次「工具 → 因子库段（值/z-score/IC）→ 上下文注入」均正常（新链路可复现地工作）；
LLM 产出与 claim 结果存在**模型/端点侧方差**（解析降级 1/4、单条 FAIL 1/4，均为替换端点的既有
方差性质）——单次 live 读数不足以支撑任何结论，这正是 n=10 校准批次（§3 后续项）存在的原因。
四次调用中**均未**出现因子库数值被写成 claim（0 条因子相关 claims）——见 §2 的 citation 注册缺口。

## 1. 交付内容与口径确认

### 1.1 T1 只读工具 `src/finance_agent/factors/tools.py`

- 输出 JSON 键逐字：`{"symbol", "as_of", "factors": [{"factor_id", "expression", "category",
  "value", "zscore", "ic_mean_60d", "icir_60d"}]}`（`json.dumps(ensure_ascii=False,
  allow_nan=False)`——非有限值一律 `null`，严格 JSON）。
- 口径：`factors` 缺省 `list_active_factors`（只取 active）；逐因子 `evaluate(parse(expression,
  enforce_complexity=False))` 取 `as_of` 行该标的列值；z-score = 该日**全市场截面**
  `cross_section_zscore`（ddof=1，与 L2 ranker 同口径）；IC 缺省 `recent_ic_stats(window=60)`。
- 只读保证：AST 静态扫描钉死（无 `add_factor`/`set_status`/`set_pool_increment`/`record_ic`/
  `apply_bucket_quota`/`init_library` 调用，无 `write_*`/`update_*`/`record_*` 类调用，无写函数
  import，无 `sqlite3` import）；工具**不接受表达式入参**（不暴露表达式求值能力）。
- **取舍（一处计划内部冲突）**：plan Task 1 Step 1 写「面板缺日期 → 明确错误」，而 A1（标注
  「实施者不得自行改口径」）写「面板缺 symbol 列/日期 → value/zscore 为 null（不编造）」。
  本轮按 **A1** 实现（null），并以 `note` 显式写明缺失日期与面板可用范围（不静默、不抛错）
  ——同时满足「不编造」与「明确错误可观测」。**该取舍交人工终审。**

### 1.2 T2 注册与追踪（`agent_factory.py` + `nodes/analysts.py`）

- 工具集权威表 `ANALYST_TOOLSETS`：`technical → ("query_factor_library",)`，
  `macro/fundamental/sentiment → ()`（非目标：其他三分析师零改动）；用途标签逐字
  `PURPOSE_TAG_ANALYST_FACTOR_TOOL = "analyst_factor_tool"`。
- 追踪（复用既有机制）：`_trace_tool(name, metadata=…)` 落 span `tool_call:query_factor_library`
  （入参/输出/异常）+ `purpose_tag` + `duration_ms`；`metadata` 为**新增可选参数**（既有调用、
  既有 span 契约测试不受影响）。工具调用发生在分析师节点内，故其耗时天然计入节点耗时追踪。
- 薄壳数据源（A1 只定义入参、未定生产来源，本轮落定）：市场快照库（既有 `read_universe` +
  `build_panel_from_rows`，经 `factors/monitor` 的最近 N 交易日装配，lookback=60）+
  因子库；`as_of` = 快照库最新数据日期。**只读纪律**：库文件不存在即按空库处理，不建库、
  不写行（单测断言不创建任何库文件）。
- 消费方：`make_technical_analyst(with_factor_tool=…)`；生产入口 `technical_analyst` = 带工具
  （spec「技术分析师 SHALL 可通过 query_factor_library 查询」）；工具故障 → 上下文如实标注
  「不可用」，不阻断分析。

### 1.3 T3 消融变体与报告口径（`evals/ablation.py`）

- `_TECH_VARIANTS = ("tech_bare", "tech_factor_tool")`，与分层 `_VARIANTS` **分离**（既有跑批/
  报告语义零变化；"既有测试零修改" 红线）；两臂唯一差异 = 技术分析师节点工具绑定
  （`_TECH_VARIANT_FACTOR_TOOL`，经 `make_technical_analyst` 构造）；共享同一 state 快照。
- judge 维度按变体适用性过滤：无辩论/决策层 → 只评 `report_relevance`（同 `analysts` 口径）。
- 报告口径（spec causal-ablation 场景）：CI 含 0 → 逐字「无显著差异」；
  `render_factor_tool_ablation_report` **结构守卫**（出现「无效/无价值/无用/没有价值」→ 渲染
  `ValueError`，fail loud，坏措辞不进报告）；聚合复用既有 `paired_bootstrap_ci`
  （逐标的中位配对 + mean-diff CI，与 `aggregate_results` 同源）。
- 跑批入口 `run_factor_tool_ablation` 复用既有快照机制与库侧唯一判分入口 `score_run`；
  CLI 新增 `--suite factor-tool`（默认 `layers` 不变）。

## 2. 边界与归因（诚实披露）

- **计划前提偏差**（见文首校正）：T2 未采用「LLM function calling 工具循环」。若 owner 期望
  字面意义的「LLM 主动调用工具」，那属新执行机制且需 ADR —— 请另行裁决（§3 后续项 2）。
- **烟测中断归因**：`RateLimitError` 来自供应商账户（glm-4.6，同进程 10 次大 prompt 调用后
  触发），非代码缺陷；本机另两把密钥（DeepSeek / Zen 中转）实测 401 无效，项目标准
  `LLM_API_KEY`/`.env` 未配置，故以 env 显式注入运行且**未改任何配置**。
- **@live 失败归因（既有 env 泄漏）**：`evals/run.py:371` 的 `load_dotenv()`（无路径 → 向上查找）
  在 pytest 会话内命中**宿主仓库** `D:\WorkSpace\finance_analysis_agent\.env`（本 worktree 无
  `.env`），把 `LANGFUSE_*`/`LLM_*` 泄进进程 → `@live` 用例不再跳过而失败（fm_decision 需真实
  Langfuse trace）。证据：单跑该用例 `1 skipped`；`tests/evals` 目录内跑 `1 failed`（同点）；
  `-m "not live"` 全绿；**排除本 change 新增测试文件后**（`--ignore=tests/evals/test_ablation_factor_tool.py`）
  依旧 `1 failed, 1284 passed`——归因与本 change 无关；本 change 不触 `llm/`、`langfuse_tracing.py`、
  `evals/run.py`。与上一轮 `add-screening-ranker` 验证报告 §2 记录的「宿主 .env 泄漏 LLM_MAX_CONTEXT」
  同类同源。
- **live 证据强度**：单调用探针证明「工具 → 因子库段（值/z-score/IC）→ 真实 LLM 报告（16/16
  claims PASS）」端到端；**未**证明 1 对消融跑批端到端（被限流中断），也**不构成任何增量价值
  判断**（n=1、judge 未校准）。
- **citation 注册缺口（既有链路，本轮未扩）**：因子库数值目前仅进上下文，未注册 state 根 /
  `_COMPUTATIONAL_RECALC` → 若 LLM 把它写成 claim，可能判 FAIL（本轮 0 条因子相关 claims，
  未触发）。列为后续项 3。

## 3. 后续项（显式边界，不属本次交付）

1. **n=10 消融批次**（计划 A4）：需真实 LLM + judge，且评估 SOP 要求 judge 校准先行、一次只动
   一个变量 → 本轮不跑；产出报告落 `docs/evals/` 并登记 `metrics.md` 时间线 + `runs.jsonl`
   —— 已挂 **issue #326**（`needs-triage`，含完成路径与本轮烟测中断事实）。
2. **真 ReAct 工具调用**（若 owner 期望字面「LLM 调用工具」）→ 需人工落 ADR 后接线
   （gateway tools 通道已具备，缺的是分析师节点的工具循环与降级协议）。
3. **因子库数值 citations 注册**（若要允许其进 claim）：state 根 + `_COMPUTATIONAL_RECALC`
   注册 + prompt 前缀约定。
4. **烟测重跑**（限流额度充足或换端点时）：同 CLI 一行即可复跑（§4），产出即含 CI 含 0 →
   「无显著差异」的渲染报告。

## 4. 复现方法

```bash
uv run pytest tests/factors tests/evals -q -m "not live"   # 门禁口径（全绿）
uv run pytest tests/ -q -m "not live"                      # 全量回归
uv run ruff check <changed files> && uv run ruff format --check <changed files>
uv run mypy                                                # 与基线对比须无新增错误
# 单调用 live 探针（需 LLM_* env；缺凭据 → 落 `-blocked` 兄弟 artifact，退出码 0，不覆盖 live 证据）
uv run python tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.py
# 1 对消融烟测（需 LLM_* + JUDGE_* env；本轮被供应商限流中断）
uv run python -m evals.ablation --tickers 600519 --repeats 1 --judge-repeats 1 --suite factor-tool
```

artifact：`tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.json`（探针逐字证据：
因子库段、live 报告摘要/markdown 摘要、citation 桶计数）。
