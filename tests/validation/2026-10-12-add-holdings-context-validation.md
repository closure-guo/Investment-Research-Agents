# 验证报告: add-holdings-context

**日期**: 2026-10-12
**验证人**: agent（自动化验证 + 真实 LLM 冒烟）；**owner 签字待补**
**分支**: `add-holdings-context`（worktree `.worktrees/add-holdings-context`），10 commit，基于 `origin/main` `a3e2eb97`
**关联 delta**: `openspec/changes/add-holdings-context/`
**E2E 门禁**: 不适用（非交互类变更——不涉及前端 UI、SSE 流式、会话切换、状态流转）

---

## 1. 变更范围

受众持仓上下文（三态：已申报持有 / 已申报未持有 / 未申报）+「决策落到可执行动作」三收口：

| # | 改动 | 落点 |
|---|---|---|
| 1 | `HoldingsContext` 三态模型 + `state["holdings_context"]` + `AnalyzeRequest.holdings` | `models.py` / `state.py` / `api.py` |
| 2 | 决策层持仓行注入（Trader + Risk Judge，三态均注入不省略） | `nodes/_llm_utils.py` / `nodes/trader.py` / `nodes/risk.py` |
| 3 | prompt 受众可执行性约束段（A 股普通账户不可做空 + 三态方向表达纪律） | `prompts/trader.md` / `prompts/risk_judge.md` |
| 4 | 未申报 `sell_type` 默认 `short` → `exit`（可执行默认）+ 打回措辞按分型分支 | `nodes/risk.py` / `nodes/validate.py` |
| 5 | 未持有受众的 exit 可执行性门禁（打回恰一次 → 仍犯降级 watch） | `nodes/risk.py` |
| 6 | 报告做空披露行（按做空模板呈现的 sell：`short` 与历史 `None`） | `nodes/report.py` |

---

## 2. 自动化验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `uv run pytest` | **4645 passed / 4 failed / 7 skipped**（907s） |
| 基线对照 | 干净 `origin/main`（`a3e2eb97`，独立 worktree，同一 `.env`）跑同一命令 | **4599 passed / 4 failed** —— **失败集完全相同** |
| 增量 | — | 本 change 新增 **46 个通过用例**（4645−4599），**零回归** |
| Lint | `uv run ruff check` | All checks passed |
| 类型 | `uv run mypy` | 83 errors —— 基线实测同为 **83 errors**，零新增（既有问题，CI 该步非门禁） |
| Delta | `openspec validate add-holdings-context --strict` | valid |
| prompt 发布 | `uv run python scripts/deploy_prompts.py`（仅 `trader`/`risk_judge`） | 导入 2 / 跳过 12 / 失败 0 |
| 发布回验 | production 内容 == 本地逐字比对 | True；且**同时含** `## 周期契约`（上游 #321，未被覆盖）与 `## 受众可执行性约束`；全文无残留「默认按 short」 |
| eval 门禁 | `evals.run._verify_prompt_sync(client)` | **空列表**（14 个 prompt 全部一致） |

**4 个失败的归因**：`tests/evals/test_fm_decision_live.py`、`tests/llm_contracts/...::test_ark_tool_call_contract`、`tests/test_trace_content_live.py` 三者为 `@live`（真实 LLM/provider，ark 订阅过期属既有）；`tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default` 单跑通过（顺序相关 flake）。四者在**干净 `origin/main` 上同样失败**，与本 change 无关。

---

## 3. 真实 LLM 冒烟（本次变更的核心行为由真模型验证）

直接调用改动过的 `risk_judge` 代码路径（真实 Kimi `openai/k3-256k` + 已发布的 Langfuse production prompt），构造看空 setup，逐态观察：

### 用例 A：受众已申报未持有（`holds=False`）

- **终稿**：`action=watch`、`sell_type=None`、价位全清空
- **门禁状态**：`holdings_executability_check = {result: pass, note: ""}` —— **门禁未触发**（note 为空），说明**模型首轮就自行选了 watch**，不是被门禁拦回
- **模型理由原文**：「受众未持有该标的，无仓位可减；看空但不具备融券做空的受众条件，且当前赔率 1.20:1、止损距离 5.0% 在 0.29 年化波动率下风险收益不佳，不构成追空或反向建仓的理由，故观望等待方向确认。」并明写「按可执行性约束 MUST NOT 输出 sell_type=exit，看空结论落地为观望（watch）」
- **判定**：受众约束在真模型上**生效**（prompt 政策 + context 行被真实遵守）

### 用例 B：受众持仓未申报（`holds=None`，生产默认态）

- **终稿**：`action=sell`、`sell_type=exit`、`exit_schedule` 已申报、无建仓价位
- **渲染**：方向 / 置信度 / 仓位 /「减仓节奏」/ 再评估触发条件 / 理由；**无价位行、无做空披露行**（exit 走对分支）
- **模型理由尾句**：「若受众实际未持有，则本 sell/exit 不适用，不应追卖或默认 short，应转为观望并等待再评估触发条件。」`exit_schedule` 原文亦同时覆盖两种受众：「已持有者先减仓至轻敞口…未持有者不执行卖出，也不以 short 表达看空。」
- **判定**：可执行默认 `exit` 与「未申报需说明未持有处置」在真模型上**生效**

### 用例 C：未持有 + 受众明确要求做空研究（逃生出口）

- **终稿**：`action=sell`、`sell_type=short`、entry 100 / stop 105 / target 93
- **渲染**：价位行 + 派生指标 + **做空披露行**（`做空披露: 做空建仓需融资融券信用账户（标的须为两融标的且有券源），A 股普通证券账户不可执行；本条为方向研判，不代表可直接执行`）
- **模型理由原文**：「受众明确要求做空研究且标的可融券，故看空结论可按 short 表达（非默认看空表达，而是响应明确需求）…**执行前提披露**：做空需融资融券信用账户且券源实际可得，若券源不可得或账户不支持，本决策不可执行，应转为观望。」
- **判定**：`short` 逃生出口可达且未被误伤；披露行端到端渲染正确；模型主动复述执行前提

**三例共同结论**：本变更的三条行为主线（受众约束生效、可执行默认、披露与逃生出口）在**真实模型 + 已发布 prompt** 下全部按规格表现，且未出现 `short` 被当作默认看空表达的情形。

---

## 4. 未由本次验证覆盖的部分

- **披露行的真模型触发**：三例中无一产出 `short`/`None` 形态以外的路径（正是设计目标），故披露行的**真模型**出现路径未直接观测；披露行本身由 `tests/nodes/test_report_decision_render.py` 4 条用例（short / 历史 None / exit 不渲染 / buy 不渲染）+ 常量与规格字面量全文相等断言覆盖。
- **完整管线端到端**：本次为决策层直调冒烟，未跑 `POST /api/analyze` 全链路（需拉起后端容器）。持仓声明的请求入口接线由 `tests/nodes/test_holdings_context.py::TestRequestHoldingsIntake` 三态参数化覆盖（含变异验证可红），ReAct 追问路径由 `TestReactHoldingsPropagation` 覆盖。
- **持久化**：本 change 明确不含持仓持久化与真实账户接入（design.md §1 边界）；`holdings` 为请求级声明。

---

## 5. 审查记录

| 轮次 | 范围 | 裁决 | 修复 |
|---|---|---|---|
| 任务审查 1 | 持仓管道底层（T1+T2） | Spec ❌ / Needs fixes（3 Important + 4 Minor） | `f35bcea4` 全修（请求接入三态测试含变异验证 / 注入点未申报态表驱动 / ReAct 路径打通 / 注释与边界） |
| 复审 1 | 同上 | **Spec ✅ / Approved** | — |
| 任务审查 2 | 决策可执行性收口（T3–T6） | Spec ✅ / Needs fixes（1 Important + 6 Minor） | `80c3362a`（终稿复核降级后价位注换代 + prompt 与断言加固，变异验证 6/6 KILLED） |
| 全分支终审 | 全部 10 commit | **ready to merge，无 BLOCKER / 无 MAJOR** | 6 条 Minor 全部裁为可留（follow-up 级） |

**终审遗留（follow-up 级，不阻断）**：`_downgrade_exit_to_watch` 未清 `position_size`（同文件滞回降级清空，风格不一致）；两份 prompt 未申报 bullet 的「说明未持有处置」半句无断言守护（删除仍绿）；`validate.py:_validate_price_levels` 对 exit 形态仍要求建仓价位（既有接缝，exit 成为默认后触发概率上升，最多一次 Layer III 打回、无用户可见错误）。

---

## 6. 收口过程的重要发现

本地 `main` 落后 `origin/main` **40 个提交**，而 `add-horizon-semantics-to-decision-layer`（PR #321/#324）的 prompt 改动**已先于本地 main 部署到 Langfuse production**。本次 `deploy_prompts.py` 预检因此判定 `mismatched`（生产内容不命中分支历史）。经**指纹取证**（生产内容 vs 本地 vs HEAD vs git 历史）定位为「本地 main 陈旧」而非 UI 编辑，rebase 到 `origin/main` 后生产内容命中 `#321` 历史版本，预检改判 `stale` 放行——**未**使用 `sync_prompts.py` 收编。发布后已回验周期契约段未被覆盖。

**给后续会话的提醒**：开工前先 `git fetch` 核对本地 main 与 `origin/main`；生产 prompt 可能领先于本地 main。

---

## 7. 结论

- [x] 自动化验证通过（全量 pytest 零回归 + ruff + mypy 零新增 + delta strict + prompt 指纹一致性）
- [x] 真实 LLM 冒烟通过（三态行为主线均在真模型上生效）
- [ ] **owner 人工确认 + 签字**（待补）
- [ ] archive 与 sync（`openspec/specs/` 主规范库合并）—— 前置：owner 签字 + `tasks.md` 全勾

**遗留待办**：`openspec/specs/agent-prompt-contracts/spec.md:89` 与 `openspec/specs/report-decision-rendering/spec.md:14` 仍是旧措辞（「默认按 short」「未申报形态」），须经 delta sync 合并替换，**MUST NOT** 手改主规范库。
