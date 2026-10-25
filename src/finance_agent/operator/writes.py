"""快速模式受控写操作工具（P3）。

统一两步确认：第一次只预览 + token；用户明确确认后第二次才执行。
业务动作全部复用既有服务函数，不在这里写第二套实现。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from finance_agent.operator.confirmation import consume_confirmation, issue_confirmation

_logger = logging.getLogger("finance_agent.operator.writes")


def _ingest_document(
    *,
    filename: str,
    data: bytes,
    overrides: dict | None = None,
    extract: bool = True,
) -> dict:
    """共享直投入库服务间接层（add-research-report-ingest；测试打桩点）。"""
    from finance_agent.data.research_reports import ingest_research_document

    return ingest_research_document(filename, data, overrides=overrides, extract=extract)


_BACKGROUND_TASKS: set[asyncio.Task] = set()
_PREVIEW_INSTRUCTION = (
    "请把预览摘要转述给用户；用户明确回复「确认执行」后，再以 confirm=true "
    "调用本工具（token 可省略）。不要自行确认，也不要在用户确认前执行。"
)


# ── 默认服务适配（测试可 monkeypatch 这些模块级函数） ──


async def _factor_control(desired_running: bool) -> dict[str, Any]:
    from finance_agent.api import _factor_research_control_update

    return await asyncio.to_thread(_factor_research_control_update, desired_running)


async def _run_job(job_id: str) -> dict[str, Any]:
    from finance_agent.outcome.ops.jobs import run_job

    return await asyncio.to_thread(run_job, job_id, source="manual")


async def _list_clauses() -> list[dict[str, Any]]:
    from finance_agent.memory.semantic import list_clauses

    return await asyncio.to_thread(list_clauses, None)


async def _set_clause(clause_id: str, action: str) -> bool:
    from finance_agent.memory.semantic import confirm_clause, reject_clause

    fn = confirm_clause if action == "confirm" else reject_clause
    return await asyncio.to_thread(fn, clause_id)


async def _pool_payload() -> dict[str, Any]:
    from finance_agent.api import screening_pool

    return await screening_pool()


async def _pool_analyze() -> Any:
    from finance_agent.api import screening_analyze

    return await screening_analyze()


def make_write_tools(
    *,
    session_id: str,
    user_message: str,
) -> dict[str, Callable[..., Awaitable[str]]]:
    """创建全部写工具（名称与 tool_registry 常量一致）。"""

    def _audit(action: str, params: dict[str, Any], result: str) -> None:
        _logger.info(
            "operator_write session_id=%s action=%s params=%s result=%s",
            session_id,
            action,
            json.dumps(params, ensure_ascii=False, sort_keys=True),
            result,
        )

    def _preview(action: str, params: dict[str, Any], summary: str) -> str:
        token, ttl = issue_confirmation(
            session_id=session_id, action=action, params=params, summary=summary
        )
        return json.dumps(
            {
                "ok": True,
                "status": "confirmation_required",
                "preview": summary,
                "token": token,
                "expires_in": int(ttl),
                "instruction": _PREVIEW_INSTRUCTION,
            },
            ensure_ascii=False,
        )

    def _success(action: str, params: dict[str, Any], payload: dict[str, Any]) -> str:
        _audit(action, params, "ok")
        return json.dumps({"ok": True, **payload}, ensure_ascii=False)

    def _failure(action: str, params: dict[str, Any], message: str) -> str:
        _audit(action, params, f"error: {message}")
        return json.dumps({"ok": False, "error": message}, ensure_ascii=False)

    def _confirmed(action: str, params: dict[str, Any], token: str) -> tuple[bool, str]:
        return consume_confirmation(
            session_id=session_id,
            token=token,
            action=action,
            params=params,
            user_message=user_message,
        )

    async def set_factor_research_running(
        desired_running: bool, confirm: bool = False, token: str = ""
    ) -> str:
        """设置因子研发看护的期望运行状态（写操作，需两步确认）

        第一次调用只返回预览与确认提示；用户明确回复「确认执行」后，再以
        confirm=true 调用。执行后回执包含 pending / honor / honor_eta_seconds：
        这只是写入期望态，真正启停由 distro 侧研发代理调和，不承诺立即生效。

        Args:
            desired_running: true=希望看护运行；false=希望停止
            confirm: 是否已获得用户确认（首次调用必须为 false）
            token: 首次预览返回的令牌（可省略，服务端按会话与参数匹配）
        """
        action = "set_factor_research_running"
        params = {"desired_running": bool(desired_running)}
        want = "运行" if desired_running else "停止"
        summary = (
            f"将把因子研发看护的期望态设为「{want}」。"
            "这只是写入期望态，真正启停由研发代理调和，可能延迟；不会立即杀进程。"
        )
        if not confirm:
            return _preview(action, params, summary)
        ok, reason = _confirmed(action, params, token)
        if not ok:
            return _failure(action, params, reason)
        try:
            result = await _factor_control(bool(desired_running))
        except HTTPException as exc:
            if exc.status_code == 409:
                return _failure(
                    action,
                    params,
                    "control_unavailable: 当前无受理能力（快照与启动器都不可用），未写控制文件",
                )
            return _failure(action, params, f"HTTP {exc.status_code}: {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            return _failure(action, params, f"{type(exc).__name__}: {exc}")
        control = (result or {}).get("control") or {}
        return _success(
            action,
            params,
            {
                "desired_running": control.get("desired_running"),
                "pending": control.get("pending"),
                "honor": control.get("honor"),
                "honor_eta_seconds": control.get("honor_eta_seconds"),
            },
        )

    async def run_ops_job(job_id: str, confirm: bool = False, token: str = "") -> str:
        """手动补跑一个日批任务（写操作，需两步确认；后台执行）

        只接受已知 job_id。第一次调用只返回预览；用户确认后任务在后台启动，
        工具立即返回回执，运行结果用 get_daily_ops_status 查询。

        Args:
            job_id: 任务 ID，如 factor_harvest_daily
            confirm: 是否已获得用户确认（首次调用必须为 false）
            token: 首次预览返回的令牌（可省略）
        """
        from finance_agent.outcome.ops.jobs import JOB_IDS, JOB_LABELS

        action = "run_ops_job"
        jid = (job_id or "").strip()
        if jid not in JOB_IDS:
            return _failure(action, {"job_id": jid}, f"未知 job_id: {jid!r}")
        params = {"job_id": jid}
        label = JOB_LABELS.get(jid, jid)
        summary = (
            f"将手动补跑「{label}」({jid})。任务在后台执行，可能耗时并消耗资源；"
            "工具会立即返回回执，结果用 get_daily_ops_status 查询。"
        )
        if not confirm:
            return _preview(action, params, summary)
        ok, reason = _confirmed(action, params, token)
        if not ok:
            return _failure(action, params, reason)

        task = asyncio.create_task(_run_job(jid))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        await asyncio.sleep(0.2)
        if task.done():
            if task.cancelled():
                return _success(action, params, {"job_id": jid, "status": "triggered"})
            exc = task.exception()
            if exc is not None:
                if type(exc).__name__ == "JobAlreadyRunning":
                    return _failure(action, params, "already_running: 该任务已在运行")
                if type(exc).__name__ == "CohortDisabled":
                    return _failure(action, params, "cohort_disabled: cohort 开关未开启")
                return _failure(action, params, f"{type(exc).__name__}: {exc}")
            result = task.result() or {}
            return _success(
                action,
                params,
                {
                    "job_id": jid,
                    "status": result.get("status"),
                    "run_id": result.get("run_id"),
                    "summary": result.get("summary"),
                    "error": result.get("error"),
                },
            )
        return _success(
            action,
            params,
            {
                "job_id": jid,
                "status": "triggered",
                "note": "后台执行中；用 get_daily_ops_status 查询运行结果",
            },
        )

    async def review_memory_clause(
        clause_id: str, action: str = "confirm", confirm: bool = False, token: str = ""
    ) -> str:
        """确认或驳回一条语义记忆条款（写操作，需两步确认）

        第一次调用只返回预览（含条款文本）；用户确认后执行。action 只接受
        confirm / reject；未知条款 ID 会明确报错。

        Args:
            clause_id: 条款 ID
            action: confirm=确认（此后参与风控注入）；reject=驳回（永不注入）
            confirm: 是否已获得用户确认（首次调用必须为 false）
            token: 首次预览返回的令牌（可省略）
        """
        mode = (action or "").strip().lower()
        cid = (clause_id or "").strip()
        audit_action = f"review_memory_clause:{mode or 'unknown'}"
        if mode not in ("confirm", "reject"):
            return _failure(audit_action, {"clause_id": cid}, "action 必须是 confirm 或 reject")
        try:
            rows = await _list_clauses()
        except Exception as exc:  # noqa: BLE001
            return _failure(audit_action, {"clause_id": cid}, f"{type(exc).__name__}: {exc}")
        row = next((item for item in rows if str(item.get("clause_id")) == cid), None)
        if row is None:
            return _failure(audit_action, {"clause_id": cid}, f"未知条款: {cid}")
        params = {"clause_id": cid, "action": mode}
        text = str(row.get("text") or "")[:120]
        target = "confirmed" if mode == "confirm" else "rejected"
        summary = f"将把条款 {cid} 标为 {target}。条款内容：{text}"
        if not confirm:
            return _preview(audit_action, params, summary)
        ok, reason = _confirmed(audit_action, params, token)
        if not ok:
            return _failure(audit_action, params, reason)
        try:
            changed = await _set_clause(cid, mode)
        except Exception as exc:  # noqa: BLE001
            return _failure(audit_action, params, f"{type(exc).__name__}: {exc}")
        if not changed:
            return _failure(audit_action, params, f"条款状态变更失败或未知条款: {cid}")
        return _success(audit_action, params, {"id": cid, "status": target})

    async def start_pool_analysis(confirm: bool = False, token: str = "") -> str:
        """对当前精选池批量发起深度分析（写操作，需两步确认）

        第一次调用返回池大小预览；用户确认后复用既有选股分析队列（并发上限 2）。
        空池会明确报错，不会静默返回空队列。

        Args:
            confirm: 是否已获得用户确认（首次调用必须为 false）
            token: 首次预览返回的令牌（可省略）
        """
        action = "start_pool_analysis"
        params: dict[str, Any] = {}
        try:
            payload = await _pool_payload()
        except Exception as exc:  # noqa: BLE001
            return _failure(action, params, f"{type(exc).__name__}: {exc}")
        pool = payload.get("pool") or []
        if not pool:
            return _failure(action, params, "精选池为空：无可分析的标的（先跑选股日批）")
        summary = (
            f"将对精选池 {len(pool)} 只标的批量发起深度分析；"
            "复用既有分析队列（并发上限 2），可能消耗 LLM 配额。"
        )
        if not confirm:
            return _preview(action, params, summary)
        ok, reason = _confirmed(action, params, token)
        if not ok:
            return _failure(action, params, reason)
        try:
            result = await _pool_analyze()
        except Exception as exc:  # noqa: BLE001
            return _failure(action, params, f"{type(exc).__name__}: {exc}")
        if isinstance(result, JSONResponse):
            raw = result.body
            if isinstance(raw, memoryview):
                raw = raw.tobytes()
            try:
                body = json.loads(raw)
            except Exception:  # noqa: BLE001
                body = {}
            message = body.get("message") or body.get("error") or "批量分析请求被拒绝"
            return _failure(action, params, str(message))
        if not isinstance(result, dict):
            return _failure(action, params, "批量分析返回了非预期结果")
        return _success(
            action,
            params,
            {
                "pool_size": len(pool),
                "started": result.get("started"),
                "queued": result.get("queued"),
                "removed": result.get("removed"),
            },
        )

    async def ingest_research_report(
        text: str,
        title: str = "",
        org: str = "",
        publish_date: str = "",
        filename: str = "",
        confirm: bool = False,
        token: str = "",
    ) -> str:
        """把一段研报正文直投入库（写操作，需两步确认）。

        文本经共享入库服务写入研报库：内容哈希幂等（重复投喂回执 skipped），
        同时落盘一份到投喂目录。不接受 URL 参数（不做网络抓取）。

        Args:
            text: 研报正文（必填非空）
            title/org/publish_date: 可选元数据，显式提供时优先于文件名推断
            filename: 可选文件名（影响元数据推断），缺省按时间戳生成
            confirm: 是否已获得用户确认（首次调用必须为 false）
            token: 首次预览返回的令牌（可省略，服务端按会话与参数匹配）
        """
        action = "ingest_research_report"
        clean_text = str(text or "")
        if not clean_text.strip():
            return _failure(action, {}, "empty_text: 请提供研报正文内容（不接受空文本）")
        digest = hashlib.sha1(clean_text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        meta = {
            key: value
            for key, value in {
                "title": title,
                "org": org,
                "publish_date": publish_date,
                "filename": filename,
            }.items()
            if value
        }
        params: dict[str, Any] = {"text_sha1": digest, "text_chars": len(clean_text), **meta}
        summary = (
            f"将把一段 {len(clean_text)} 字的研报正文入库；"
            "内容哈希幂等（重复投喂返回 skipped），全文仅个人研究自用。"
        )
        if not confirm:
            return _preview(action, params, summary)
        ok, reason = _confirmed(action, params, token)
        if not ok:
            return _failure(action, params, reason)
        fname = str(filename or "").strip() or (
            f"对话投喂_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if not Path(fname).suffix:
            fname += ".txt"
        overrides = {
            key: value
            for key, value in {
                "title": title,
                "org": org,
                "publish_date": publish_date,
            }.items()
            if value
        }
        try:
            receipt = await asyncio.to_thread(
                _ingest_document,
                filename=fname,
                data=clean_text.encode("utf-8"),
                overrides=overrides,
                extract=True,
            )
        except ValueError as exc:
            return _failure(action, params, str(exc))
        except Exception as exc:  # noqa: BLE001
            return _failure(action, params, f"{type(exc).__name__}: {exc}")
        payload: dict[str, Any] = {
            "status": receipt.get("status"),
            "doc_key": receipt.get("doc_key"),
            "char_count": receipt.get("char_count"),
        }
        if receipt.get("status") == "ingested":
            payload["report_key"] = receipt.get("report_key")
            payload["depth_class"] = receipt.get("depth_class")
            if receipt.get("parse_error"):
                payload["parse_error"] = receipt["parse_error"]
            if receipt.get("extraction_error"):
                payload["extraction_error"] = receipt["extraction_error"]
        return _success(action, params, payload)

    return {
        "set_factor_research_running": set_factor_research_running,
        "run_ops_job": run_ops_job,
        "review_memory_clause": review_memory_clause,
        "start_pool_analysis": start_pool_analysis,
        "ingest_research_report": ingest_research_report,
    }
