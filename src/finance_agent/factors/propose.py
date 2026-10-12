"""LLM 因子提案节点（add-factor-proposal-agent）：合同校验 + repair + prompt 组装 + 批量入口。

设计依据 docs/superpowers/plans/2026-10-13-add-factor-proposal-agent.md P1–P5：

- 合同（P2）：每条提案 = `expression`（DSL 合法，走默认复杂度硬约束）
  + `hypothesis`（经济学假设 ≥30 字，中文按字符数，去首尾空白）
  + `category`（枚举）。DSL 解析失败触发修复重试，上限 2 次
  （首轮 + 2 = 3 次尝试）；耗尽后该条丢弃进 `rejected`，并在 trace metadata
  记 `proposal_rejected`（逐条 raw + 最后错误）。
- 双档路由（P1）：broad → quick profile / deep → deep profile，经 PolicyRouter
  （`route_proposal_call`）；实际 LLM 调用经 gateway，`purpose="factor_proposal"`。
- 跨桶采样（P4）：prompt 注入分桶统计与算子历史 IC 先验；单桶占比 > 50% 时
  显式要求候选偏向其他桶。
- 软评分（P5）：`alignment_score` 只写元数据，**任何代码路径不得因该分值拒收**。

LLM 调用经注入的 client（`ProposalLLMClient` 协议，stub 可替，单测零网络）；
默认实现 `GatewayProposalClient` 薄包 gateway `complete_text`。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Protocol

from finance_agent.factors.dsl import DSLParseError, FactorComplexityError, parse
from finance_agent.prompts.loader import load_prompt

# 提案用途标签（PolicyRouter 注册名 + trace 用途标签，逐字）
PURPOSE_TAG = "factor_proposal"

# category 枚举（spec 逐字：价量/反转/动量/流动性/波动率）
CATEGORIES: tuple[str, ...] = ("价量", "反转", "动量", "流动性", "波动率")

# hypothesis 下限（中文按字符数，去首尾空白后计数）
MIN_HYPOTHESIS_CHARS = 30

# 修复重试上限（首轮之外允许的修复次数；总尝试次数 = 1 + MAX_REPAIR_ATTEMPTS）
MAX_REPAIR_ATTEMPTS = 2

# 广度变异批次候选数下限（spec：每批次产出不少于 20 条候选）
MIN_BROAD_BATCH_N = 20

# 单桶占比超过该阈值 → prompt 追加显式跨桶指令（不含等于）
_DOMINANCE_THRESHOLD = 0.5

# 提案 prompt 模板名（权威源 src/finance_agent/prompts/factor_proposal.md）
_PROMPT_NAME = "factor_proposal"


@dataclass(frozen=True)
class FactorProposal:
    """通过合同校验的一条因子提案（`mode`：产出档位 broad/deep）。"""

    expression: str
    hypothesis: str
    category: str
    alignment_score: float | None = None
    mode: str = "broad"


@dataclass(frozen=True)
class RejectedProposal:
    """三次尝试后仍不合法的提案：原始 payload + 最后一次错误原文。"""

    raw: dict[str, Any]
    error: str


@dataclass(frozen=True)
class ProposalBatchResult:
    """批量提案结果：通过的提案 + 丢弃明细 + trace metadata（含 proposal_rejected 标记）。"""

    proposals: tuple[FactorProposal, ...]
    rejected: tuple[RejectedProposal, ...]
    trace_metadata: dict[str, Any]


class ProposalResponseError(ValueError):
    """提案响应无法解析为 JSON payload（携带原始文本，供 repair/trace 记录）。"""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class ProposalLLMClient(Protocol):
    """提案 LLM 客户端协议（stub 可替；默认实现 `GatewayProposalClient`）。

    `complete_json(prompt, *, purpose, metadata) -> dict`：一次提案调用，返回
    已解析的 payload dict（`{"proposals": [...]}` 或裸提案 dict）；响应无法解析为
    JSON 时抛 `ProposalResponseError`（repair 循环据此计入一次失败尝试）。
    """

    def complete_json(
        self, prompt: str, *, purpose: str, metadata: dict[str, Any]
    ) -> dict[str, Any]: ...


def _soft_alignment_score(raw: Any) -> float | None:
    """alignment_score 软信号归一（P5）：数值收敛到 [0,1]；缺失/非数值 → None。

    该分值**永不参与拒收**——非数值（含 bool）按「未评分」处理而非合同违约。
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return min(1.0, max(0.0, float(raw)))


def validate_proposal_payload(payload: dict[str, Any]) -> tuple[FactorProposal | None, str | None]:
    """合同校验（P2）：返回 `(proposal, None)` 或 `(None, 错误消息)`。

    校验项与错误消息（错误消息即 repair 上下文原文）：
    - `expression`：非空字符串且 `parse()` 通过（默认复杂度硬约束）；
    - `hypothesis`：去首尾空白后 `len(...) >= MIN_HYPOTHESIS_CHARS`
      （中文按字符数）；
    - `category` ∈ CATEGORIES；
    - `alignment_score`：软信号（见 `_soft_alignment_score`），不拒收。
    """
    if not isinstance(payload, dict):
        return None, "提案必须是 JSON 对象"
    expression = payload.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        return None, "expression 缺失或非字符串"
    try:
        parse(expression)
    except (DSLParseError, FactorComplexityError) as exc:
        return None, f"expression DSL 校验失败：{exc}"
    hypothesis = payload.get("hypothesis")
    if not isinstance(hypothesis, str):
        return None, "hypothesis 缺失或非字符串"
    stripped = hypothesis.strip()
    if len(stripped) < MIN_HYPOTHESIS_CHARS:
        return None, (
            f"hypothesis 不足 {MIN_HYPOTHESIS_CHARS} 字（去首尾空白后 {len(stripped)} 字）"
        )
    category = payload.get("category")
    if category not in CATEGORIES:
        return None, f"category {category!r} 不在枚举 {list(CATEGORIES)}"
    return (
        FactorProposal(
            expression=expression.strip(),
            hypothesis=stripped,
            category=category,
            alignment_score=_soft_alignment_score(payload.get("alignment_score")),
        ),
        None,
    )


@dataclass
class _Draft:
    """一轮未通过合同校验的条目（下一轮修复对象）。"""

    raw: dict[str, Any]
    error: str
    attempts: int


def _response_items(payload: Any) -> list[tuple[dict[str, Any], str | None]]:
    """响应 payload → `(条目 dict, 预置错误)` 列表。

    `{"proposals": [...]}` 为批量形态；裸提案 dict（含合同字段）视作单条；
    其余形态包成一条不可解析条目（raw 保留原 payload 供 trace 与修复上下文）。
    """
    if isinstance(payload, dict):
        proposals = payload.get("proposals")
        if isinstance(proposals, list):
            return [
                (item, None)
                if isinstance(item, dict)
                else ({"_invalid_item": item}, "提案条目必须是 JSON 对象")
                for item in proposals
            ]
        if any(key in payload for key in ("expression", "hypothesis", "category")):
            return [(payload, None)]
    return [
        (
            {"_invalid_payload": payload},
            '响应缺 proposals 列表（期望 {"proposals": [...]} 形态）',
        )
    ]


def _validate_round(
    items: list[tuple[dict[str, Any], str | None]], *, mode: str, attempts: int
) -> tuple[list[FactorProposal], list[_Draft]]:
    """校验一轮响应条目：通过者收编（钉上 mode），未通过者成下一轮修复对象。"""
    proposals: list[FactorProposal] = []
    drafts: list[_Draft] = []
    for raw, preset_error in items:
        if preset_error is not None:
            drafts.append(_Draft(raw=raw, error=preset_error, attempts=attempts))
            continue
        proposal, error = validate_proposal_payload(raw)
        if proposal is None:
            drafts.append(_Draft(raw=raw, error=error or "合同校验失败", attempts=attempts))
        else:
            proposals.append(dataclasses.replace(proposal, mode=mode))
    return proposals, drafts


def _repair_prompt(base_prompt: str, drafts: list[_Draft]) -> str:
    """修复轮 prompt：把上一轮失败条目的 payload 原文与校验错误原文追加为上下文。

    client 协议为单 prompt（非多轮对话），「错误消息原文作为上下文追加」在此
    实现为修复段逐条附错误原文 + 原始 payload。
    """
    lines = [base_prompt, "", "# 修复轮（上一轮输出未通过合同校验，错误原文如下）"]
    for index, draft in enumerate(drafts, 1):
        payload_text = json.dumps(draft.raw, ensure_ascii=False, default=str)
        lines.append(f"## 待修复 {index}")
        lines.append(f"原始 payload：{payload_text}")
        lines.append(f"校验错误：{draft.error}")
    lines.append("请修正上述错误后重新输出完整 JSON（proposals 列表），不要重复已通过的提案。")
    return "\n".join(lines)


def _trace_metadata(*, mode: str, rejected: tuple[RejectedProposal, ...]) -> dict[str, Any]:
    """批量调用 trace metadata（P2）：用途标签 + 档位 + 逐条拒收明细 + 拒收标记。"""
    meta: dict[str, Any] = {
        "purpose_tag": PURPOSE_TAG,
        "mode": mode,
        "rejected": [{"raw": item.raw, "error": item.error} for item in rejected],
    }
    if rejected:
        meta["proposal_rejected"] = True
    return meta


def _run_proposal_batch(*, mode: str, prompt: str, client: Any) -> ProposalBatchResult:
    """批量执行：首轮 + 至多 `MAX_REPAIR_ATTEMPTS` 次修复（P2/P3）。

    每轮调用 client 一次；通过者立即收编，未通过者进 next 轮修复上下文；
    修复轮响应同样按条目校验（部分通过即部分收编）。轮次耗尽后仍未通过的条目
    逐条进 `rejected`（raw + 最后错误）并在 trace metadata 记 `proposal_rejected`。
    """
    proposals: list[FactorProposal] = []
    drafts: list[_Draft] = []
    for round_index in range(1 + MAX_REPAIR_ATTEMPTS):
        call_prompt = prompt if round_index == 0 else _repair_prompt(prompt, drafts)
        metadata: dict[str, Any] = {
            "purpose_tag": PURPOSE_TAG,
            "mode": mode,
            "attempt": round_index + 1,
            "repair_count": round_index,
        }
        try:
            payload = client.complete_json(call_prompt, purpose=PURPOSE_TAG, metadata=metadata)
            items = _response_items(payload)
        except ProposalResponseError as exc:
            items = [({"raw_text": exc.raw_text}, str(exc))]
        round_proposals, drafts = _validate_round(items, mode=mode, attempts=round_index + 1)
        proposals.extend(round_proposals)
        if not drafts:
            break
    rejected = tuple(RejectedProposal(raw=draft.raw, error=draft.error) for draft in drafts)
    return ProposalBatchResult(
        proposals=tuple(proposals),
        rejected=rejected,
        trace_metadata=_trace_metadata(mode=mode, rejected=rejected),
    )


def build_broad_prompt(
    *, bucket_stats: dict[str, int], operator_ic: dict[str, float], n: int
) -> str:
    """广度变异 prompt 组装（P4，纯函数）：模板 + 分桶统计 + 算子先验 + 候选数要求。"""
    template = load_prompt(_PROMPT_NAME)
    sampling_context = f"因子库分桶存量：{bucket_stats}\n算子历史入库 IC 均值：{operator_ic}"
    extra_instruction = f"本批需产出不少于 {n} 条候选（proposals 数组长度 ≥ {n}）。"
    return _render_template(
        template, sampling_context=sampling_context, extra_instruction=extra_instruction
    )


def build_deep_prompt(*, feedback: list[dict]) -> str:
    """深度改进 prompt 组装（P4，纯函数）：拒收原因分布 + 衰减复盘注入。"""
    template = load_prompt(_PROMPT_NAME)
    sampling_context = f"因子库反馈（拒收原因分布 / 衰减复盘）：{feedback}"
    extra_instruction = "针对上述反馈做深度改进：优先修复高频拒收原因、替换已衰减形态。"
    return _render_template(
        template, sampling_context=sampling_context, extra_instruction=extra_instruction
    )


def _render_template(template: str, *, sampling_context: str, extra_instruction: str) -> str:
    """占位符替换（与 `load_prompt` 的 `{{key}}` 约定一致）。"""
    return (
        template.replace("{{categories}}", "/".join(CATEGORIES))
        .replace("{{sampling_context}}", sampling_context)
        .replace("{{extra_instruction}}", extra_instruction)
    )


def propose_broad_batch(
    *,
    n: int = MIN_BROAD_BATCH_N,
    client: Any,
    llm_config: dict[str, Any] | None = None,
    bucket_stats: dict[str, int],
    operator_ic: dict[str, float],
) -> ProposalBatchResult:
    """广度变异批次（≥20 条候选；P3）：入参 n 硬校验后组装 prompt 并执行 repair 循环。

    `n` 为 prompt 级要求（生成本批候选数）；响应条数由 LLM 满足，本函数不因
    响应条数少于 n 拒绝整批（合同逐条约束，见 spec「提案输出合同」）。
    `llm_config`：请求级 LLM 配置（供路由档位决策/审计；stub 客户端可忽略）。
    """
    if n < MIN_BROAD_BATCH_N:
        raise ValueError(f"广度变异批次候选数 n 不得少于 {MIN_BROAD_BATCH_N}（收到 {n}）")
    prompt = build_broad_prompt(bucket_stats=bucket_stats, operator_ic=operator_ic, n=n)
    return _run_proposal_batch(mode="broad", prompt=prompt, client=client)


def propose_deep_improvement(
    *,
    feedback: list[dict],
    client: Any,
    llm_config: dict[str, Any] | None = None,
) -> ProposalBatchResult:
    """深度改进批次（P3）：拒收原因分布 + 衰减复盘注入 prompt 后执行 repair 循环。"""
    prompt = build_deep_prompt(feedback=feedback)
    return _run_proposal_batch(mode="deep", prompt=prompt, client=client)
