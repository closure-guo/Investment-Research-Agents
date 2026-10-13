# add-factor-proposal-agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 LLM 因子提案节点：合同化输出（expression/hypothesis/category）+ DSL 解析失败的修复重试（上限 2 次）→ 丢弃并 trace 标记；双 profile 分工（broad=quick / deep=deep，均带用途标签 `factor_proposal`）；跨桶采样与算子先验注入；假设-因子对齐软评分。

**Architecture:** `factors/propose.py` 纯业务层（prompt 组装、合同校验、repair 循环、路由决策为纯函数；LLM 调用经注入的 client 便于 stub）。`llm/types.py` 的 `Purpose` 增加 `factor_proposal`；`llm/router.py` 注册其能力要求。prompt 模板入 `src/finance_agent/prompts/factor_proposal.md`（权威源；部署经既有 `scripts/deploy_prompts.py`，本机 Langfuse 不在线时如实记录为待部署）。

**Tech Stack:** Python 3.14 / dataclasses / 既有 LLM Gateway（`llm/gateway.py`）+ PolicyRouter / pytest（stub client，零网络）。

## Global Constraints

- 测试：`uv run pytest tests/factors tests/llm -q`；ruff/mypy 同仓库纪律。
- TDD 红→绿；既有测试零修改（`Purpose` 字面量扩展与 REQUIRED_CAPS 新增是新增断言允许的既有文件改动——只在既有 router 测试文件**追加**用例）。
- 合同逐字：`expression`（DSL 合法）、`hypothesis`（≥30 字）、`category` ∈ {价量, 反转, 动量, 流动性, 波动率}；失败 → repair 重试上限 2 次（共 3 次尝试）→ 丢弃 + Langfuse trace 标记 `proposal_rejected`。
- broad 批 ≥20 条候选；deep 改进消费因子库反馈（拒收原因分布/衰减复盘）。
- 对齐软评分（LLM 自评 0-1）只写元数据，**不参与任何硬拒收**。
- 分桶占比 > 50% 时 prompt 必须含该桶占比与「偏向其他桶」的显式指令。
- 零真实网络：全部测试用 stub client（`TESTING` 语义与既有 stub 约定一致）；真跑（50 条提案人工核对）作为验证步骤单独执行，失败如实记录。

## 关键设计决策（实施者必读）

**P1 — 用途注册与双 profile 路由（对 delta 的字面实现）**：
- `Purpose` 字面量新增 `"factor_proposal"`；`REQUIRED_CAPS["factor_proposal"] = {"json_schema": {"json_mode", "strict_schema"}}`（结构化合同硬要求；`_BASE_CAP.json_schema="json_mode"` 保证 env 解析出的通用 profile 可通过）。
- 路由纯函数 `route_proposal_call(mode, *, llm_config=None) -> RouterResult`：候选 = [quick profile, deep profile]（各自 `resolve_profile(purpose="quick"/"deep", llm_config=...)`）；broad → `select_profile(purpose="factor_proposal", candidates=[quick, deep])`（保序 → primary=quick）；deep → 候选序 [deep, quick] → primary=deep。**「经 PolicyRouter 解析」= 该函数即路由器调用点**。
- 实际 LLM 调用经 `gateway.complete_text(...)`/`complete_text_with_fallback(...)`，`purpose="factor_proposal"`（trace 用途标签）；trace metadata 附 `{"purpose_tag": "factor_proposal", "mode": "broad"|"deep"}`。

**P2 — repair 循环语义**：首轮 + 至多 2 次修复 = 3 次尝试；每次修复把**解析错误消息原文**作为上下文追加进对话；三次后仍不可解析 → 该条进入 `rejected` 列表（含最后错误），并在 trace metadata 记 `proposal_rejected`（逐条：`{"rejected": [{"raw": ..., "error": ...}]}` 挂在批量调用的 metadata 上）。合同校验含 hypothesis 字数（中文按字符数 ≥30，去首尾空白）与 category 枚举。

**P3 — 批量返回**：`ProposalBatchResult(proposals: tuple[FactorProposal, ...], rejected: tuple[RejectedProposal, ...], trace_metadata: dict)`；`propose_broad_batch(n≥20)` 校验入参；`propose_deep_improvement(feedback)` 把反馈（拒收原因分布 + 衰减复盘文本）注入 prompt。

**P4 — prompt 组装纯函数**：`build_broad_prompt(bucket_stats, operator_ic, n) -> str`：渲染模板 `prompts/factor_proposal.md`（`str.format` 或简单占位替换——按既有 prompt 加载约定实现，参考 `prompts/` 其他模板的加载方式）；分桶占比 >50% → 追加显式指令段（含占比数字与「其他桶」枚举）；`build_deep_prompt(feedback)` 同理。两个函数纯、可单测（断言文本含占比与指令）。

**P5 — 软评分**：LLM 输出可含 `alignment_score`（0-1，缺失 → None）；写入 `FactorProposal.alignment_score`；**任何代码路径不得因该分值拒收**（测试：alignment_score=0 的提案仍进入 proposals）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/llm/types.py`（改，加字面量） | `Purpose` 增 `"factor_proposal"` |
| `src/finance_agent/llm/router.py`（改，加一行） | `REQUIRED_CAPS["factor_proposal"]` |
| `src/finance_agent/factors/propose.py`（新） | 合同/repair/路由/prompt/批量入口 |
| `src/finance_agent/prompts/factor_proposal.md`（新） | 提案 prompt 模板（权威源） |
| `tests/factors/test_propose.py`（新） | 全部单测（stub client） |
| `tests/llm/test_router.py`（追加） | `factor_proposal` 能力过滤 + 双 profile 路由用例 |
| `tests/validation/2026-10-13-add-factor-proposal-agent-validation.md`（新） | 验证报告（含 50 条真跑或受阻记录） |

## Interfaces（逐字）

```python
CATEGORIES: tuple[str, ...] = ("价量", "反转", "动量", "流动性", "波动率")
@dataclass(frozen=True) class FactorProposal:
    expression: str; hypothesis: str; category: str
    alignment_score: float | None = None; mode: str = "broad"
@dataclass(frozen=True) class RejectedProposal:
    raw: dict; error: str
@dataclass(frozen=True) class ProposalBatchResult:
    proposals: tuple[FactorProposal, ...]; rejected: tuple[RejectedProposal, ...]
    trace_metadata: dict
def validate_proposal_payload(payload: dict) -> tuple[FactorProposal | None, str | None]  # (proposal, error)
def route_proposal_call(mode: str, *, llm_config: dict | None = None) -> RouterResult     # mode ∈ {"broad","deep"}
def build_broad_prompt(*, bucket_stats: dict[str, int], operator_ic: dict[str, float], n: int) -> str
def build_deep_prompt(*, feedback: list[dict]) -> str
def propose_broad_batch(*, n: int = 20, client: Any, llm_config: dict | None = None,
                        bucket_stats: dict[str, int], operator_ic: dict[str, float]) -> ProposalBatchResult
def propose_deep_improvement(*, feedback: list[dict], client: Any, llm_config: dict | None = None) -> ProposalBatchResult
```
`client` 协议（stub 可替）：`client.complete_json(prompt: str, *, purpose: str, metadata: dict) -> dict`——实施时若既有 gateway 无同名方法，则定义 `ProposalLLMClient` Protocol 并在默认实现里包 `gateway.complete_text`（实施者读 gateway 签名后钉死；stub 只需实现该 Protocol）。

---

### Task 1: 合同 + repair 循环

- [ ] **Step 1: 写失败测试**：合法 payload → proposal；`expression` 未知算子 → error 含解析消息；hypothesis 29 字 → error；category 不在枚举 → error；`alignment_score=0` 不拒收；repair：stub 依次返回 [坏, 坏, 好] → 第 3 次成功且 proposals=1；[坏, 坏, 坏] → rejected=1 且 `trace_metadata["rejected"][0]["error"]` 非空、`purpose_tag` 存在；入参 `n < 20` → `ValueError`。
- [ ] **Step 2–4: 红 → 实现 → 绿**（P2/P3）。
- [ ] **Step 5: Commit** `feat(factor-proposal): contract validation + repair loop with trace rejection (T1)`。

### Task 2: 用途注册 + 双 profile 路由

- [ ] **Step 1: 写失败测试**：`route_proposal_call("broad")` → `primary` 为 quick profile（monkeypatch `resolve_profile` 返回带名字的假 profile）；`"deep"` → deep；未知 mode → `ValueError`；router 层：`select_profile(purpose="factor_proposal", candidates=[json_mode profile, json_schema="none" profile])` → 后者被拒（REQUIRED_CAPS 生效），无候选 → `UnsupportedCapabilityError`；trace 带 `purpose_tag`。
- [ ] **Step 2–4: 红 → 实现 → 绿**（P1；`Purpose` 字面量与 REQUIRED_CAPS 改动在此提交）。
- [ ] **Step 5: Commit** `feat(llm): register factor_proposal purpose + quick/deep routing (T2)`。

### Task 3: 跨桶采样 prompt + 软评分

- [ ] **Step 1: 写失败测试**：`build_broad_prompt` 含分桶占比数字与算子 IC 表；动量占比 70% → 文本含「70%」与显式「偏向其他桶（价量/反转/流动性/波动率）」指令；占比均 ≤50% → 不含该指令段；`build_deep_prompt` 含拒收原因分布与衰减复盘；模板文件存在且被加载（`prompts/factor_proposal.md` 非空、含占位符）；alignment_score 透传与不拒收（P5）。
- [ ] **Step 2–4: 红 → 实现 → 绿。**
- [ ] **Step 5: Commit** `feat(factor-proposal): cross-bucket prompt assembly + operator priors + soft alignment (T3)`。

### Task 4: 验证与收口

- [ ] 真跑尝试（有 key 才跑，无则如实记录）：用现有 provider 配置（`LLM_*` env）发起 2 批 broad（各 ≥20 条）→ 统计 DSL 可解析率、hypothesis 完整率、category 分布、alignment 分布；结果落报告。**不得伪造**；网络/额度受阻如实记录并挂 follow-up。
- [ ] prompt 部署：`uv run python scripts/deploy_prompts.py`（本机 Langfuse 不在线 → 如实记录为「待部署」；不得声称已部署）。
- [ ] 报告 `tests/validation/2026-10-13-add-factor-proposal-agent-validation.md`（命令证据、真跑结果或受阻说明、结论）；`tasks.md` 全勾 + 逐条注明。
- [ ] Commit `docs(openspec): add-factor-proposal-agent validation report + tasks 回填`；（控制器执行 archive）。
