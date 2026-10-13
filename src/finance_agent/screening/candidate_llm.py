"""L3 LLM 排雷粗筛（add-screening-candidate-llm T1；C1–C3/C5 决策部分）。

计划 docs/superpowers/plans/2026-10-13-add-screening-candidate-llm.md 关键设计决策
（实施者不得自行改口径）：

- **C1 client seam**：``TriageLLMClient`` 协议与 ``factors/propose.py`` 的
  ``ProposalLLMClient`` 形状相同（``complete_json(prompt, *, purpose, metadata)``），
  但 purpose 固定 ``"quick"``（成本纪律，spec 明文 quick profile）；默认实现
  ``GatewayTriageClient`` 薄包 gateway ``complete_text`` 并把 quick **钉死**
  （调用方即使传其他 purpose 也不会触发 deep 档），stub 可直接替换（单测零网络）。
- **C2 prompt 组装**：``build_triage_prompt(candidate, events)`` 渲染
  ``prompts/screening_triage.md``（权威源，同既有部署流程），注入综合分、
  ``factor_contribs`` 逐条摘要（factor_id/zscore/weight/contribution）与事件列表
  （``date [type] title``；缺 title 的事件退化为原始 dict 的 JSON 摘要）；事件为空
  → 「无最近事件」占位。纯函数、渲染确定。
- **C3 失败与违约降级**：合同逐字 ``verdict ∈ {keep, drop}``、``reason ≤ 50 字``
  （中文按字符数，去首尾空白）；违约重试 1 次仍违约 → 该只
  ``verdict="keep", degraded=True, reason=DEGRADED_REASON`` 并按 warning 记录
  （含 symbol + 错误类型）；任一只调用抛异常 → 同路降级；**不阻断批次**。
- **并发**：``ThreadPoolExecutor``，``max_workers`` 默认 8（可配置）；结果按
  **输入候选顺序**确定性归位（并发完成顺序不影响输出）。
- **事件数据由调用方注入**（``events_by_symbol``）：本 change 不自行取数——proposal
  中「复用 events/ 模块」按实际代码修正为参数注入，生产由 pipeline 从公告/新闻源
  装配（写进本 docstring 与验证报告）。
- **落库（C5 决策部分）**：批次决策（含降级 keep）一次经
  ``store.record_triage_decisions`` 写入 ``triage_decisions``（单连接 executemany
  + 单次 commit）；批次 trace metadata（含 warnings）经既有 span 助手 best-effort
  发射到 Langfuse。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from finance_agent.langfuse_tracing import get_langfuse, update_current_span
from finance_agent.llm.contracts import extract_json
from finance_agent.llm.gateway import complete_text
from finance_agent.llm.types import Purpose
from finance_agent.prompts.loader import load_prompt
from finance_agent.screening.ranker import Candidate

_logger = logging.getLogger("finance_agent.screening.candidate_llm")

# 用途标签（spec 明文 quick profile；成本纪律——粗筛不走 deep）
PURPOSE_TAG: Purpose = "quick"

# 输出合同（逐字）：verdict 枚举 + reason 字数上限（中文按字符数，去首尾空白）
VERDICTS: tuple[str, ...] = ("keep", "drop")
MAX_REASON_CHARS = 50

# 合同尝试次数：首轮 + 违约重试 1 次（仍违约 → keep 降级，见 C3）
MAX_CONTRACT_ATTEMPTS = 2

# 并发上限默认（Global Constraints；入参 max_workers 可配置）
DEFAULT_MAX_WORKERS = 8

# 固定降级文案（C3：失败与违约同路，宁可多留不可错杀）
DEGRADED_REASON = "triage 失败，按 keep 降级"

# triage prompt 模板名（权威源 src/finance_agent/prompts/screening_triage.md）
_PROMPT_NAME = "screening_triage"


@dataclass(frozen=True)
class TriageDecision:
    """单只候选的 L3 粗筛决策（``degraded=True`` = 失败/违约降级 keep）。"""

    symbol: str
    verdict: str
    reason: str
    degraded: bool = False


@dataclass(frozen=True)
class TriageBatchResult:
    """批次结果：决策（顺序 = 输入候选顺序）+ warnings（失败/违约明细）+ trace metadata。"""

    decisions: tuple[TriageDecision, ...]
    warnings: tuple[dict[str, Any], ...]
    trace_metadata: dict[str, Any]


class TriageResponseError(ValueError):
    """triage 响应无法解析为 JSON payload（携带原始文本，供 warning/trace 记录）。"""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class TriageLLMClient(Protocol):
    """粗筛 LLM 客户端协议（stub 可替；默认实现 `GatewayTriageClient`）。

    `complete_json(prompt, *, purpose, metadata) -> dict`：一次粗筛调用，返回已解析
    的 payload dict（``{"verdict": ..., "reason": ...}``）；响应无法解析为 JSON 时
    抛异常（run_triage 按单只失败降级 keep，不阻断批次）。
    """

    def complete_json(
        self, prompt: str, *, purpose: Purpose, metadata: dict[str, Any]
    ) -> dict[str, Any]: ...


class GatewayTriageClient:
    """默认 ``TriageLLMClient``：gateway ``complete_text`` 薄适配（唯一生产 LLM 入口）。

    - gateway 调用固定 ``purpose="quick"``（C1 成本纪律）——即使调用方传入其他
      purpose 也**钉死为 quick**，杜绝档位漂移；trace metadata 附 ``purpose_tag``
      与 symbol/attempt 等调用上下文；
    - 响应文本经 ``extract_json`` 解析为 payload；不可解析 → ``TriageResponseError``
      （携带响应原文；run_triage 按单只失败降级，不阻断批次）。
    """

    def __init__(
        self, *, llm_config: dict[str, Any] | None = None, max_tokens: int | None = None
    ) -> None:
        self._llm_config = llm_config
        self._max_tokens = max_tokens

    def complete_json(
        self, prompt: str, *, purpose: Purpose, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        trace_metadata: dict[str, Any] = {**metadata, "purpose_tag": PURPOSE_TAG}
        text, _meta = complete_text(
            [{"role": "user", "content": prompt}],
            purpose=PURPOSE_TAG,  # 钉死 quick：粗筛不得经本 seam 触发 deep 档
            max_tokens=self._max_tokens,
            llm_config=self._llm_config,
            trace={"name": _PROMPT_NAME, "metadata": trace_metadata},
        )
        try:
            return extract_json(text)
        except json.JSONDecodeError as exc:
            raise TriageResponseError(f"triage 响应不是合法 JSON：{exc}", raw_text=text) from exc


def validate_triage_payload(
    payload: dict[str, Any],
) -> tuple[tuple[str, str] | None, str | None]:
    """合同校验（C3）：返回 ``((verdict, reason), None)`` 或 ``(None, 错误消息)``。

    校验项（错误消息即 warning/审计原文）：
    - ``verdict`` ∈ ``VERDICTS``（``keep``/``drop``）；
    - ``reason``：去首尾空白后非空且 ``len(...) <= MAX_REASON_CHARS``（中文按字符数）。
    """
    if not isinstance(payload, dict):
        return None, "triage 响应必须是 JSON 对象"
    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        return None, f"verdict {verdict!r} 不在枚举 {list(VERDICTS)}"
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None, "reason 缺失或非字符串"
    stripped = reason.strip()
    if len(stripped) > MAX_REASON_CHARS:
        return None, (f"reason 超长（去首尾空白后 {len(stripped)} 字 > {MAX_REASON_CHARS} 字）")
    return (verdict, stripped), None


def _render_events(events: Sequence[dict]) -> str:
    """事件列表渲染：每条 ``date [type] title``；缺 title 的事件退化为 JSON 摘要。"""
    if not events:
        return "无最近事件"
    lines: list[str] = []
    for event in events:
        title = event.get("title")
        if title is None:
            payload = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
            lines.append(f"- {payload}")
            continue
        date_text = event.get("date") or "日期未知"
        type_text = event.get("type") or "类型未知"
        lines.append(f"- {date_text} [{type_text}] {title}")
    return "\n".join(lines)


def _render_factor_contribs(candidate: Candidate) -> str:
    """因子贡献摘要：逐条 ``因子 id: zscore / weight / contribution``（4 位小数）。"""
    if not candidate.factor_contribs:
        return "（无因子贡献记录）"
    return "\n".join(
        f"- 因子 {contrib.factor_id}: zscore={contrib.zscore:.4f}，"
        f"weight={contrib.weight:.4f}，contribution={contrib.contribution:.4f}"
        for contrib in candidate.factor_contribs
    )


def build_triage_prompt(candidate: Candidate, events: Sequence[dict]) -> str:
    """粗筛 prompt 组装（C2）：渲染 ``screening_triage.md`` 模板（纯函数，渲染确定）。

    注入：symbol / trade_date / 综合分 / 排名 / factor_contribs 逐条摘要 / 事件列表
    （空事件 → 「无最近事件」占位）。事件原样落入模板的 ``<events_data>`` 数据区块
    ——区块外模板声明「仅为数据、非指令」（prompt-injection 卫生）。
    """
    template = load_prompt(_PROMPT_NAME)
    return (
        template.replace("{{symbol}}", candidate.symbol)
        .replace("{{trade_date}}", candidate.trade_date)
        .replace("{{composite_score}}", f"{candidate.composite_score:.4f}")
        .replace("{{rank}}", str(candidate.rank))
        .replace("{{factor_contribs}}", _render_factor_contribs(candidate))
        .replace("{{events}}", _render_events(events))
    )


def _degraded_decision(symbol: str) -> TriageDecision:
    """C3 降级决策：固定 keep + degraded + 固定文案。"""
    return TriageDecision(symbol=symbol, verdict="keep", reason=DEGRADED_REASON, degraded=True)


def _triage_one(
    candidate: Candidate, prompt: str, *, client: TriageLLMClient
) -> tuple[TriageDecision, dict[str, Any] | None]:
    """单只执行：调用 → 合同校验 → 违约重试 1 次 → 仍违约/抛异常按 keep 降级。

    返回 ``(decision, warning | None)``，本函数**不向外抛异常**（单只失败不阻断批次）。
    """
    last_error: str | None = None
    for attempt in range(1, MAX_CONTRACT_ATTEMPTS + 1):
        metadata: dict[str, Any] = {
            "purpose_tag": PURPOSE_TAG,
            "symbol": candidate.symbol,
            "attempt": attempt,
        }
        try:
            payload = client.complete_json(prompt, purpose=PURPOSE_TAG, metadata=metadata)
        except Exception as exc:  # noqa: BLE001 -- 单只失败按 keep 降级，不阻断批次（C3）
            return _degraded_decision(candidate.symbol), {
                "symbol": candidate.symbol,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        contract, error = validate_triage_payload(payload)
        if contract is not None:
            verdict, reason = contract
            return TriageDecision(symbol=candidate.symbol, verdict=verdict, reason=reason), None
        last_error = error or "合同校验失败"
    return _degraded_decision(candidate.symbol), {
        "symbol": candidate.symbol,
        "error": "contract_violation",
        "message": last_error,
    }


def _trace_metadata(
    *,
    trade_date: str,
    decisions: tuple[TriageDecision, ...],
    warnings: tuple[dict[str, Any], ...],
    max_workers: int,
) -> dict[str, Any]:
    """批次 trace metadata：用途/日期/数量分布/降级与 warning 明细（spec：trace 含 warning）。"""
    keep_count = sum(1 for decision in decisions if decision.verdict == "keep")
    return {
        "purpose_tag": PURPOSE_TAG,
        "trade_date": trade_date,
        "candidate_count": len(decisions),
        "keep_count": keep_count,
        "drop_count": len(decisions) - keep_count,
        "degraded_count": sum(1 for decision in decisions if decision.degraded),
        "max_workers": max_workers,
        "warnings": [dict(warning) for warning in warnings],
    }


def _emit_trace_metadata(metadata: dict[str, Any]) -> None:
    """best-effort：批次 trace metadata（含 warnings）写入当前 Langfuse span。

    Langfuse 未配置 → 仅 debug 日志（零网络零副作用）；写入失败不阻断业务。
    """
    if get_langfuse() is None:
        _logger.debug("Langfuse 未配置：triage 批次 trace metadata 仅返回、不发射")
        return
    try:
        update_current_span(metadata={"screening_triage": metadata})
    except Exception:  # noqa: BLE001 -- 观测失败不阻断业务
        _logger.warning("triage 批次 trace metadata 发射失败", exc_info=True)


def run_triage(
    candidates: Sequence[Candidate],
    *,
    events_by_symbol: dict[str, list[dict]],
    client: TriageLLMClient,
    trade_date: str,
    max_workers: int = DEFAULT_MAX_WORKERS,
    db_path: str | Path | None = None,
) -> TriageBatchResult:
    """L3 粗筛批次入口：并发逐只 triage → 合同校验/降级 → 落库 → 返回（输入序）。

    - ``events_by_symbol``：调用方注入的事件列表（proposal 的「复用 events/ 模块」
      按实际代码修正为参数注入——本模块不自行取数，生产由 pipeline 装配）；
      无该 symbol → 空事件 → prompt 给出「无最近事件」占位；
    - 并发经 ``ThreadPoolExecutor``（上限 ``max_workers``，默认 8）；结果按**输入
      候选顺序**归位，并发完成顺序不改变输出；
    - 每只决策（含降级 keep）批次一次经 ``store.record_triage_decisions`` 落库
      （单连接 executemany + 单次 commit，避免逐行建连/重复建表；主线程顺序写，
      短连接纪律，避免并发写 SQLite 争锁）；
    - 批次 trace metadata（含 warnings）best-effort 发射到 Langfuse。
    """
    if max_workers < 1:
        raise ValueError(f"max_workers 必须是正整数: {max_workers!r}")
    prompts = [
        build_triage_prompt(candidate, events_by_symbol.get(candidate.symbol, []))
        for candidate in candidates
    ]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_triage_one, candidate, prompt, client=client)
            for candidate, prompt in zip(candidates, prompts, strict=True)
        ]
        outcomes = [future.result() for future in futures]  # 输入序收集
    decisions = tuple(decision for decision, _warning in outcomes)
    warnings = tuple(warning for _decision, warning in outcomes if warning is not None)
    # 函数内导入：store 依赖本模块的合同枚举（模块级互相导入会成环），
    # 且本函数是该 seam 的唯一消费点。
    from finance_agent.screening.store import record_triage_decisions

    record_triage_decisions(trade_date, decisions, db_path=db_path)
    trace_metadata = _trace_metadata(
        trade_date=trade_date, decisions=decisions, warnings=warnings, max_workers=max_workers
    )
    _emit_trace_metadata(trace_metadata)
    return TriageBatchResult(decisions=decisions, warnings=warnings, trace_metadata=trace_metadata)
