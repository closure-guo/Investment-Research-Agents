"""因子收割桥（add-factor-harvest-bridge）：RD-Agent 产出 → 转译 → 同一五道闸。

链路（全自动，日批；供 `factor_harvest_daily` 零参数调用）：

1. **候选源**：distro 采集器写出的候选文件（缺省 ``data/factor_research/candidates.json``，
   经 ``FACTOR_RESEARCH_CANDIDATES_PATH`` 可覆盖）——RD-Agent 因子块
   （``name`` / ``description`` / ``formulation``）；
2. **转译**（LLM，``purpose="factor_transcription"``）：RD-Agent 原生形态 → Qlib Alpha
   表达式，**二值裁决**（严格等价或显式不可翻译；MUST NOT 猜测/近似）；
3. **翻译**（确定性，``factors.qlib_translate`` 映射表可审计）：Qlib → DSL；
   不可翻译/超复杂度逐条留 blocker；
4. **过闸**：``run_factor_external_import``（与外部导入/月批**同一五道闸**，
   ``source="external"``，逐条裁决留事件史含闸口读数 ``detail``）；
5. **幂等 / 重放**：已处理候选记状态文件（``harvest_state.json``，``FACTOR_RESEARCH_HARVEST_STATE_PATH``
   可覆盖），不重复导入；LLM 网关不可用/编排级异常一律上抛（job 记 failed——fail loud，
   MUST NOT 静默跳过或伪造候选）；转译响应**不合载荷合同**（非法 JSON / 缺字段）按
   ``TRANSCRIPTION_MAX_ATTEMPTS`` 重试（网关级异常不重试）；调用方指定 ``retry_statuses``
   时，状态命中该集合的已处理候选**重放**（重新走转译 → 翻译 → 过闸，新裁决覆盖旧记录并
   保留 ``retried_from``）——重放是显式运维动作（``scripts/replay_factor_harvest.py``），
   日批缺省不启用（recover-factor-supply-losses）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from finance_agent.factors import qlib_translate
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.external import run_factor_external_import
from finance_agent.llm.contracts import extract_json
from finance_agent.llm.errors import OutputTruncatedError
from finance_agent.llm.gateway import complete_text
from finance_agent.llm.types import Purpose
from finance_agent.prompts.loader import load_prompt

logger = logging.getLogger(__name__)

# trace 用途标签 / 路由 purpose（PolicyRouter 硬要求见 router.REQUIRED_CAPS）
PURPOSE_TAG: Purpose = "factor_transcription"

DEFAULT_CANDIDATES_PATH = "data/factor_research/candidates.json"
DEFAULT_STATE_PATH = "data/factor_research/harvest_state.json"
# 跨会话记忆桥（生成侧读取；导出失败不影响收割结果）
DEFAULT_FEEDBACK_PATH = "data/factor_research/explored_factors.json"
# 单批上限（闸③ BH-FDR 分辨率覆盖 K ≤ 100；留裕度，超量候选顺延下一批）
DEFAULT_BATCH_LIMIT = 60
# 单轮日批最多串行几批（每批独立 BH-FDR 家族，≤100 分辨率约束不变）；防单次运行无限拉长
DEFAULT_MAX_BATCHES_PER_RUN = 12
# 聚合摘要里保留的拒收明细上限（job_runs 摘要体积保护）
MAX_AGGREGATED_REJECTIONS = 100
DEFAULT_CATEGORY = "外部"
# 转译合同失败重试上限（共 3 次尝试；与「提案输出合同」修复重试上限同款——
# recover-factor-supply-losses：2026-10-19 实测 2 条候选因 LLM 响应非 JSON 一次性落账）
TRANSCRIPTION_MAX_ATTEMPTS: int = 3


class TranscriptionResponseError(ValueError):
    """转译响应不合载荷合同（非法 JSON / 缺字段）。"""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class TranscriptionLLMClient(Protocol):
    """转译 LLM 客户端协议（stub 可替，单测零网络）。"""

    def complete_json(
        self, prompt: str, *, purpose: Purpose, metadata: dict[str, Any]
    ) -> dict[str, Any]: ...


class GatewayTranscriptionClient:
    """默认客户端：gateway ``complete_text`` 薄适配（唯一生产 LLM 入口）。"""

    def __init__(self, *, max_tokens: int | None = None) -> None:
        self._max_tokens = max_tokens

    def complete_json(
        self, prompt: str, *, purpose: Purpose, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        text, _meta = complete_text(
            [{"role": "user", "content": prompt}],
            purpose=purpose,
            max_tokens=self._max_tokens,
            trace={"name": PURPOSE_TAG, "metadata": metadata},
        )
        try:
            return extract_json(text)
        except json.JSONDecodeError as exc:
            raise TranscriptionResponseError(
                f"转译响应不是合法 JSON：{exc}", raw_text=text
            ) from exc


@dataclass(frozen=True)
class Transcription:
    """二值裁决：``qlib_expression`` 非空 XOR ``reason`` 非空。"""

    qlib_expression: str | None
    reason: str | None


def parse_transcription_payload(payload: Mapping[str, Any]) -> Transcription:
    """载荷校验：``{"qlib_expression": str}`` 或 ``{"untranslatable": true, "reason": str}``。"""
    if payload.get("untranslatable") is True:
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("untranslatable 载荷缺 reason（MUST 显式给出拒译原因）")
        return Transcription(qlib_expression=None, reason=reason)
    expression = payload.get("qlib_expression")
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("转译载荷须含非空 qlib_expression 或 untranslatable=true")
    return Transcription(qlib_expression=expression.strip(), reason=None)


def build_transcription_prompt(candidate: Mapping[str, Any]) -> str:
    """组装转译 prompt（模板 ``factor_transcription``，占位符 ``{{factor_block}}``）。"""
    block = (
        f"factor_name: {candidate.get('name')}\n"
        f"factor_description: {candidate.get('description')}\n"
        f"factor_formulation: {candidate.get('formulation')}"
    )
    return load_prompt("factor_transcription").replace("{{factor_block}}", block)


def transcribe_candidate(
    candidate: Mapping[str, Any], client: TranscriptionLLMClient
) -> Transcription:
    """单条候选转译（LLM）→ 二值裁决；响应不合合同 → ``TranscriptionResponseError``。

    载荷合同失败（``parse_transcription_payload`` 的 ``ValueError``）在本函数边界
    归一为 ``TranscriptionResponseError``——重试面因此精确等于「模型输出不合合同」，
    **不吞**网关/配置侧 ``ValueError`` 家族（见 ``_transcribe_with_retry``）。
    """
    name = str(candidate.get("name"))
    prompt = build_transcription_prompt(candidate)
    payload = client.complete_json(
        prompt,
        purpose=PURPOSE_TAG,
        metadata={"factor_name": name, "source": "rdagent_harvest"},
    )
    try:
        return parse_transcription_payload(payload)
    except ValueError as exc:
        raise TranscriptionResponseError(str(exc), raw_text="") from exc


class _ContractExhaustedError(Exception):
    """转译合同失败重试耗尽（携带实际尝试次数与原始异常，供审计留痕）。"""

    def __init__(self, attempts: int, cause: Exception) -> None:
        super().__init__(str(cause))
        self.attempts = attempts
        self.cause = cause


def _recorded_status(entry: Any) -> str | None:
    """状态文件条目 → 已记录状态字符串（非 dict / 缺 status → ``None``，防御手改半截文件）。"""
    if isinstance(entry, dict) and entry.get("status") is not None:
        return str(entry["status"])
    return None


def _with_retried_from(verdict: dict[str, Any], previous_status: str | None) -> dict[str, Any]:
    """重放审计：重放产生的新裁决保留来源状态（``retried_from``）。"""
    if previous_status is not None:
        verdict["retried_from"] = previous_status
    return verdict


def _transcribe_with_retry(
    candidate: Mapping[str, Any], client: TranscriptionLLMClient
) -> tuple[Transcription, int]:
    """转译（含合同失败重试）→ ``(裁决, 尝试次数)``。

    重试面**仅** ``TranscriptionResponseError``（非法 JSON / 载荷合同缺字段，见
    ``transcribe_candidate``）——「模型输出不合合同」类故障，退避不做（转译调用廉价、
    次数有界）。``OutputTruncatedError`` 由调用方按单候选 ``transcription_error``
    留痕（不阻断批次）；其余异常（LLM 网关不可用类 ``LLMError``、resolver 配置类
    ``ValueError`` 子类）不在捕获面内：直接上抛，维持「网关不可用 fail loud」语义。
    重试耗尽 → 抛 ``_ContractExhaustedError``（携带实际次数；调用方留痕）。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return transcribe_candidate(candidate, client), attempt
        except TranscriptionResponseError as exc:
            if attempt >= TRANSCRIPTION_MAX_ATTEMPTS:
                raise _ContractExhaustedError(attempt, exc) from exc
            logger.warning(
                "transcription contract failure (attempt %s/%s) factor=%s: %s",
                attempt,
                TRANSCRIPTION_MAX_ATTEMPTS,
                candidate.get("name"),
                exc,
            )


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _refresh_explored_feedback(state_path: Path, candidates_path: Path) -> None:
    """刷新跨会话记忆桥（best-effort：失败只告警，不影响收割结果）。"""
    try:
        from finance_agent.factors.feedback import export_feedback

        target = os.environ.get("FACTOR_FEEDBACK_PATH", DEFAULT_FEEDBACK_PATH)
        export_feedback(
            state_path=state_path,
            candidates_path=candidates_path,
            output_path=target,
        )
    except Exception:  # noqa: BLE001 - 记忆桥是附加产物，不得让收割批失败
        logger.warning("explored feedback export failed", exc_info=True)


def _candidates_path(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("FACTOR_RESEARCH_CANDIDATES_PATH", DEFAULT_CANDIDATES_PATH))


def _state_path(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("FACTOR_RESEARCH_HARVEST_STATE_PATH", DEFAULT_STATE_PATH))


def run_factor_harvest(
    *,
    candidates_path: str | Path | None = None,
    state_path: str | Path | None = None,
    client: TranscriptionLLMClient | None = None,
    panel: MarketPanel | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
    retry_statuses: Collection[str] | None = None,
) -> dict[str, Any]:
    """收割批入口（job 零参数调用）：候选文件 → 转译 → 翻译 → 五道闸 → 状态回填。

    无新候选 → 零摘要（非错误）。LLM 网关不可用/面板不可得 → 上抛（fail loud）。

    ``retry_statuses``：按状态重放（recover-factor-supply-losses）——状态文件中
    已处理候选的状态命中该集合时重新进入本批（新裁决覆盖旧记录并保留
    ``retried_from``）；重放候选先于未处理候选进入限批截断。缺省 ``None`` =
    现幂等行为（已处理候选不重复导入），日批 job 即该形态。
    """
    cand_file = _candidates_path(candidates_path)
    st_file = _state_path(state_path)
    raw_candidates = _load_json(cand_file).get("candidates")
    all_candidates = (
        [c for c in raw_candidates if isinstance(c, dict) and c.get("name")]
        if isinstance(raw_candidates, list)
        else []
    )
    processed: dict[str, Any] = dict(_load_json(st_file).get("processed") or {})
    retry_set = set(retry_statuses or ())
    replay = (
        [
            candidate
            for candidate in all_candidates
            if _recorded_status(processed.get(str(candidate.get("name")))) in retry_set
        ]
        if retry_set
        else []
    )
    fresh = [c for c in all_candidates if str(c.get("name")) not in processed]
    pending = (replay + fresh)[: max(limit, 0)]
    replay_names = {str(c.get("name")) for c in replay}

    summary: dict[str, Any] = {
        "proposed": len(pending),
        "replayed": sum(1 for c in pending if str(c.get("name")) in replay_names),
        "transcribed": 0,
        "untranslatable": 0,
        "transcription_errors": 0,
        "admitted": 0,
        "rejected": 0,
        "errors": 0,
        "rejections": [],
    }
    if not pending:
        return summary

    llm = client if client is not None else GatewayTranscriptionClient()
    payload: list[dict[str, Any]] = []
    verdicts: dict[str, dict[str, Any]] = {}

    for candidate in pending:
        name = str(candidate["name"])
        description = str(candidate.get("description") or "").strip()
        formulation = str(candidate.get("formulation") or "").strip()
        previous_status = _recorded_status(processed.get(name)) if name in replay_names else None

        try:
            transcription, attempts = _transcribe_with_retry(candidate, llm)
        except OutputTruncatedError as exc:
            # 单候选输出被截断（思考模型 reasoning 吃满输出预算）→ 留痕后继续，
            # 不按网关不可用处理（RateLimit/连接类仍 fail loud）。状态命中
            # transcription_error 可经 retry_statuses 重放。
            logger.warning("transcription output truncated factor=%s: %s", name, exc)
            verdicts[name] = _with_retried_from(
                {
                    "status": "transcription_error",
                    "detail": f"输出截断：{exc}",
                    "attempts": 1,
                    "at": _now(),
                },
                previous_status,
            )
            payload.append(
                {
                    "expression": formulation,
                    "hypothesis": (f"[RD-Agent/{name}] 转译失败：输出截断（模型输出预算耗尽）"),
                    "category": DEFAULT_CATEGORY,
                }
            )
            continue
        except _ContractExhaustedError as exc:
            verdicts[name] = _with_retried_from(
                {
                    "status": "transcription_error",
                    "detail": str(exc.cause),
                    "attempts": exc.attempts,
                    "at": _now(),
                },
                previous_status,
            )
            payload.append(
                {
                    "expression": formulation,
                    "hypothesis": f"[RD-Agent/{name}] 转译失败：{exc.cause}",
                    "category": DEFAULT_CATEGORY,
                }
            )
            continue
        if transcription.qlib_expression is None:
            verdicts[name] = _with_retried_from(
                {
                    "status": "untranslatable",
                    "stage": "llm",
                    "detail": transcription.reason,
                    "attempts": attempts,
                    "at": _now(),
                },
                previous_status,
            )
            payload.append(
                {
                    "expression": formulation,
                    "hypothesis": f"[RD-Agent/{name}] 不可翻译（LLM）：{transcription.reason}",
                    "category": DEFAULT_CATEGORY,
                }
            )
            continue
        qlib_expression = transcription.qlib_expression
        translated = qlib_translate.translate_qlib_expression(qlib_expression)
        if not translated.translatable:
            verdicts[name] = _with_retried_from(
                {
                    "status": "untranslatable",
                    "stage": "translator",
                    "detail": "；".join(translated.blockers),
                    "qlib_expression": qlib_expression,
                    "attempts": attempts,
                    "at": _now(),
                },
                previous_status,
            )
            payload.append(
                {
                    "expression": qlib_expression,  # 预检失败 → errors 桶（gate=None）
                    "hypothesis": (
                        f"[RD-Agent/{name}] 不可翻译（确定性映射）："
                        f"{'；'.join(translated.blockers)}"
                    ),
                    "category": DEFAULT_CATEGORY,
                }
            )
            continue
        verdicts[name] = _with_retried_from(
            {
                "status": "pending_gate",
                "expression": translated.dsl_expression,
                "qlib_expression": qlib_expression,
                "attempts": attempts,
                "at": _now(),
            },
            previous_status,
        )
        payload.append(
            {
                "expression": translated.dsl_expression,
                "hypothesis": (
                    f"[RD-Agent/{name}] {description}；formulation: {formulation}；"
                    f"转译 {qlib_expression} → {translated.dsl_expression}"
                ),
                "category": DEFAULT_CATEGORY,
            }
        )

    summary["transcribed"] = sum(1 for v in verdicts.values() if v["status"] == "pending_gate")
    summary["untranslatable"] = sum(1 for v in verdicts.values() if v["status"] == "untranslatable")
    summary["transcription_errors"] = sum(
        1 for v in verdicts.values() if v["status"] == "transcription_error"
    )

    if payload:
        import_summary = run_factor_external_import(
            payload,
            panel=panel,
            db_path=db_path,
            snapshot_db_path=snapshot_db_path,
            ledger_source="harvest",
        )
        summary["admitted"] = import_summary["admitted"]
        summary["rejected"] = import_summary["rejected"]
        summary["errors"] = import_summary["errors"]
        summary["rejections"] = import_summary["rejections"]
        rejected_by_expression = {str(r.get("expression")): r for r in import_summary["rejections"]}
        for verdict in verdicts.values():
            if verdict["status"] != "pending_gate":
                continue
            rejection = rejected_by_expression.get(str(verdict["expression"]))
            if rejection is None:
                verdict["status"] = "admitted"
            else:
                verdict["status"] = "rejected"
                verdict["gate"] = rejection.get("gate")
                verdict["reason"] = rejection.get("reason")
                verdict["detail"] = rejection.get("detail")

    for name, verdict in verdicts.items():
        previous = processed.get(name)
        if (
            isinstance(previous, dict)
            and "qlib_expression" not in verdict
            and previous.get("qlib_expression")
        ):
            # 重放覆盖不得丢失历次转译产物：复译链路（确定性翻译重放）依赖状态内的
            # qlib_expression；一次转译失败的重放若抹掉它，该候选只能再烧一次 LLM 调用。
            verdict["qlib_expression"] = previous["qlib_expression"]
    processed.update(verdicts)
    _write_json(st_file, {"updated_at": _now(), "processed": processed})
    _refresh_explored_feedback(st_file, cand_file)
    logger.info(
        "factor harvest: proposed=%s transcribed=%s untranslatable=%s admitted=%s rejected=%s errors=%s",
        summary["proposed"],
        summary["transcribed"],
        summary["untranslatable"],
        summary["admitted"],
        summary["rejected"],
        summary["errors"],
    )
    return summary


def run_factor_harvest_until_drained(
    *,
    max_batches: int = DEFAULT_MAX_BATCHES_PER_RUN,
    limit: int = DEFAULT_BATCH_LIMIT,
    **kwargs: Any,
) -> dict[str, Any]:
    """串行循环收割直到无待处理候选（日批入口：每天一次，内部自动多批清积压）。

    约束不变：每批候选数 ≤ ``limit``（默认 60），即闸③ BH-FDR 的 K ≤ 100 分辨率
    始终成立；多批之间是**独立的检验家族**，不合并 p 值。

    停止条件：``proposed == 0``（已清空当前待处理）或 ``max_batches`` 用尽。
    任一批异常直接上抛（``run_job`` 记 failed；状态文件幂等，下次从断点继续）。
    聚合摘要把拒绝明细截断到 ``MAX_AGGREGATED_REJECTIONS``，避免 job_runs 过大。
    """
    totals: dict[str, Any] = {
        "batches": 0,
        "drained": False,
        "proposed": 0,
        "transcribed": 0,
        "untranslatable": 0,
        "transcription_errors": 0,
        "admitted": 0,
        "rejected": 0,
        "errors": 0,
        "rejections": [],
        "rejections_truncated": False,
    }
    for _ in range(max(1, max_batches)):
        summary = run_factor_harvest(limit=limit, **kwargs)
        if summary["proposed"] == 0:
            totals["drained"] = True
            break
        totals["batches"] += 1
        for key in (
            "proposed",
            "transcribed",
            "untranslatable",
            "transcription_errors",
            "admitted",
            "rejected",
            "errors",
        ):
            totals[key] += int(summary[key])
        for rejection in summary["rejections"]:
            if len(totals["rejections"]) >= MAX_AGGREGATED_REJECTIONS:
                totals["rejections_truncated"] = True
                break
            totals["rejections"].append(rejection)
    return totals
