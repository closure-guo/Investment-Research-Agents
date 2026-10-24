# Proposal: add-holdings-context

## Why

A 股散户几乎只能做多：融券做空受两融标的范围、券源与信用账户门槛限制，多数标的实际不可执行。但决策层的输出空间里，`short`（做空建仓）与 `exit`（持有者减仓）共用同一个 `action=sell` 载体，且系统不掌握「受众是否持有该标的」——分型只能靠 LLM 自述，未申报时的默认值又偏向不可执行的 `short`。

实跑证据（生产库 `data/sessions.db`，2026-08-04 至 10-09，150 条观点）：

- 方向分布 long 13 / short 13 / neutral 124。13 条 short 全部来自 `action=sell`，逐条读裁决词**无一条为纯「做空建仓」**——用的全是「分批减仓」「定位为降低敞口而非博取空头收益」「现价减仓」「轻量减仓而非清仓追空」等持有者减仓话术，并出现「底仓」「重建仓」等假定受众持仓的表述。这 13 条在结算上 9 条 superseded、2 条 unresolvable、2 条 duplicate_of_day，**做空桶零真实样本**。
- 口径打架：报告的正文结论是「降低敞口」，申报价位却是空头建仓结构（2026-09-25 光大银行 entry 3 / stop 3.1 / target 2.9——止损在入场上方），战绩层再按 `sell→short` 映射展示为「看空」。辩论层自己已把该问题喊出，该报告激进方原话：「要么大幅下调目标价以拉开赔率，**要么明确这只是退出而非做空**」。
- 默认值是反的：`risk_judge` 对未申报 `sell_type` 的 sell 默认按 `short` 处理（`src/finance_agent/nodes/risk.py:316`），与生产实际 13/13 的减仓语义相反。这是全系统唯一一处硬编码把受众推向不可执行动作的地方。

同时，后续要接入自动选股与交易，需要一个承载真实持仓状态的接入点。现在补上，`sell_type` 分型判定才能从「LLM 自述」升级为「状态决定」，受众约束也才有确定性载体。

## What Changes

1. **新增 `holdings-context` 能力（预留框架）**：分析请求可携带受众持仓声明，解析为 `state["holdings_context"]`（三态：已申报持有 / 已申报未持有 / 未申报）并注入决策层（Trader、Risk Judge）context。本次落的是**管道**——模型、状态键、解析、注入、消费契约；真实账户接入与持久化留给后续 delta。
2. **决策层受众可执行性约束（prompt 政策）**：`trader` / `risk_judge` 提示词增加受众约束段——A 股散户融券受限，`short` 仅在受众明确要求做空研究时使用；否则看空表达按持仓三态落到 `exit`（已持有）或 `watch`（未持有）；结论必须落到可执行动作。
3. **未申报 `sell_type` 默认值 short → exit**（可执行默认）：`risk_judge` 的打回后默认与滞回「维持前判」路径同步改；配套把价位完整性打回措辞按分型分支——`exit` 缺 `exit_schedule` 时要求申报减仓节奏，而不是要求建仓价位。
4. **未持有受众的 exit 门禁**：受众已申报未持有时，`sell_type=exit`（无敞口可退）为语义矛盾，打回重申一次；仍犯则降级 `watch` 并如实标注落 state（复用既有滞回归一模式）。
5. **做空披露注**：报告「交易决策」节对按做空建仓模板呈现的 sell（`sell_type="short"` 与历史未申报形态）渲染披露行，说明做空需两融信用账户、A 股普通账户不可执行，本条为方向研判。

## Capabilities

### New Capabilities

- `holdings-context`：受众持仓上下文——声明入口、三态解析、决策层注入、未持有受众的 exit 门禁

### Modified Capabilities

- `agent-prompt-contracts`：MODIFIED「Trader 决策语义契约」（未申报 sell_type 的默认由 short 改 exit 并同步申报要求措辞）；ADDED「决策层受众可执行性约束」（受众约束段 + 持仓三态下的方向表达纪律）
- `report-decision-rendering`：MODIFIED「交易决策节渲染操作参数」（做空披露行；未申报 sell_type 的默认语义由 short 改 exit，历史形态渲染口径不变并明确为历史兼容路径）

## Impact

- **代码**：`models.py`（`HoldingsContext`）、`state.py`（`holdings_context` 声明）、`api.py`（`AnalyzeRequest.holdings` 可选声明 → `initial_state`）、决策层 context 注入（`nodes/trader.py`、`nodes/risk.py`）、`nodes/risk.py`（默认值 + 打回措辞分支 + 未持有门禁）、`nodes/report.py`（披露行）
- **prompts**：`trader.md`、`risk_judge.md`（受众约束段）——改动后须执行 `uv run python scripts/deploy_prompts.py` 发布（prompt-deploy-consistency 门禁）
- **前端**：无改动（`holdings` 为可选请求字段，未申报即现行行为，不触发 E2E 门禁）
- **测试**：模型 / 解析 / 注入 / 默认值 / 门禁 / 渲染 / 提示词契约测试；结算方向映射不变（零回归）
- **非交互类变更**：不涉及前端 UI、SSE 流式、会话切换、状态流转，不适用 E2E 门禁
- **明确不做（后续 delta）**：持仓持久化与真实账户接入（自动选股 / 交易本体）、前端持仓录入 UI、`sell→short` 结算方向映射变更（「跌了获益」对 exit 与 short 同样成立，维持现状）、两融标的名单数据源

## 与其他 change 的关系

- `add-horizon-semantics-to-decision-layer`（已在 `origin/main`，PR #321/#324）：同样改 `agent-prompt-contracts` 与 `trader.md`/`risk_judge.md`，但落在**不同 requirement 与不同章节**——该 change 为 ADDED「决策层周期语义契约」/「分析师论据周期标注」并在两份 prompt 顶部插入 `## 周期契约` 段；本 change 为 MODIFIED「Trader 决策语义契约」+ ADDED「决策层受众可执行性约束」并在文末追加 `## 受众可执行性约束` 段。本 change 已 rebase 到 `a3e2eb97`（含该 PR），两段在 `trader.md` / `risk_judge.md` 中并存，发布后指纹回验确认未覆盖周期契约段。
  教训记录：本 change 的初始分支基于**陈旧本地 main**（`f5b54fe3`，落后 `origin/main` 40 个提交），而该 change 的 prompt 已先于本地 main 部署到 Langfuse production——`deploy_prompts.py` 预检因此对本 change 判定 `mismatched`（生产内容不命中分支历史）。经指纹取证（生产内容 vs 本地 vs HEAD vs git 历史）定位为「本地 main 陈旧」而非 UI 编辑后，rebase 到 `origin/main` 使生产内容命中 `#321` 历史版本，预检改判 `stale` 放行。**未**使用 `sync_prompts.py` 收编。
- `add-watch-trigger-tracking`：同改 `report-decision-rendering` 的**不同 requirement**（该 change 为 ADDED「watch 触发位与入池跟踪渲染」；本 change 为 MODIFIED「交易决策节渲染操作参数」）。该 requirement 已随其同步进入 `origin/main` 主规范库。
