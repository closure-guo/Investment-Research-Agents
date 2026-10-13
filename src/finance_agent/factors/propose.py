"""LLM 因子提案节点（add-factor-proposal-agent）：合同校验 + repair + prompt 组装 + 批量入口。

设计依据 docs/superpowers/plans/2026-10-13-add-factor-proposal-agent.md P1–P5：

- 合同（P2）：每条提案 = `expression`（DSL 合法，走默认复杂度硬约束）
  + `hypothesis`（经济学假设 ≥30 字，中文按字符数，去首尾空白）
  + `category`（枚举）。DSL 解析失败触发修复重试，上限 2 次
  （首轮 + 2 = 3 次尝试）；耗尽后该条丢弃进 `rejected`，并在 trace metadata
  记 `proposal_rejected`（逐条 raw + 最后错误）。修复轮的未应答条目持续
  待处理（评审修正 2：不得因某轮少答/空答而静默消失），批次 trace metadata
  经既有 span 助手 best-effort 发射到 Langfuse（评审修正 3）。
- 双档路由（P1）：broad → quick profile / deep → deep profile，经 PolicyRouter
  （`route_proposal_call`）；实际 LLM 调用经 gateway，`purpose="factor_proposal"`，
  并把路由选中的 profile **原样钉死**进本次调用（评审修正 1：gateway `profile=`
  覆盖，杜绝 purpose 重解析漂移）。候选按 (name, model, base_url) 去重
  （评审修正 4：fallback 链不得列 primary 自己）。
- 跨桶采样（P4）：prompt 注入分桶统计与算子历史 IC 先验；单桶占比 > 50% 时
  显式要求候选偏向其他桶。
- 软评分（P5）：`alignment_score` 只写元数据，**任何代码路径不得因该分值拒收**。
- broad 批次不足 n 时补一轮（同一 prompt + 缺口说明，评审修正 5），
  `shortfall`（初始缺口/最终缺口）落 trace metadata。

LLM 调用经注入的 client（`ProposalLLMClient` 协议，stub 可替，单测零网络）；
默认实现 `GatewayProposalClient` 薄包 gateway `complete_text`。
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from finance_agent.factors.dsl import DSLParseError, FactorComplexityError, parse
from finance_agent.langfuse_tracing import get_langfuse, update_current_span
from finance_agent.llm.contracts import extract_json
from finance_agent.llm.gateway import complete_text
from finance_agent.llm.resolver import resolve_profile
from finance_agent.llm.router import RouterResult, select_profile
from finance_agent.llm.types import ModelProfile, Purpose
from finance_agent.prompts.loader import load_prompt

_logger = logging.getLogger("finance_agent.factors.propose")

# 提案用途标签（PolicyRouter 注册名 + trace 用途标签，逐字）
PURPOSE_TAG: Purpose = "factor_proposal"

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
        self, prompt: str, *, purpose: Purpose, metadata: dict[str, Any]
    ) -> dict[str, Any]: ...


def route_proposal_call(mode: str, *, llm_config: dict[str, Any] | None = None) -> RouterResult:
    """按提案档位经 PolicyRouter 选 profile（P1；本题即路由器调用点）。

    - broad：候选 = [quick profile, deep profile]，保序 → primary=quick；
    - deep：候选序 [deep, quick] → primary=deep；
    - 两档候选各自 `resolve_profile(purpose="quick"/"deep", llm_config=...)`；
      按 (name, model, base_url) 去重后再 `select_profile(purpose="factor_proposal",
      ...)`（评审修正 4：两档解析到同一 profile 时，fallback 链不得列 primary 自己）；
    - `select_profile` 施加 REQUIRED_CAPS（json_schema ∈ {json_mode, strict_schema}）
      ——不满足的候选出局，全部出局时抛 `UnsupportedCapabilityError`
      （typed error，不发起裸调用）。
    """
    if mode == "broad":
        purposes: tuple[Purpose, Purpose] = ("quick", "deep")
    elif mode == "deep":
        purposes = ("deep", "quick")
    else:
        raise ValueError(f"未知提案档位 mode={mode!r}（仅支持 'broad'/'deep'）")
    candidates: list[ModelProfile] = []
    seen: set[tuple[str, str, str | None]] = set()
    for purpose in purposes:
        profile = resolve_profile(purpose=purpose, llm_config=llm_config)
        key = (profile.name, profile.model, profile.base_url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(profile)
    return select_profile(purpose=PURPOSE_TAG, candidates=candidates)


class GatewayProposalClient:
    """默认 `ProposalLLMClient`：gateway `complete_text` 薄适配（唯一生产 LLM 入口）。

    - 档位决策经 `route_proposal_call(mode)`（PolicyRouter 调用点，P1），并把
      路由选中的 profile 经 gateway `profile=` 覆盖**原样钉死**为本次调用的
      profile（评审修正 1：实际调用的就是路由选中的档位，无 purpose 重解析漂移）；
    - gateway 调用固定 `purpose="factor_proposal"`（trace 用途标签），
      trace metadata 附 `purpose_tag` / `mode` / router 决策（P1）；
    - 响应文本经 `extract_json` 解析为 payload；不可解析 → `ProposalResponseError`
      （携带响应原文，repair 循环据此计入一次失败尝试）。
    """

    def __init__(
        self,
        *,
        mode: str,
        llm_config: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> None:
        if mode not in ("broad", "deep"):
            raise ValueError(f"未知提案档位 mode={mode!r}（仅支持 'broad'/'deep'）")
        self._mode = mode
        self._llm_config = llm_config
        self._max_tokens = max_tokens

    def complete_json(
        self, prompt: str, *, purpose: Purpose, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        route = route_proposal_call(self._mode, llm_config=self._llm_config)
        trace_metadata: dict[str, Any] = {
            **metadata,
            "purpose_tag": PURPOSE_TAG,
            "mode": self._mode,
            "router": route.trace,
        }
        text, _meta = complete_text(
            [{"role": "user", "content": prompt}],
            purpose=purpose,
            max_tokens=self._max_tokens,
            profile=route.primary,
            trace={"name": f"{PURPOSE_TAG}.{self._mode}", "metadata": trace_metadata},
        )
        try:
            return extract_json(text)
        except json.JSONDecodeError as exc:
            raise ProposalResponseError(f"提案响应不是合法 JSON：{exc}", raw_text=text) from exc


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
    """一轮未通过合同校验的条目（下一轮修复对象）：原始 payload + 该轮错误原文。"""

    raw: dict[str, Any]
    error: str


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
    items: list[tuple[dict[str, Any], str | None]], *, mode: str
) -> tuple[list[FactorProposal], list[_Draft]]:
    """校验一轮响应条目：通过者收编（钉上 mode），未通过者成下一轮修复对象。"""
    proposals: list[FactorProposal] = []
    drafts: list[_Draft] = []
    for raw, preset_error in items:
        if preset_error is not None:
            drafts.append(_Draft(raw=raw, error=preset_error))
            continue
        proposal, error = validate_proposal_payload(raw)
        if proposal is None:
            drafts.append(_Draft(raw=raw, error=error or "合同校验失败"))
        else:
            proposals.append(dataclasses.replace(proposal, mode=mode))
    return proposals, drafts


def _draft_key(draft: _Draft) -> str:
    """待修复条目的身份键：原始 payload 的 canonical JSON（键排序、保中文逐字）。"""
    return json.dumps(draft.raw, ensure_ascii=False, sort_keys=True, default=str)


def _merge_drafts(*groups: list[_Draft]) -> list[_Draft]:
    """合并待修复条目并按身份键去重（后者覆盖前者 = 保留最新一次错误原文）。"""
    merged: dict[str, _Draft] = {}
    for group in groups:
        for draft in group:
            merged[_draft_key(draft)] = draft
    return list(merged.values())


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


def _topup_prompt(base_prompt: str, *, missing: int) -> str:
    """补充轮 prompt（评审修正 5）：同一基础 prompt + 缺口说明。"""
    return (
        f"{base_prompt}\n\n"
        f"# 补充轮（上一轮产出不足：距目标还差 {missing} 条）\n"
        f"请再产出不少于 {missing} 条候选（proposals 数组长度 ≥ {missing}），"
        "不要重复已通过的提案。"
    )


def _trace_metadata(
    *,
    mode: str,
    rejected: tuple[RejectedProposal, ...],
    router_trace: dict[str, Any],
    shortfall: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """批量调用 trace metadata（P1/P2）：用途标签 + 档位 + 路由决策 + 逐条拒收明细。

    `shortfall`（评审修正 5）：broad 批次不足 n 时的初始缺口 / 最终缺口。
    """
    meta: dict[str, Any] = {
        "purpose_tag": PURPOSE_TAG,
        "mode": mode,
        "router": router_trace,
        "rejected": [{"raw": item.raw, "error": item.error} for item in rejected],
    }
    if rejected:
        meta["proposal_rejected"] = True
    if shortfall is not None:
        meta["shortfall"] = shortfall
    return meta


def _emit_trace_metadata(metadata: dict[str, Any]) -> None:
    """best-effort：把批次 trace metadata（含 `proposal_rejected`）写入当前 Langfuse span。

    Langfuse 未配置 → 仅 debug 日志（零网络零副作用）；写入失败不阻断业务
    （评审修正 3：批次级弃收事实此前只随返回值流转，无落点）。
    """
    if get_langfuse() is None:
        _logger.debug("Langfuse 未配置：proposal 批次 trace metadata 仅返回、不发射")
        return
    try:
        update_current_span(metadata={"proposal_batch": metadata})
    except Exception:  # noqa: BLE001 -- 观测失败不阻断业务
        _logger.warning("proposal 批次 trace metadata 发射失败", exc_info=True)


def _run_proposal_batch(
    *,
    mode: str,
    prompt: str,
    client: ProposalLLMClient,
    llm_config: dict[str, Any] | None = None,
    n: int | None = None,
) -> ProposalBatchResult:
    """批量执行：首轮 + 至多 `MAX_REPAIR_ATTEMPTS` 次修复（P2/P3）+ 可选补充轮（修正 5）。

    每轮调用 client 一次；通过者立即收编，未通过者进下一轮修复上下文；
    修复轮响应按位置应答本轮待修复清单——未被应答的条目**跨轮保留**，空响应
    视为终止信号（评审修正 2：任何耗尽路径下未收编的条目都进 `rejected`，
    逐条 raw + 最后错误，绝不静默消失）。轮次耗尽后仍在待修复清单中的条目
    逐条进 `rejected`，并在 trace metadata 记 `proposal_rejected`。
    档位决策先经 `route_proposal_call`（能力不满足/未知档位在此即 typed error，
    不发起裸调用），其 trace 并入返回的 trace_metadata。
    `n`（仅 broad）：**首轮全数通过但条数不足 n**（未消耗任何修复轮）时补发一轮
    （同 prompt + 缺口说明，评审修正 5），`shortfall`（初始缺口 / 最终缺口）记入
    trace metadata；已触发修复轮或已有弃收的批次不追量（弃收事实优先，不再消耗调用）。
    """
    router_trace = route_proposal_call(mode, llm_config=llm_config).trace
    proposals: list[FactorProposal] = []
    pending: list[_Draft] = []
    repair_consumed = False
    for round_index in range(1 + MAX_REPAIR_ATTEMPTS):
        if round_index > 0:
            repair_consumed = True
        call_prompt = prompt if round_index == 0 else _repair_prompt(prompt, pending)
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
        round_proposals, failures = _validate_round(items, mode=mode)
        proposals.extend(round_proposals)
        if round_index > 0 and not items:
            # 修复轮空响应 = 终止信号：本轮什么都没答，待修复条目原样保留进 rejected
            break
        if round_index == 0:
            pending = _merge_drafts(failures)
        else:
            # 修复轮响应按位置应答待修复清单：响应条数之外的尾部条目未被处理，保留
            unaddressed = pending[len(items) :]
            pending = _merge_drafts(unaddressed, failures)
        if not pending:
            break
    shortfall: dict[str, Any] | None = None
    if n is not None and not repair_consumed and len(proposals) < n:
        initial = n - len(proposals)
        top_up_metadata: dict[str, Any] = {
            "purpose_tag": PURPOSE_TAG,
            "mode": mode,
            "attempt": 1 + MAX_REPAIR_ATTEMPTS + 1,
            "repair_count": 0,
            "top_up": True,
        }
        try:
            payload = client.complete_json(
                _topup_prompt(prompt, missing=initial),
                purpose=PURPOSE_TAG,
                metadata=top_up_metadata,
            )
            items = _response_items(payload)
        except ProposalResponseError as exc:
            items = [({"raw_text": exc.raw_text}, str(exc))]
        topup_proposals, topup_failures = _validate_round(items, mode=mode)
        proposals.extend(topup_proposals)
        pending = _merge_drafts(pending, topup_failures)
        shortfall = {"initial": initial, "final": max(0, n - len(proposals)), "top_up": True}
    rejected = tuple(RejectedProposal(raw=draft.raw, error=draft.error) for draft in pending)
    trace_metadata = _trace_metadata(
        mode=mode, rejected=rejected, router_trace=router_trace, shortfall=shortfall
    )
    _emit_trace_metadata(trace_metadata)
    return ProposalBatchResult(
        proposals=tuple(proposals),
        rejected=rejected,
        trace_metadata=trace_metadata,
    )


def _render_bucket_stats(bucket_stats: dict[str, int]) -> str:
    """分桶存量渲染：`桶名 N 条（P%）`，按存量降序（同量按桶名），无存量 → 明示为空。"""
    total = sum(bucket_stats.values())
    if total <= 0:
        return "（因子库为空）"
    parts = [
        f"{name} {count} 条（{count / total:.0%}）"
        for name, count in sorted(bucket_stats.items(), key=lambda item: (-item[1], item[0]))
    ]
    return "；".join(parts)


def _dominance_instruction(bucket_stats: dict[str, int]) -> str:
    """单桶占比 > 50% → 显式跨桶指令（含占比数字与其他桶枚举），否则空串（P4）。

    严格大于阈值才触发——占比均 ≤50% 时不产生任何引导段（spec「跨桶采样防收敛」）。
    """
    total = sum(bucket_stats.values())
    if total <= 0:
        return ""
    dominant, count = max(bucket_stats.items(), key=lambda item: item[1])
    share = count / total
    if share <= _DOMINANCE_THRESHOLD:
        return ""
    others = "/".join(category for category in CATEGORIES if category != dominant)
    return (
        f"单桶占优提示：{dominant} 桶存量占比 {share:.0%}（{count}/{total} 条）已超过 50%，"
        f"本批候选必须偏向其他桶（{others}），不得继续集中在 {dominant} 桶。"
    )


def _render_operator_ic(operator_ic: dict[str, float]) -> str:
    """算子历史入库 IC 均值渲染：`op: 0.0210`（IC 降序，同值按算子名）；无数据 → 明示。"""
    if not operator_ic:
        return "（暂无入库 IC 统计）"
    parts = [
        f"{op}: {ic:.4f}"
        for op, ic in sorted(operator_ic.items(), key=lambda item: (-item[1], item[0]))
    ]
    return "；".join(parts)


def _render_feedback(feedback: list[dict]) -> str:
    """因子库反馈渲染：逐条 `1. 键=值，键=值`（保持条目顺序）；无反馈 → 明示。"""
    if not feedback:
        return "（暂无因子库反馈）"
    return "\n".join(
        f"{index}. " + "，".join(f"{key}={value}" for key, value in item.items())
        for index, item in enumerate(feedback, 1)
    )


def build_broad_prompt(
    *, bucket_stats: dict[str, int], operator_ic: dict[str, float], n: int
) -> str:
    """广度变异 prompt 组装（P4）：分桶占比 + 算子先验 + 候选数 + 跨桶指令。

    渲染确定（同输入同输出）；模板经既有 `load_prompt`（Langfuse production 优先、
    本地 `prompts/factor_proposal.md` 兜底 + TTL 缓存）。
    """
    template = load_prompt(_PROMPT_NAME)
    sampling_context = (
        f"因子库分桶存量：{_render_bucket_stats(bucket_stats)}\n"
        f"算子历史入库 IC 均值：{_render_operator_ic(operator_ic)}"
    )
    extra_instruction = f"本批需产出不少于 {n} 条候选（proposals 数组长度 ≥ {n}）。"
    dominance = _dominance_instruction(bucket_stats)
    if dominance:
        extra_instruction = f"{extra_instruction}\n{dominance}"
    return _render_template(
        template, sampling_context=sampling_context, extra_instruction=extra_instruction
    )


def build_deep_prompt(*, feedback: list[dict]) -> str:
    """深度改进 prompt 组装（P4）：拒收原因分布 + 衰减复盘注入（模板同上）。"""
    template = load_prompt(_PROMPT_NAME)
    sampling_context = f"因子库反馈（拒收原因分布 / 衰减复盘）：\n{_render_feedback(feedback)}"
    extra_instruction = (
        "针对上述反馈做深度改进：优先修复高频拒收原因、替换已衰减形态；每条提案仍需满足输出合同。"
    )
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
    client: ProposalLLMClient,
    llm_config: dict[str, Any] | None = None,
    bucket_stats: dict[str, int],
    operator_ic: dict[str, float],
) -> ProposalBatchResult:
    """广度变异批次（≥20 条候选；P3）：入参 n 硬校验后组装 prompt 并执行 repair 循环。

    `n` 为 prompt 级要求（生成本批候选数）；合同逐条约束提案——响应条数少于 n
    时补一轮（同 prompt + 缺口说明，评审修正 5），`shortfall` 落 trace metadata；
    多于 n 不拒收整批（见 spec「提案输出合同」）。
    `llm_config`：请求级 LLM 配置，透传档位路由决策（trace metadata 记 router）。
    """
    if n < MIN_BROAD_BATCH_N:
        raise ValueError(f"广度变异批次候选数 n 不得少于 {MIN_BROAD_BATCH_N}（收到 {n}）")
    prompt = build_broad_prompt(bucket_stats=bucket_stats, operator_ic=operator_ic, n=n)
    return _run_proposal_batch(
        mode="broad", prompt=prompt, client=client, llm_config=llm_config, n=n
    )


def propose_deep_improvement(
    *,
    feedback: list[dict],
    client: ProposalLLMClient,
    llm_config: dict[str, Any] | None = None,
) -> ProposalBatchResult:
    """深度改进批次（P3）：拒收原因分布 + 衰减复盘注入 prompt 后执行 repair 循环。"""
    prompt = build_deep_prompt(feedback=feedback)
    return _run_proposal_batch(mode="deep", prompt=prompt, client=client, llm_config=llm_config)
