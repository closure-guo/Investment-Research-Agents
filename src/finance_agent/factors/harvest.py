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
   ``source="external"``，逐条裁决留事件史含闸口读数 ``detail``）；状态回填按导入
   摘要 ``outcomes`` 的**位置**对齐（同批同表达式碰撞时先入库者不被错标）；
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
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from finance_agent.factors import qlib_translate
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.external import run_factor_external_import
from finance_agent.factors.monthly import (  # noqa: PLC2701 - 面板构造口径单一来源（external 同先例）
    _panel_from_snapshot,
)
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
# 批内逐候选心跳（监控页展示实时进度；与状态文件分离，崩溃不污染 processed 语义）
DEFAULT_HEARTBEAT_PATH = "data/factor_research/harvest_heartbeat.json"
# 单批上限（闸③ BH-FDR 分辨率覆盖 K ≤ 100；留裕度，超量候选顺延下一批）
DEFAULT_BATCH_LIMIT = 60
# 单轮日批最多串行几批（每批独立 BH-FDR 家族，≤100 分辨率约束不变）；防单次运行无限拉长
DEFAULT_MAX_BATCHES_PER_RUN = 12
# 聚合摘要里保留的拒收明细上限（job_runs 摘要体积保护）
MAX_AGGREGATED_REJECTIONS = 100
# 机理分桶（update-harvest-factor-category）：有序关键词表，首中即停。顺序刻意：
# 量价交互先于量与流动性/动量反转——标签 "[Price-Volume ...]" 含 "volume"，且
# "volume adjusted reversal" 应归动量反转而非量族。只匹配**首个方括号机理标签**
# + 因子名（正文里的跨族提及不计——实测 volume_autocorr 正文含 "price-volume
# correlation family"，整段匹配会误分类）。未命中落兜底桶（不丢因子、不影响准入）。
CATEGORY_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "量价交互",
        ("price-volume", "price volume", "co-movement", "pv corr", "volume return correlation"),
    ),
    ("动量反转", ("momentum", "reversal", "动量", "反转")),
    ("量与流动性", ("volume", "liquidity", "vol ratio", "amihud", "illiq", "换手", "turnover")),
    ("波动率", ("volatility", "range", "波动")),
)
CATEGORY_FALLBACK = "外部-其他"
_LABEL_RE = re.compile(r"\[([^\]]+)\]")


def _mechanism_text(name: Any, description: Any) -> str:
    """机理标签（首个方括号段）优先 + 因子名；无标签 → 整段描述兜底。

    存量 hypothesis 形态 ``[RD-Agent/名] [机理标签] …``：首段是来源前缀，机理
    标签在第二段——前缀段跳过。
    """
    text = str(description or "")
    labels = _LABEL_RE.findall(text)
    if labels and labels[0].lower().startswith("rd-agent") and len(labels) > 1:
        base = labels[1]
    elif labels:
        base = labels[0]
    else:
        base = text
    return f"{base} {str(name or '')}".lower()


def classify_category(name: Any, description: Any) -> str:
    """候选机理 → 固定桶集（确定性，零 LLM）；未命中/异常输入 → 兜底桶。"""
    text = _mechanism_text(name, description)
    for bucket, keywords in CATEGORY_BUCKETS:
        if any(keyword.lower() in text for keyword in keywords):
            return bucket
    return CATEGORY_FALLBACK
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


def _write_harvest_heartbeat(
    *,
    batch_started_at: str,
    processed_in_batch: int,
    total_in_batch: int,
    phase: str,
) -> None:
    """批内逐候选心跳（best-effort）：供监控页展示实时进度；失败不影响收割裁决。

    与 ``harvest_state.json`` 分离是刻意的：状态文件只在整批结束落盘（崩溃不产生
    半成品 ``pending_gate``），心跳只用于观测，不承载任何裁决语义。
    """
    try:
        _write_json(
            Path(os.environ.get("FACTOR_RESEARCH_HEARTBEAT_PATH", DEFAULT_HEARTBEAT_PATH)),
            {
                "batch_started_at": batch_started_at,
                "processed_in_batch": processed_in_batch,
                "total_in_batch": total_in_batch,
                "phase": phase,
                "updated_at": _now(),
            },
        )
    except OSError:
        logger.debug("harvest heartbeat write failed", exc_info=True)


def _candidates_path(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("FACTOR_RESEARCH_CANDIDATES_PATH", DEFAULT_CANDIDATES_PATH))


def _state_path(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("FACTOR_RESEARCH_HARVEST_STATE_PATH", DEFAULT_STATE_PATH))


def _pending_selection(
    cand_file: Path, st_file: Path, *, limit: int, retry_statuses: Collection[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any], set[str]]:
    """候选文件 × 状态文件 → ``(本批待处理, 已处理表, 重放名集)``。

    选批语义的单一来源：重放（状态命中 ``retry_statuses``）先于未处理候选进入
    限批截断。``run_factor_harvest`` 的选批与 ``run_factor_harvest_until_drained``
    的「是否需要预构面板」预判共用本函数——两处语义必须同源，改动须两处同义。
    """
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
    return pending, processed, {str(c.get("name")) for c in replay}


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
    pending, processed, replay_names = _pending_selection(
        cand_file, st_file, limit=limit, retry_statuses=retry_statuses
    )

    summary: dict[str, Any] = {
        "proposed": len(pending),
        "replayed": sum(1 for c in pending if str(c.get("name")) in replay_names),
        "transcribed": 0,
        "untranslatable": 0,
        "transcription_errors": 0,
        "admitted": 0,
        "rejected": 0,
        "errors": 0,
        # errors 桶构成披露（refine-harvest-errors-caliber，issue #465）：占位 =
        # 本批不可翻译/转译失败（留痕语义不变，已被上两键单列）；real = 真实
        # 预检失败（已转译候选意外未过预检——正常恒 0，>0 是翻译器/编排缺陷信号）
        "errors_breakdown": {"placeholders": 0, "real": 0},
        "rejections": [],
    }
    if not pending:
        return summary

    llm = client if client is not None else GatewayTranscriptionClient()
    payload: list[dict[str, Any]] = []
    verdicts: dict[str, dict[str, Any]] = {}
    # pending_gate 候选 → 其在 payload 中的位置（导入裁决按位置回填，见下）
    pending_payload_index: dict[str, int] = {}
    batch_started_at = _now()
    _write_harvest_heartbeat(
        batch_started_at=batch_started_at,
        processed_in_batch=0,
        total_in_batch=len(pending),
        phase="transcribe",
    )

    for index, candidate in enumerate(pending, start=1):
        _write_harvest_heartbeat(
            batch_started_at=batch_started_at,
            processed_in_batch=index - 1,
            total_in_batch=len(pending),
            phase="transcribe",
        )
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
                    "category": classify_category(name, description),
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
                    "category": classify_category(name, description),
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
                    "category": classify_category(name, description),
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
                    "category": classify_category(name, description),
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
                "category": classify_category(name, description),
            }
        )
        pending_payload_index[name] = len(payload) - 1

    summary["transcribed"] = sum(1 for v in verdicts.values() if v["status"] == "pending_gate")
    summary["untranslatable"] = sum(1 for v in verdicts.values() if v["status"] == "untranslatable")
    summary["transcription_errors"] = sum(
        1 for v in verdicts.values() if v["status"] == "transcription_error"
    )

    _write_harvest_heartbeat(
        batch_started_at=batch_started_at,
        processed_in_batch=len(pending),
        total_in_batch=len(pending),
        phase="gate",
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
        # errors 构成分列（refine-harvest-errors-caliber）：占位条目在导入预检中
        # 必失败（原始公式/Qlib 形态非合法 DSL）→ real 即「已转译候选意外未过
        # 预检」；下截 0 防未来口径漂移把 real 推成负数。
        placeholders = summary["untranslatable"] + summary["transcription_errors"]
        summary["errors_breakdown"] = {
            "placeholders": placeholders,
            "real": max(0, int(import_summary["errors"]) - placeholders),
        }
        # 回填按 ``outcomes`` 的**位置**对齐，MUST NOT 按表达式反查：同批逐字重复的
        # 表达式各占一位（先入库者 admitted、后入库者被闸口拒收）——按表达式反查
        # 会让已入库候选被同表达式的拒收记录错标 rejected，状态文件与反馈桥随之
        # 向生成侧谎报该方向已被拒（RD-Agent 同名重提是记录在案的惯性行为）。
        outcomes = import_summary["outcomes"]
        for name, verdict in verdicts.items():
            if verdict["status"] != "pending_gate":
                continue
            outcome = outcomes[pending_payload_index[name]]
            if outcome is not None and outcome.get("admitted"):
                verdict["status"] = "admitted"
            else:
                verdict["status"] = "rejected"
                if outcome is not None:
                    verdict["gate"] = outcome.get("gate")
                    verdict["reason"] = outcome.get("reason")
                    verdict["detail"] = outcome.get("detail")

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
    _write_harvest_heartbeat(
        batch_started_at=batch_started_at,
        processed_in_batch=len(pending),
        total_in_batch=len(pending),
        phase="done",
    )
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

    面板复用：未显式注入 ``panel`` 且存在待处理候选时，准入面板（500 交易日）
    **每运行只构造一次**并逐批复用——同一次运行内快照数据不变，逐批各建一份
    是纯重复劳动（与单批同一 ``_panel_from_snapshot`` 路径，fail loud 语义不变）；
    无待处理候选（空候选文件/全部已处理）不构造，与单批的零面板语义一致。

    停止条件：``proposed == 0``（已清空当前待处理）或 ``max_batches`` 用尽。
    任一批异常直接上抛（``run_job`` 记 failed；状态文件幂等，下次从断点继续）。
    聚合摘要把拒绝明细截断到 ``MAX_AGGREGATED_REJECTIONS``，避免 job_runs 过大。
    """
    panel = kwargs.pop("panel", None)
    if panel is None:
        cand_file = _candidates_path(kwargs.get("candidates_path"))
        st_file = _state_path(kwargs.get("state_path"))
        has_pending = bool(
            _pending_selection(
                cand_file, st_file, limit=limit, retry_statuses=kwargs.get("retry_statuses")
            )[0]
        )
        if has_pending:
            panel = _panel_from_snapshot(kwargs.get("snapshot_db_path"))
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
        summary = run_factor_harvest(limit=limit, panel=panel, **kwargs)
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
