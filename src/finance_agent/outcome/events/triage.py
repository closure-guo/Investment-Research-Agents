"""业绩预告 LLM 分诊（delta add-event-driven-lane T4）。

设计要点（口径权威 docs/evals/metrics.md §1.15 + design.md「分诊合同与降级」）：

- **client seam**：``EventTriageClient`` 协议与 ``screening/candidate_llm.py`` 的
  ``TriageLLMClient`` 同形（``complete_json(prompt, *, purpose, metadata)``），
  purpose 钉死 ``"quick"``（成本纪律）；默认实现 ``GatewayTriageClient`` 薄包
  gateway ``complete_text`` + ``extract_json``（不可解析 → ``EventTriageResponseError``）。
- **prompt 组装**：``build_triage_prompt`` 渲染 ``prompts/event_triage.md``（权威源），
  注入预告要素全量 + 快照要素（``SNAPSHOT_INPUT_FIELDS``，快照表实际可得键），输入预算
  ≤ ``MAX_INPUT_FIELDS``（12）字段——预算常量集中定义，禁止散落。快照上下文由调用方
  **逐行时点化**注入（键 = ``forecast_key``；每行取 ≤ 该行 ``available_at`` 的最近快照日
  字段，MUST NOT 共用单一快照日/前视），缺 → 该条无快照字段。
- **合同与降级**：``verdict ∈ {outperform, in_line, risk}``、``reason ≤ 50 字``、
  ``evidence`` 至少 1 条（field+value）——非法输出拒收；单条失败（typed ``LLMError``
  或解析失败）降级 ``in_line`` + warning，**不阻断批次**。降级行照常落库（reason 为
  固定文案、evidence 记 ``{"field": "degraded", ...}`` 供后续识别/重分诊），与
  screening 先例「宁可多留不可错杀」一致。
- **落库**：分诊结果含模型标识与 prompt 版本指纹（``load_prompt_with_meta``，
  指纹 ``<name>@<version>``）；主线程顺序写（短连接纪律，避免并发写 SQLite 争锁）。
- **并发**：``ThreadPoolExecutor``，``max_workers`` 默认 8，结果按输入序归位。
- **时点纪律**：``as_of`` 给定时，``available_at > as_of`` 的行跳过（防御性截断，
  MUST NOT 前视），计入 ``skipped``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from finance_agent.langfuse_tracing import get_langfuse, update_current_span
from finance_agent.llm.contracts import extract_json
from finance_agent.llm.gateway import complete_text
from finance_agent.llm.types import Purpose
from finance_agent.outcome.events.model import forecast_key, record_triage
from finance_agent.prompts.loader import load_prompt, load_prompt_with_meta

logger = logging.getLogger("finance_agent.outcome.events.triage")

# prompt 模板名（权威源 src/finance_agent/prompts/event_triage.md）
PROMPT_NAME = "event_triage"

# 用途标签（spec 成本纪律：分诊走 quick 档，钉死）
PURPOSE_TAG: Purpose = "quick"

# 输出合同：verdict 枚举 + reason 字数上限（中文按字符数，去首尾空白）
VERDICTS: tuple[str, ...] = ("outperform", "in_line", "risk")
MAX_REASON_CHARS = 50
DEGRADED_VERDICT = "in_line"
DEGRADED_REASON = "分诊失败，按 in_line 降级"

# 分诊输入预算（≤ 12 字段，常量集中定义）：预告要素 7 + 快照要素余量 5
MAX_INPUT_FIELDS = 12
FORECAST_INPUT_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "report_period",
    "forecast_type",
    "change_low",
    "change_high",
    "announce_date",
)
# 快照要素（**快照表实际可得键**：close←close_raw / pct_chg / amount；pe/market_cap
# 快照表无此列，不得出现在注入面）——调用方按行时点化注入，取前
# MAX_INPUT_FIELDS - len(FORECAST_INPUT_FIELDS) 个。
SNAPSHOT_INPUT_FIELDS: tuple[str, ...] = (
    "close",
    "pct_chg",
    "amount",
)

# 并发上限默认（brief：并发 ≤ 8）
DEFAULT_MAX_WORKERS = 8


class EventTriageResponseError(ValueError):
    """分诊响应无法解析为 JSON payload（携带原始文本，供 warning/trace 记录）。"""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class EventTriageClient(Protocol):
    """分诊 LLM 客户端协议（stub 可替；默认实现 ``GatewayTriageClient``）。"""

    def complete_json(
        self, prompt: str, *, purpose: Purpose = "quick", metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


class GatewayTriageClient:
    """默认 ``EventTriageClient``：gateway ``complete_text`` 薄适配（唯一生产 LLM 入口）。

    - purpose 固定 ``quick``（成本纪律）——调用方传其他 purpose 也**钉死为 quick**；
    - 响应经 ``extract_json`` 解析；不可解析 → ``EventTriageResponseError``（单条失败
      由 ``triage_forecasts`` 按 in_line 降级，不阻断批次）；
    - ``last_model``：最近一次调用的模型标识（gateway trace metadata 的 ``model``），
      供分诊结果落库（``triage_forecasts`` 读取）。
    """

    def __init__(
        self, *, llm_config: dict[str, Any] | None = None, max_tokens: int | None = None
    ) -> None:
        self._llm_config = llm_config
        self._max_tokens = max_tokens
        self.last_model: str | None = None

    def complete_json(
        self, prompt: str, *, purpose: Purpose = "quick", metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        trace_metadata: dict[str, Any] = {**(metadata or {}), "purpose_tag": PURPOSE_TAG}
        text, meta = complete_text(
            [{"role": "user", "content": prompt}],
            purpose=PURPOSE_TAG,  # 钉死 quick：分诊不得经本 seam 触发 deep 档
            max_tokens=self._max_tokens,
            llm_config=self._llm_config,
            trace={"name": PROMPT_NAME, "metadata": trace_metadata},
        )
        if isinstance(meta, Mapping) and meta.get("model"):
            self.last_model = str(meta["model"])
        try:
            return extract_json(text)
        except json.JSONDecodeError as exc:
            raise EventTriageResponseError(f"分诊响应不是合法 JSON：{exc}", raw_text=text) from exc


def validate_triage_payload(
    payload: Any,
) -> tuple[tuple[str, str, list[dict[str, Any]]] | None, str | None]:
    """合同校验：返回 ``((verdict, reason, evidence), None)`` 或 ``(None, 错误消息)``。

    校验项（错误消息即 warning 原文）：
    - ``verdict`` ∈ ``VERDICTS``；
    - ``reason``：去首尾空白后非空且 ≤ ``MAX_REASON_CHARS`` 字；
    - ``evidence``：至少 1 条，逐条含非空 ``field`` 字符串与 ``value`` 键。
    """
    if not isinstance(payload, Mapping):
        return None, "分诊响应必须是 JSON 对象"
    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        return None, f"verdict {verdict!r} 不在枚举 {list(VERDICTS)}"
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None, "reason 缺失或非字符串"
    stripped = reason.strip()
    if len(stripped) > MAX_REASON_CHARS:
        return None, f"reason 超长（去首尾空白后 {len(stripped)} 字 > {MAX_REASON_CHARS} 字）"
    evidence = payload.get("evidence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not evidence:
        return None, "evidence 缺失或非非空列表"
    items: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            return None, "evidence 条目必须是 {field, value} 对象"
        field = item.get("field")
        if not isinstance(field, str) or not field.strip():
            return None, "evidence 条目缺非空 field"
        if "value" not in item:
            return None, "evidence 条目缺 value"
        items.append({"field": field.strip(), "value": item.get("value")})
    return (str(verdict), stripped, items), None


def _render_value(value: Any) -> str:
    """字段取值渲染：None → 显式「缺失」占位（不折算 0/不编造）。"""
    if value is None:
        return "缺失"
    return str(value)


def build_triage_prompt(
    forecast: Mapping[str, Any], *, snapshot_ctx: Mapping[str, Any] | None
) -> str:
    """分诊 prompt 组装：渲染 ``event_triage.md``（纯函数，渲染确定）。

    注入预告要素全量（``FORECAST_INPUT_FIELDS``）+ 快照要素余量
    （``SNAPSHOT_INPUT_FIELDS`` 前 ``MAX_INPUT_FIELDS - 7`` 个），逐行 ``- key: value``；
    超预算的快照键不渲染（输入预算 ≤ 12 字段，常量集中定义）。
    """
    template = load_prompt(PROMPT_NAME)
    lines = [f"- {name}: {_render_value(forecast.get(name))}" for name in FORECAST_INPUT_FIELDS]
    snapshot_ctx = snapshot_ctx or {}
    budget = MAX_INPUT_FIELDS - len(FORECAST_INPUT_FIELDS)
    for name in SNAPSHOT_INPUT_FIELDS[:budget]:
        if name in snapshot_ctx:
            lines.append(f"- {name}: {_render_value(snapshot_ctx.get(name))}")
    return template.replace("{{forecast_fields}}", "\n".join(lines))


def _client_model_label(client: Any) -> str | None:
    """模型标识：优先最近一次调用的 ``last_model``（gateway 真实路由结果），其次静态标签。"""
    last = getattr(client, "last_model", None)
    if isinstance(last, str) and last:
        return last
    label = getattr(client, "model_label", None)
    return label if isinstance(label, str) and label else None


def _degraded_result(error_type: str, message: str) -> tuple[str, str, list[dict[str, Any]]]:
    """降级结果：固定 in_line 文案 + evidence 标记（可识别、可后续重分诊）。"""
    return (
        DEGRADED_VERDICT,
        DEGRADED_REASON,
        [{"field": "degraded", "value": {"error": error_type, "message": message[:200]}}],
    )


def _triage_one(
    forecast: Mapping[str, Any],
    *,
    client: Any,
    snapshot_ctx: Mapping[str, Any] | None,
) -> tuple[tuple[str, str, list[dict[str, Any]]], dict[str, Any] | None]:
    """单条：组装 → 调用 → 合同校验；抛异常/违约一律降级（不向外抛，不阻断批次）。"""
    metadata: dict[str, Any] = {
        "purpose_tag": PURPOSE_TAG,
        "code": forecast.get("code"),
        "report_period": forecast.get("report_period"),
    }
    try:
        # prompt 组装也在降级面内（模板缺失/渲染异常同样按 in_line 降级，不阻断批次）
        prompt = build_triage_prompt(forecast, snapshot_ctx=snapshot_ctx)
        payload = client.complete_json(prompt, purpose=PURPOSE_TAG, metadata=metadata)
    except Exception as exc:  # noqa: BLE001 -- 单条失败按 in_line 降级，不阻断批次
        error_type = type(exc).__name__
        return _degraded_result(error_type, str(exc)), {
            "code": forecast.get("code"),
            "error": error_type,
            "reason": str(exc)[:200],
        }
    contract, error = validate_triage_payload(payload)
    if contract is None:
        return _degraded_result("contract_violation", error or "合同校验失败"), {
            "code": forecast.get("code"),
            "error": "contract_violation",
            "reason": error,
        }
    return contract, None


def _emit_trace_metadata(metadata: dict[str, Any]) -> None:
    """best-effort：批次 trace metadata（含 warnings）写入当前 Langfuse span。"""
    if get_langfuse() is None:
        logger.debug("Langfuse 未配置：分诊批次 trace metadata 仅返回、不发射")
        return
    try:
        update_current_span(metadata={"event_triage": metadata})
    except Exception:  # noqa: BLE001 -- 观测失败不阻断业务
        logger.warning("分诊批次 trace metadata 发射失败", exc_info=True)


def triage_forecasts(
    rows: Sequence[Mapping[str, Any]],
    *,
    client: Any,
    db_path: str | Any = None,
    as_of: str | None = None,
    snapshot_ctx_by_key: Mapping[str, Mapping[str, Any]] | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, Any]:
    """分诊批次入口：并发逐条 → 合同校验/降级 → 落库 → 返回摘要。

    - ``as_of`` 给定：``available_at > as_of`` 的行跳过（时点纪律防御，计入 ``skipped``）；
    - ``snapshot_ctx_by_key``：调用方注入的**逐行**快照要素，键 = ``forecast_key(row)``
      （调用方按各行 ``available_at`` 时点化取数；缺 → 该条无快照字段）；
    - 结果按输入序归位；落库在主线程顺序执行（单连接逐条 UPSERT，幂等）；
    - **全批降级 fail-loud**（评审 I3）：``degraded == triaged 且 triaged > 0``
      （模板系统性缺失/网关整体不可用等）→ 抛 ``RuntimeError``，批次按失败处置——
      与 ``screening/pipeline`` 的 L3 全降级护栏同纪律（防「全 in_line 兜底」冒充成功）；
      单条偶发失败仍降级 ``in_line``、不阻断批次（既有语义不变）；
    - 返回 ``{"triaged", "skipped", "outperform", "in_line", "risk", "degraded",
      "model", "prompt_fingerprint", "warnings"}``。
    """
    if max_workers < 1:
        raise ValueError(f"max_workers 必须是正整数: {max_workers!r}")
    snapshot_ctx_by_key = snapshot_ctx_by_key or {}
    pending: list[Mapping[str, Any]] = []
    skipped = 0
    for row in rows:
        available_at = str(row.get("available_at") or "")
        if as_of is not None and available_at and available_at > as_of:
            skipped += 1  # 时点截断：公告日晚于判断日的记录不进分诊（MUST NOT 前视）
            continue
        pending.append(row)
    prompts_ctx = [snapshot_ctx_by_key.get(forecast_key(row)) for row in pending]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_triage_one, row, client=client, snapshot_ctx=ctx)
            for row, ctx in zip(pending, prompts_ctx, strict=True)
        ]
        outcomes = [future.result() for future in futures]  # 输入序收集

    info = load_prompt_with_meta(PROMPT_NAME)
    prompt_fingerprint = f"{info.prompt_name}@{info.prompt_version}"
    counts = {"outperform": 0, "in_line": 0, "risk": 0}
    warnings: list[dict[str, Any]] = []
    for row, (contract, warning) in zip(pending, outcomes, strict=True):
        verdict, reason, evidence = contract
        counts[verdict] = counts.get(verdict, 0) + 1
        if warning is not None:
            warnings.append(warning)
            logger.warning(
                "分诊降级 in_line: code=%s error=%s reason=%s",
                warning.get("code"),
                warning.get("error"),
                warning.get("reason"),
            )
        try:
            record_triage(
                forecast_key(row),
                verdict=verdict,
                reason=reason,
                evidence=evidence,
                model=_client_model_label(client),
                prompt_fingerprint=prompt_fingerprint,
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001 -- 单条落库失败不阻断批次（其余照常产出）
            logger.exception("分诊结果落库失败: code=%s", row.get("code"))
            warnings.append(
                {"code": row.get("code"), "error": "persist_error", "reason": "分诊结果落库失败"}
            )
    result: dict[str, Any] = {
        "triaged": len(pending),
        "skipped": skipped,
        **counts,
        "degraded": len(
            [warning for warning in warnings if warning.get("error") != "persist_error"]
        ),
        "model": _client_model_label(client),
        "prompt_fingerprint": prompt_fingerprint,
        "warnings": warnings,
    }
    # 全批降级 = 系统性故障（模板缺失/网关整体不可用）→ fail-loud（评审 I3，照
    # screening/pipeline L3 护栏先例）：MUST NOT 以「全 in_line 兜底」冒充成功；
    # 单条偶发失败（degraded < triaged）仍按降级语义返回，不阻断批次。
    if result["triaged"] > 0 and result["degraded"] == result["triaged"]:
        raise RuntimeError(
            f"事件分诊全部降级（{result['degraded']}/{result['triaged']}），"
            "疑似分诊模板系统性缺失或 LLM 网关整体不可用：批次按 failed 处理"
        )
    _emit_trace_metadata(result)
    return result
