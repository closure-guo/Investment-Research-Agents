"""FastAPI backend - SSE streaming for 5-layer analysis + sessions + NLP + streaming chat."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, field_validator

_logger = logging.getLogger("finance_agent.api")

load_dotenv()  # 加载 .env，须在 finance_agent 模块导入前执行（llm.py 等在 import 时读取环境变量）

# 默认 LLM 模型（无环境变量 LLM_MODEL 时的回退值）。
# 与 /api/llm-config 占位保持一致；实际管线默认在 agent_factory，后续如需统一另行裁决。
DEFAULT_LLM_MODEL = "deepseek/deepseek-v4-pro"

from finance_agent.data.cache import get_shared_cache  # noqa: E402
from finance_agent.data.monitoring import get_monitor  # noqa: E402
from finance_agent.graph import build_5layer_graph  # noqa: E402
from finance_agent.langfuse_tracing import get_langfuse  # noqa: E402
from finance_agent.llm import LLMConfig  # noqa: E402
from finance_agent.llm.probe_cache import get_probe_cache  # noqa: E402
from finance_agent.pipeline_runner import PipelineRunner, build_layer_tree  # noqa: E402
from finance_agent.session_store import (  # noqa: E402
    append_chat,
    append_session_event,
    clear_all_sessions,
    create_chat_session,
    create_session,
    delete_session,
    get_max_event_seq,
    get_previous_completed_session,
    get_session,
    get_session_trace_id,
    get_terminal_event,
    init_db,
    list_session_events,
    list_sessions,
    rename_session,
    set_pipeline_anchor,
    update_pipeline_snapshot,
    update_pipeline_timelines,
    update_session_for_clarify,
    update_session_report,
    update_session_status,
    upsert_chat,
)
from finance_agent.stream_registry import registry  # noqa: E402
from finance_agent.timeline_builder import apply_chat_event  # noqa: E402


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """启动时清扫悬挂 running 会话：后端重启后 PipelineRunner 内存态已丢失，置 failed 供前端恢复展示。"""
    PipelineRunner.mark_swept_failed()
    # cohort 运维配置引导(delta add-eval-ops-console):env → ops_config 表的一次性种子,
    # 已有表值不覆盖(界面改过的值重启保持)。旁路铁律:引导失败只记 ERROR,不阻断启动。
    from finance_agent.outcome.ops.model import bootstrap_cohort_from_env

    try:
        bootstrap_cohort_from_env()
    except Exception:
        _logger.exception("cohort 运维配置引导失败(忽略,回退 env/默认)")
    # 决策结算日批 scheduler(旁路;TESTING/DECISION_SETTLE_ENABLED=0 返回 None)
    # 旁路铁律:scheduler 任何失败不得影响 API 启动,记 ERROR 降级继续
    from finance_agent.outcome.scheduler import start_scheduler, stop_scheduler

    _scheduler = None
    try:
        _scheduler = start_scheduler()
    except Exception:
        _logger.exception("decision settle scheduler 启动失败,降级继续运行 API")
    try:
        yield
    finally:
        # 旁路铁律:退出时必须尝试关闭 scheduler;关闭失败记 ERROR 不阻断 API 关停
        try:
            stop_scheduler(_scheduler)
        except Exception:
            _logger.exception("decision settle scheduler 关闭失败(忽略)")


app = FastAPI(title="Finance Analysis Agent API", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 测试模式开关（F2：E2E 门禁基础设施）──
# TESTING=1 时注册测试专用端点（/api/test/seed, /api/test/reset），
# 完整 LLM stub 实现推迟到 F3（见 agent_factory._make_llm_client）
TESTING: bool = os.getenv("TESTING") == "1"

graph = build_5layer_graph()

# REPORTS_DIR 支持环境变量覆盖（与 export/service.py 一致）：E2E 门禁 webServer
# 注入 REPORTS_DIR=<tmp 目录> 实现测试隔离，避免 /api/files 扫描生产 reports/
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "reports"))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# 导出文件格式白名单（add-download-center）：列表/下载/删除三类文件接口共用
EXPORT_EXTENSIONS = {".docx", ".pptx", ".pdf", ".md"}


def resolve_reports_path(file_name: str) -> Path:
    """将文件名解析到 REPORTS_DIR 内的绝对路径；越界（路径穿越）抛 400。

    三类文件接口（下载/列表/删除）共用：取 basename 剥离目录成分后，
    resolve 并校验父目录必须是 REPORTS_DIR 本身。
    """
    safe_name = Path(file_name).name
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="非法文件名")
    reports_root = REPORTS_DIR.resolve()
    file_path = (reports_root / safe_name).resolve()
    if file_path.parent != reports_root:
        raise HTTPException(status_code=400, detail="非法文件名")
    return file_path


# Initialize session DB
# (统一由下方 _init_stores() 执行——import 期建表失败仅 ERROR 降级,见 #57)

# 决策日志表(幂等建表,decision_log 与 sessions 同库;decision-outcome-tracking)
# cohort 跑批记账表(delta add-forward-paper-trading-cohort;独立于 predictions)
from finance_agent.outcome.cohort.model import init_cohort_runs  # noqa: E402
from finance_agent.outcome.store import (  # noqa: E402
    DECISION_STATUSES,
    decision_stats,
    init_decision_log,
    list_decisions,
)
from finance_agent.outcome.track_record.calibration import calibration_table  # noqa: E402

# predictions 战绩体系(add-track-record):全量观点记录取代 decision_log 写入
from finance_agent.outcome.track_record.ingest import (  # noqa: E402
    persist_prediction_from_accumulated,
)
from finance_agent.outcome.track_record.judgment import (  # noqa: E402
    DEFAULT_HORIZON_DAYS,
)
from finance_agent.outcome.track_record.model import (  # noqa: E402
    avoidance_stats,
    count_predictions,
    get_active_agent,
    get_latest_metrics,
    get_prediction,
    init_predictions,
    init_track_record_tables,
    latest_equity_date,
    list_agents,
    list_audit,
    list_current_predictions,
    list_daily_marks,
    list_equity_curve,
    list_predictions,
    prediction_stats,
)
from finance_agent.outcome.track_record.segments import segment_all  # noqa: E402


def _init_stores() -> None:
    """import 期建表:任一存储初始化失败仅 ERROR 降级,不阻断 API 启动(#57)。

    DB 不可写时相关路由的写入本就有 per-call 兜底;import 期裸调用一旦抛错,
    整个进程起不来,/api/health 与只读路由一并陪葬。降级启动 + 日志可见,
    运维可据 ERROR 定位具体哪张库失败。
    """
    for label, init_fn in (
        ("sessions", init_db),
        ("decision_log", init_decision_log),
        ("predictions", init_predictions),
        ("track_record", init_track_record_tables),
        ("cohort_runs", init_cohort_runs),
    ):
        try:
            init_fn()
        except Exception:
            _logger.error("初始化 %s 存储失败(降级继续,写入有 per-call 兜底)", label, exc_info=True)


_init_stores()

# ── Node → Layer/Description mapping (shared with frontend) ──

LAYER_STEPS: list[dict] = [
    {"node": "check_cache", "layer": "PREP", "desc": "数据准备", "icon": "database"},
    {"node": "fetch_data", "layer": "PREP", "desc": "获取财务数据", "icon": "download"},
    {"node": "validate_financials", "layer": "PREP", "desc": "勾稽校验", "icon": "check-double"},
    {"node": "compute_metrics", "layer": "PREP", "desc": "指标计算", "icon": "calculator"},
    {"node": "fundamental_analyst", "layer": "Layer I", "desc": "基本面分析", "icon": "chart-line"},
    {"node": "technical_analyst", "layer": "Layer I", "desc": "技术面分析", "icon": "chart-line"},
    {"node": "macro_analyst", "layer": "Layer I", "desc": "宏观分析", "icon": "globe"},
    {"node": "sentiment_analyst", "layer": "Layer I", "desc": "舆情分析", "icon": "comments"},
    {"node": "verify_citations", "layer": "校验", "desc": "引用校验", "icon": "shield-alt"},
    {"node": "bull_r1", "layer": "Layer II", "desc": "看多辩论 R1", "icon": "arrow-up"},
    {"node": "bear_r1", "layer": "Layer II", "desc": "看空辩论 R1", "icon": "arrow-down"},
    {"node": "bull_r2", "layer": "Layer II", "desc": "看多辩论 R2", "icon": "arrow-up"},
    {"node": "bear_r2", "layer": "Layer II", "desc": "看空辩论 R2", "icon": "arrow-down"},
    {"node": "research_manager", "layer": "Layer II", "desc": "研究结论", "icon": "flag"},
    {"node": "trader", "layer": "Layer III", "desc": "交易决策", "icon": "hand-holding-usd"},
    {"node": "aggressive_r1", "layer": "Layer IV", "desc": "激进风控 R1", "icon": "fire"},
    {"node": "conservative_r1", "layer": "Layer IV", "desc": "保守风控 R1", "icon": "shield-alt"},
    {"node": "neutral_r1", "layer": "Layer IV", "desc": "中性风控 R1", "icon": "balance-scale"},
    {"node": "aggressive_r2", "layer": "Layer IV", "desc": "激进风控 R2", "icon": "fire"},
    {"node": "conservative_r2", "layer": "Layer IV", "desc": "保守风控 R2", "icon": "shield-alt"},
    {"node": "neutral_r2", "layer": "Layer IV", "desc": "中性风控 R2", "icon": "balance-scale"},
    {"node": "risk_judge", "layer": "Layer IV", "desc": "风控裁决", "icon": "gavel"},
    {"node": "fund_manager", "layer": "Layer V", "desc": "基金经理审批", "icon": "user-tie"},
    {"node": "generate_report", "layer": "报告", "desc": "报告生成", "icon": "file-alt"},
    {"node": "generate_file", "layer": "报告", "desc": "文件导出", "icon": "file-export"},
]

_NODE_MAP = {s["node"]: s for s in LAYER_STEPS}
_ALL_NODES = [s["node"] for s in LAYER_STEPS]

# Node → thinking description (streamed to frontend during execution)


# ── Request models ──


class LLMConfigRequest(BaseModel):  # noqa: N815  # 字段 camelCase 为前端 JSON 契约
    """请求级 LLM 配置（前端设置面板提交），字段为 None 时后端回退环境变量。

    字段用 camelCase 命名，与 LLMConfig dataclass 及前端 JSON 契约一致。
    """

    model: str | None = None
    baseUrl: str | None = None  # noqa: N815  # camelCase 为前端 JSON 契约
    apiKey: str | None = None  # noqa: N815  # camelCase 为前端 JSON 契约
    thinking: str | None = None
    apiForm: str | None = None  # noqa: N815  # API 形式：chat_completion / messages / responses，null=litellm 自动路由
    contextLength: int | None = None  # noqa: N815  # 请求级上下文长度（tokens），null 跟随 registry 静态 max_context

    @field_validator("apiForm")
    @classmethod
    def _api_form_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in ("chat_completion", "messages", "responses"):
            raise ValueError(
                f"apiForm 必须为 chat_completion / messages / responses 之一，收到 {v!r}"
            )
        return v

    @field_validator("contextLength")
    @classmethod
    def _context_length_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError(f"contextLength 必须为正整数，收到 {v!r}")
        return v


class AnalyzeRequest(BaseModel):
    stock_code: str = ""
    stock_name: str | None = None
    query: str = ""  # Natural language input
    analysis_type: str = "comprehensive"
    peer_codes: str | None = None
    enable_web_search: bool = False
    api_key: str | None = None
    session_id: str | None = None  # 追问时传入会话 ID
    focus: str = ""  # 深度研究意图澄清环节用户填写的关注点（Kimi 风格反问回答）
    user_id: str | None = None  # Langfuse user 聚合（ADR-0015）
    llm_config: LLMConfigRequest | None = None  # 请求级 LLM 配置（model/baseUrl/apiKey/thinking）


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    context: dict | None = None
    api_key: str | None = None
    user_id: str | None = None  # Langfuse user 聚合（ADR-0015）
    llm_config: LLMConfigRequest | None = None  # 请求级 LLM 配置（model/baseUrl/apiKey/thinking）


class RenameRequest(BaseModel):
    display_name: str


class FeedbackRequest(BaseModel):
    """用户点赞/点踩反馈请求体（add-user-feedback）。"""

    session_id: str
    value: str  # like | dislike


class ModelsRequest(BaseModel):
    """模型发现请求体（POST /api/llm-config/models）。"""

    baseUrl: str | None = None  # noqa: N815  # camelCase 为前端 JSON 契约
    apiKey: str | None = None  # noqa: N815  # camelCase 为前端 JSON 契约


class ExportRequest(BaseModel):
    """按需导出请求体（POST /api/export）。fmt: pdf | word | markdown。"""

    session_id: str
    fmt: str  # pdf | word | markdown（映射 docx/md）


class CacheClearRequest(BaseModel):
    """缓存清理请求体（POST /api/cache/clear）。

    scope: type（按类别）| code（按代码）| all（全清）。
    scope=all 必须携带 confirm=true，否则拒绝（高危确认红线）。
    """

    scope: str  # type | code | all
    data_type: str | None = None
    code: str | None = None
    confirm: bool = False


# ── Helpers ──


def _to_llm_config(req: LLMConfigRequest | None) -> LLMConfig | None:
    """将 Pydantic LLMConfigRequest 转换为内部 LLMConfig dataclass。

    req 为 None 或所有字段均为 None 时返回 None（回退环境变量默认行为）。
    """
    if req is None:
        return None
    cfg = LLMConfig(
        model=req.model,
        baseUrl=req.baseUrl,
        apiKey=req.apiKey,
        thinking=req.thinking,
        apiForm=req.apiForm,
        contextLength=req.contextLength,
    )
    # 全 None 时返回 None，避免无意义的空配置传播
    if not any([cfg.model, cfg.baseUrl, cfg.apiKey, cfg.thinking, cfg.contextLength]):
        return None
    return cfg


def _sse(data: dict) -> str:
    """Format a SSE data line.

    当 data 含 seq 字段时，在 data: 行前添加 id: 行，
    使原生 EventSource 的自动 Last-Event-ID 机制生效。
    """
    seq = data.get("seq")
    idLine = f"id: {seq}\n" if seq is not None else ""
    return f"{idLine}data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _stream_from_sync(gen):
    """把同步生成器转换为异步生成器，在线程中运行避免阻塞事件循环。

    用于在 async def event_stream 中迭代同步的 _run_graph_streaming 生成器。
    5 层分析图执行可能需要 90+ 秒，直接在事件循环中迭代会阻塞 SSE 流式输出。
    """
    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _run():
        try:
            for item in gen:
                asyncio.run_coroutine_threadsafe(q.put(item), loop)
        except Exception as e:  # noqa: BLE001
            asyncio.run_coroutine_threadsafe(q.put(e), loop)
        finally:
            asyncio.run_coroutine_threadsafe(q.put(None), loop)

    loop.run_in_executor(None, _run)

    while True:
        item = await q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def _truncate(text: str, n: int = 80) -> str:
    """截断文本到 n 个字符，超长追加省略号，并折叠换行。"""
    text = (text or "").strip().replace("\n", " ")
    return text[:n] + "…" if len(text) > n else text


_ACTION_ZH = {"buy": "买入", "sell": "卖出", "hold": "持有", "watch": "观望"}


def _decision_summary(decision: Any, label: str) -> str:
    """从 TradeDecision 对象/dict 生成一行决策摘要。"""
    if not decision:
        return f"{label}已生成"
    action = getattr(decision, "action", None)
    confidence = getattr(decision, "confidence", None)
    if action is None and isinstance(decision, dict):
        action = decision.get("action", "")
        confidence = decision.get("confidence")
    action_zh = _ACTION_ZH.get(action, action or "")
    if confidence is not None:
        try:
            return f"{label}：{action_zh}（置信度 {float(confidence):.0%}）"
        except (TypeError, ValueError):
            pass
    return f"{label}：{action_zh}"


def _node_summary(node_name: str, accumulated: dict, update: dict) -> str:
    """为已完成的管线节点生成一行式摘要，供按阶段展示的日志使用。"""
    if node_name == "check_cache":
        if accumulated.get("cache_result") == "HIT":
            return "缓存命中，复用已持久化数据"
        return "缓存未命中，准备获取最新数据"

    if node_name == "fetch_data":
        name = accumulated.get("stock_name") or accumulated.get("stock_code") or ""
        parts = []
        if accumulated.get("balance_sheet") is not None:
            parts.append("三大报表")
        if accumulated.get("kline") is not None:
            parts.append("K线行情")
        if accumulated.get("macro_indicators"):
            parts.append("宏观指标")
        if accumulated.get("news_list"):
            parts.append("新闻舆情")
        if accumulated.get("peer_financials") is not None:
            parts.append("同业数据")
        src = "、".join(parts) if parts else "财务数据"
        return f"已获取 {name} 的{src}" if name else f"已获取{src}"

    if node_name == "validate_financials":
        result = accumulated.get("validation_result", "")
        warnings = accumulated.get("validation_warnings") or []
        if result == "FAIL":
            return "勾稽校验失败（硬等式不通过）"
        if warnings:
            return f"勾稽校验通过，{len(warnings)} 项软警告"
        return "勾稽校验全部通过"

    if node_name == "compute_metrics":
        count = 0
        for key in (
            "solvency_metrics",
            "profitability_metrics",
            "efficiency_metrics",
            "cashflow_metrics",
        ):
            d = accumulated.get(key)
            if isinstance(d, dict):
                count += len(d)
        parts = [f"计算 {count} 项核心指标"]
        hs = accumulated.get("health_score")
        if isinstance(hs, dict) and hs:
            # health_score 的分值键是 total（无 score 键——旧取值恒 None 死代码，
            # clear-valuation-chain-debts D5）
            score = hs.get("total")
            if score is not None:
                parts.append(f"健康度 {score}")
        anomalies = accumulated.get("anomalies") or []
        if anomalies:
            parts.append(f"{len(anomalies)} 项异常")
        return "，".join(parts)

    if node_name == "verify_citations":
        if accumulated.get("citation_pass"):
            return "引用校验通过"
        return "引用校验完成（存在不通过项）"

    if node_name in ("bull_r1", "bull_r2", "bear_r1", "bear_r2"):
        history = accumulated.get("debate_history") or []
        for m in reversed(history):
            role = getattr(m, "role", None) or (m.get("role", "") if isinstance(m, dict) else "")
            if role in ("bull", "bear"):
                content = getattr(m, "content", None) or (
                    m.get("content", "") if isinstance(m, dict) else ""
                )
                return _truncate(content) or "辩论论点已生成"
        return "辩论论点已生成"

    if node_name == "research_manager":
        conclusion = accumulated.get("research_manager_conclusion") or ""
        return _truncate(conclusion) or "研究结论已汇总"

    if node_name == "trader":
        return _decision_summary(
            accumulated.get("trader_plan") or accumulated.get("trade_decision"), "交易建议"
        )

    if node_name in (
        "aggressive_r1",
        "aggressive_r2",
        "conservative_r1",
        "conservative_r2",
        "neutral_r1",
        "neutral_r2",
    ):
        history = accumulated.get("risk_debate_history") or []
        if history:
            last = history[-1]
            content = getattr(last, "content", None) or (
                last.get("content", "") if isinstance(last, dict) else ""
            )
            return _truncate(content) or "风控论点已生成"
        return "风控论点已生成"

    if node_name == "risk_judge":
        return _decision_summary(accumulated.get("final_trade_decision"), "风控裁决")

    if node_name == "fund_manager":
        decision = accumulated.get("fund_manager_decision", "")
        returns = accumulated.get("return_count", 0)
        if decision == "approve":
            return "基金经理审批通过"
        if decision == "return":
            return f"退回交易员修改（第 {returns} 次）"
        if decision == "reject":
            return "基金经理拒绝方案"
        return "基金经理决策完成"

    if node_name == "generate_report":
        report = accumulated.get("final_report") or ""
        return f"报告已生成（{len(report)} 字符）"

    if node_name == "generate_file":
        paths = accumulated.get("file_paths") or {}
        formats = [k for k, v in paths.items() if v]
        if formats:
            return f"已导出 {'/'.join(formats).upper()} 文件"
        return "文件导出完成"

    return ""


def _extract_output(node_name: str, update: dict, accumulated: dict) -> dict:
    """Extract structured output from a node update for the frontend."""
    output: dict[str, Any] = {}

    if node_name in (
        "technical_analyst",
        "fundamental_analyst",
        "macro_analyst",
        "sentiment_analyst",
    ):
        # 4 个并行分析师各自写 analyst_reports 的不同 key（technical/fundamental/macro/sentiment）
        reports = accumulated.get("analyst_reports") or {}
        key = node_name.replace("_analyst", "")
        report = reports.get(key) or reports.get(node_name)
        if report:
            if hasattr(report, "model_dump"):
                output = report.model_dump()
            elif isinstance(report, dict):
                output = report

    elif node_name in (
        "bull_r1",
        "bull_r2",
        "bear_r1",
        "bear_r2",
        "aggressive_r1",
        "aggressive_r2",
        "conservative_r1",
        "conservative_r2",
        "neutral_r1",
        "neutral_r2",
        "research_manager",
        "risk_judge",
    ):
        history = accumulated.get("debate_history") or []
        if history:
            last = history[-1]
            if hasattr(last, "model_dump"):
                output = last.model_dump()
            elif isinstance(last, dict):
                output = last

    elif node_name == "trader":
        decision = accumulated.get("trade_decision")
        if decision:
            if hasattr(decision, "model_dump"):
                output = decision.model_dump()
            elif isinstance(decision, dict):
                output = decision

    elif node_name == "fund_manager":
        output = update if isinstance(update, dict) else {}

    elif node_name == "generate_report":
        output = {"report_markdown": accumulated.get("final_report", "")}

    elif node_name == "generate_file":
        file_paths = accumulated.get("file_paths") or {}
        output = {"file_paths": file_paths}

    if isinstance(output, dict) and not output.get("summary"):
        summary = _node_summary(node_name, accumulated, update)
        if summary:
            output["summary"] = summary

    return output


def _merge_update(accumulated: dict, node_name: str, update: dict) -> None:
    """Merge a graph node update into accumulated state."""
    if not isinstance(update, dict):
        return
    for key, val in update.items():
        if key == "analyst_reports" and isinstance(val, dict):
            existing = accumulated.get("analyst_reports") or {}
            existing.update(val)
            accumulated["analyst_reports"] = existing
        elif key == "debate_history" and isinstance(val, list):
            existing = accumulated.get("debate_history") or []
            existing.extend(val)
            accumulated["debate_history"] = existing
        else:
            accumulated[key] = val


def _extract_analyst_summaries(accumulated: dict) -> dict:
    """Extract one-line summaries from each analyst report for chat context."""
    summaries: dict[str, str] = {}
    reports = accumulated.get("analyst_reports") or {}
    for name, report in reports.items():
        if hasattr(report, "summary"):
            summaries[name] = report.summary
        elif isinstance(report, dict):
            summaries[name] = str(report.get("summary", ""))
    return summaries


def _extract_agent_process(accumulated: dict) -> dict:
    """Extract Layer II-V intermediate outputs for session storage."""
    return {
        "research_conclusion": accumulated.get("research_manager_conclusion", ""),
        "trade_decision": _safe_dump(accumulated.get("trade_decision")),
        "risk_debate": _safe_dump(accumulated.get("risk_debate_history")),
        "fund_manager_decision": accumulated.get("fund_manager_decision", ""),
    }


def _safe_dump(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_safe_dump(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _safe_dump(v) for k, v in obj.items()}
    return obj


def _persist_decision_log(
    accumulated: dict, session_id: str, stock_code: str, stock_name: str
) -> None:
    """观点全量落 predictions(委托共享入口,旧 /api/analyze 路径)。

    旁路:任何失败仅 ERROR,不阻断报告。ReAct 深模式路径(agent_factory)与
    本路径共用 finance_agent.outcome.track_record.ingest。
    """
    persist_prediction_from_accumulated(accumulated, session_id, stock_code, stock_name)


def _stream_report_chunks(markdown: str, chunk_size: int = 200) -> list[str]:
    """Split markdown into chunks for progressive rendering."""
    chunks: list[str] = []
    lines = markdown.split("\n")
    current = ""
    for line in lines:
        current += line + "\n"
        if len(current) >= chunk_size:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


# ── Routes ──


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── 测试专用端点（仅 TESTING=1 下可用）──
if TESTING:

    @app.post("/api/test/seed")
    async def test_seed(req: dict):
        """测试数据造数端点。

        接收 display_name / session_type / chat_history（含可选 thinking + tool_calls
        + agentTimeline），内部经 session_store.create_session + append_chat 写入真实存储；
        顶层可选 pipeline_timelines（{node: [TimelineItem]}）与 pipeline_snapshot（dict），
        分别经 update_pipeline_timelines / update_pipeline_snapshot 落库，
        供历史会话恢复等 E2E 确定性构造会话（persist-full-session-timeline delta）；
        顶层可选 track_record（{equity_curve, index_closes, metrics_snapshot,
        predictions}）写战绩页造数表（add-index-performance-compare /
        add-portfolio-beta-alpha / add-prediction-pool-integrity），无需 chat_history
        也可单独使用，且不创建会话。
        """
        # 旧版 smoke 断言（{symbol}）保持占位响应，避免破坏既有契约；
        # track_record 造数块不受 chat_history 前置约束（track_record-only 造数合法）
        if "chat_history" not in req and not isinstance(req.get("track_record"), dict):
            return {"status": "ok", "mode": "testing"}
        # add-index-performance-compare:战绩页 E2E 造数(equity_curve/index_closes
        # 写入真实存储层;SESSIONS_DB_PATH 已被 e2e webServer 指向独立测试库)。
        # seed 是纯造数:track_record-only 请求到此为止,不创建会话,避免 E2E 会话
        # 侧栏被无 chat_history 的空会话污染（review fix）。
        track_seed = req.get("track_record")
        if isinstance(track_seed, dict):
            from finance_agent.outcome.track_record.model import (
                init_track_record_tables,
                upsert_equity_point,
                upsert_index_closes,
                upsert_metrics_daily,
            )

            init_track_record_tables()
            for pt in track_seed.get("equity_curve", []):
                upsert_equity_point(
                    pt["curve_date"],
                    pt["agent_nav"],
                    benchmark_nav=pt.get("benchmark_nav"),
                )
            upsert_index_closes(
                [
                    (r["index_code"], r["trade_date"], r["close"])
                    for r in track_seed.get("index_closes", [])
                ]
            )
            # add-portfolio-beta-alpha:指标快照造数(当日 upsert,缺省列自动置空;
            # 只写 agent_metrics_daily,不触碰 equity_curve/index_closes)
            metrics_snapshot = track_seed.get("metrics_snapshot")
            if isinstance(metrics_snapshot, dict):
                upsert_metrics_daily(_track_as_of(), metrics_snapshot)
            # predictions 造数（add-prediction-pool-integrity）：duplicate 徽标 E2E 用。
            # open 行原样插入；非 open 状态经 update_prediction_status 置终态，与生产
            # dedup/判定同一条状态变更路径（产生审计行）。逐行落 predictions 表，
            # 库路径与上方造数同源（模块默认,随 SESSIONS_DB_PATH 指向测试库）。
            seed_predictions = track_seed.get("predictions")
            if isinstance(seed_predictions, list):
                from finance_agent.outcome.track_record.model import (
                    insert_daily_mark as _seed_insert_mark,
                )
                from finance_agent.outcome.track_record.model import (
                    insert_prediction as _seed_insert_prediction,
                )
                from finance_agent.outcome.track_record.model import (
                    update_prediction_status as _seed_update_status,
                )

                for row in seed_predictions:
                    pid = _seed_insert_prediction(
                        {
                            "source_type": "live",
                            "symbol": row["symbol"],
                            "symbol_name": row.get("symbol_name", row["symbol"]),
                            "direction": row.get("direction", "neutral"),
                            "entry_price": row.get("entry_price", 10.0),
                            "target_price": row.get("target_price"),
                            "horizon_days": row.get("horizon_days", 20),
                            "confidence": row.get("confidence", 0.6),
                            "rationale_snapshot": {},
                            "langfuse_trace_id": None,
                            "created_at": row["created_at"],
                            "resolution_rule": None,
                        }
                    )
                    if row.get("status") and row["status"] != "open":
                        resolved: dict[str, Any] = {
                            "status": row["status"],
                            "resolution_rule": row.get("resolution_rule", row["status"]),
                        }
                        if row.get("resolved_at"):
                            resolved["resolved_at"] = row["resolved_at"]
                        # update-track-record-settle-price-display：结算字段透传
                        # （走 update_prediction_status，与生产判定写入同一路径），
                        # 供结算口径展示 E2E 造「同口径可比」的已结算行
                        for _fld in (
                            "settle_entry_price",
                            "exit_price",
                            "raw_return",
                            "excess_return",
                        ):
                            if row.get(_fld) is not None:
                                resolved[_fld] = row[_fld]
                        _seed_update_status(pid, resolved)
                    # update-track-record-display-clarity:行内 marks 子数组——浮动收益
                    # E2E 造数（prediction_id 服务端生成，顶层数组无法引用，按行携带）
                    for mk in row.get("marks") or []:
                        _seed_insert_mark(
                            pid,
                            mk["mark_date"],
                            mk.get("mark_price"),
                            mk.get("cum_return"),
                            mk.get("cum_excess"),
                            mk.get("benchmark_price"),
                        )
        # track_record-only（含 {} 空 dict）造数后即返回占位响应，不落会话
        if "chat_history" not in req:
            return {"status": "ok", "mode": "testing"}
        session_id = create_session(
            session_type=req.get("session_type", "chat"),
            display_name=req.get("display_name"),
            report_markdown=req.get("report_markdown", ""),
            status=req.get("status", "completed"),
        )
        for entry in req.get("chat_history", []):
            append_chat(
                session_id,
                role=entry["role"],
                content=entry["content"],
                thinking=entry.get("thinking"),
                tool_calls=entry.get("tool_calls"),
                agent_timeline=entry.get("agentTimeline"),
            )
        # 顶层管线字段（persist-full-session-timeline）：E2E 造「已完成管线会话」需要
        # pipeline_snapshot（走 completed 分支恢复分层时间轴）与 pipeline_timelines（恢复节点时序）
        pipeline_timelines = req.get("pipeline_timelines")
        if isinstance(pipeline_timelines, dict):
            update_pipeline_timelines(session_id, pipeline_timelines)
        pipeline_snapshot = req.get("pipeline_snapshot")
        if isinstance(pipeline_snapshot, dict):
            update_pipeline_snapshot(session_id, pipeline_snapshot)
        return {"session_id": session_id}

    @app.post("/api/test/reset")
    async def test_reset(req: dict):
        """测试数据清理端点骨架（F2 只返回占位响应，清理逻辑在 F3 落地）。"""
        return {"status": "ok", "mode": "testing"}


@app.get("/api/pipeline")
async def get_pipeline():
    """Return the pipeline node definitions for the frontend."""
    return {"steps": LAYER_STEPS}


# ── Sessions CRUD ──


@app.get("/api/sessions")
async def get_sessions():
    """List all sessions (metadata only)."""
    return {"sessions": await asyncio.to_thread(list_sessions)}


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Get full session by id."""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.patch("/api/sessions/{session_id}")
async def rename_session_api(session_id: str, req: RenameRequest):
    """Rename a session."""
    renamed = await asyncio.to_thread(rename_session, session_id, req.display_name)
    if not renamed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def delete_session_api(session_id: str):
    """Delete a session."""
    deleted = await asyncio.to_thread(delete_session, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    """取消会话的活跃生成任务。无活跃任务时检查终态事件做幂等返回。

    对应 delta spec Task 4.2。Fast path 由 PipelineRunner 后台线程驱动，
    需额外调 PipelineRunner.cancel 设置取消标志终止线程。
    """
    result = await registry.cancel(session_id)
    pipelineResult = await asyncio.to_thread(PipelineRunner.cancel, session_id)
    if not result and not pipelineResult:
        # 无活跃任务：检查是否已有终态事件（幂等返回）
        terminal = await asyncio.to_thread(get_terminal_event, session_id)
        if terminal:
            return {"ok": True, "status": terminal["type"]}
        raise HTTPException(status_code=404, detail="No active task for this session")
    return {"ok": True, "status": "interrupted"}


@app.get("/api/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    """恢复端点：经 registry.subscribe 返回 SSE 流，支持 after_seq/Last-Event-ID 断点续传。

    对应 delta spec Task 4.1。204 语义：无事件且无活跃任务时返回 204，
    避免对空会话下发无意义终态事件。
    """
    # session 存在性校验
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 解析 afterSeq：Last-Event-ID 头优先，否则查询参数，默认 0
    lastEventId = request.headers.get("Last-Event-ID")
    if lastEventId is not None:
        try:
            afterSeq = int(lastEventId)
        except ValueError:
            afterSeq = 0
    else:
        try:
            afterSeq = int(request.query_params.get("after_seq", "0"))
        except ValueError:
            afterSeq = 0

    # 204 语义：无事件且无活跃任务时返回 204
    events = await asyncio.to_thread(list_session_events, session_id, afterSeq)
    hasActive = registry.is_active(session_id)
    if not events and not hasActive:
        return Response(status_code=204)

    async def sse_stream() -> AsyncGenerator[str, None]:
        # 全量重放（afterSeq=0，刷新恢复）时注入合成 user_message，
        # 恢复 user 气泡的原始交错位置；断点续传（afterSeq>0）不注入
        async for event in registry.subscribe(
            session_id, afterSeq, replay_user_messages=(afterSeq == 0)
        ):
            yield _sse(event)

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Graph streaming (encapsulated as a reusable tool) ──


def _citations_payload(citation_report: dict | None) -> list[dict]:
    """把 citation_report（CitationReport.model_dump）转为前端引用数组。

    add-citation-display 契约：每项含 id/claim/source/verdict/detail 五字段，
    verdict 三级映射 PASS→verified / FAIL→failed / UNVERIFIABLE→unchecked。
    无引用或旧数据（citation_report 缺失）返回空数组，不报错。
    """
    if not citation_report:
        return []
    results = citation_report.get("results") or []
    verdict_map = {"PASS": "verified", "FAIL": "failed", "UNVERIFIABLE": "unchecked"}
    citations: list[dict] = []
    for i, r in enumerate(results, start=1):
        claim = r.get("claim") or {}
        field_ref = claim.get("field_ref") or ""
        field_ref_b = claim.get("field_ref_b")
        source = f"{field_ref} vs {field_ref_b}" if field_ref_b else field_ref
        details: list[str] = []
        ground_truth = r.get("ground_truth")
        delta = r.get("delta")
        if ground_truth is not None:
            details.append(f"重算值 {ground_truth}")
        if delta is not None:
            details.append(f"偏差 {delta}")
        if r.get("coverage_gap"):
            details.append("指标未注册重算覆盖（覆盖缺口）")
        if not details:
            details.append("无法自动校验（LLM 推断类声明）")
        citations.append(
            {
                "id": f"cite-{i}",
                "claim": claim.get("interpretation") or "",
                "source": source,
                "verdict": verdict_map.get(r.get("status") or "", "unchecked"),
                "detail": "；".join(details),
            }
        )
    return citations


# 引用标记模式：[[cite-<id>]]，前端渲染为行内上标
_CITATION_MARK_RE = re.compile(r"\[\[cite-([A-Za-z0-9_-]+)\]\]")


def inject_citation_marks(report_markdown: str, citations: list[dict]) -> str:
    """在报告 Markdown 中为唯一出现的 claim 注入 [[cite-<id>]] 标记。

    唯一匹配才注入（多处出现会导致锚点漂移，跳过）；零匹配不动正文。
    """
    if not report_markdown or not citations:
        return report_markdown
    for c in citations:
        claim = (c.get("claim") or "").strip()
        if not claim or claim not in report_markdown:
            continue
        if report_markdown.count(claim) != 1:
            continue
        report_markdown = report_markdown.replace(claim, f"{claim}[[{c['id']}]]", 1)
    return report_markdown


def _report_ready_event(
    analysis_id: str,
    session_id: str,
    report_markdown: str,
    chart_data: dict,
    file_paths: dict,
    stock_code: str,
    stock_name: str,
    duration_ms: int,
    citations: list[dict] | None = None,
) -> dict:
    """构造 report_ready SSE 事件载荷（供 _run_graph_streaming 下发）。

    citations：结构化引用数组（add-citation-display）；None 时省略该键（旧路径兼容）。
    """
    event = {
        "type": "report_ready",
        "analysis_id": analysis_id,
        "session_id": session_id,
        "report_markdown": report_markdown,
        "chart_data": chart_data,
        "file_paths": file_paths,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "duration_ms": duration_ms,
        "timestamp": _now(),
    }
    if citations is not None:
        event["citations"] = citations
    return event


def _blocked_failure_reason(accumulated: dict) -> tuple[str, str]:
    """无报告收尾的阻断归因（update-decision-price-gate）：返回 (failure_reason, node_id)。

    归因优先级：决策价位交叉校验门禁 fail > 勾稽校验 FAIL > 未产出报告兜底。
    调用方仅在图流正常耗尽且 report_sent 为 False 时调用。
    """
    gate = accumulated.get("decision_price_gate")
    if isinstance(gate, dict) and gate.get("result") == "fail":
        anomalies = accumulated.get("decision_price_anomalies") or []
        first = ""
        if anomalies and isinstance(anomalies[0], dict):
            first = str(anomalies[0].get("message") or "")
        return f"决策价位交叉校验未通过（打回重试后仍异常），报告阻断交付：{first}", "risk_judge"
    if accumulated.get("validation_result") == "FAIL":
        return "勾稽校验失败（硬等式不通过），管线阻断", "validate_financials"
    return "管线结束但未产出报告", "unknown"


def _run_graph_streaming(
    stock_code: str,
    stock_name: str,
    req: AnalyzeRequest,
    analysis_id: str,
    start_time: float,
    session_id: str | None = None,
    llm_config: LLMConfig | None = None,
) -> Generator[str, None, None]:
    """执行 5 层图分析，流式 yield SSE 事件。

    封装了 PREP → Layer I-V → 报告生成的完整流程，
    可作为 ReAct 循环中 run_deep_analysis 工具的执行体。

    Args:
        session_id: 可选外部传入的 session_id。若未提供，内部创建新 session。
        llm_config: 请求级 LLM 配置，透传到管线节点的 call_llm_streaming。
    """
    # D4 focus 兜底：意图澄清未收集到 focus 时，从 query 提取关注点关键词
    # 合成弱 focus（零命中保持为空，不硬造；query 全文不进分析师层）
    focus_value = (req.focus or "").strip()
    if not focus_value:
        from finance_agent.nodes.report import derive_focus_from_query

        focus_value = derive_focus_from_query(req.query or "")

    initial_state: dict[str, Any] = {
        "stock_code": stock_code,
        "stock_name": stock_name or stock_code,
        "analysis_type": req.analysis_type or "comprehensive",
        "peer_codes": [c.strip() for c in (req.peer_codes or "").split(",") if c.strip()] or None,
        "enable_web_search": req.enable_web_search,
        "api_key": req.api_key,
        "focus": focus_value,
        # 请求级 LLM 配置透传到管线节点（call_llm_streaming / call_llm）
        "llm_config": llm_config,
    }

    stock_name_display = stock_name or stock_code

    # 若 session_id 为外部传入，说明 session_created 已由调用方发送，避免重复
    session_created_externally = bool(session_id)

    if not session_id:
        session_id = create_session(
            stock_code=stock_code,
            stock_name=stock_name_display,
            status="running",
        )

    yield _sse(
        {
            "type": "analysis_start",
            "analysis_id": analysis_id,
            "session_id": session_id,
            "stock_code": stock_code,
            "stock_name": stock_name_display,
            "timestamp": _now(),
        }
    )

    if not session_created_externally:
        yield _sse(
            {
                "type": "session_created",
                "session_id": session_id,
                "display_name": f"{stock_name_display} {_now()[-5:]}",
                "timestamp": _now(),
            }
        )

    # add-report-revision-view：管线启动时回溯同标的上一份 completed 报告注入 state，
    # 供报告头「距上次报告」增量摘要渲染；None = 首份报告（含 DB 降级）。
    # 新会话已置 running，查询按 status='completed' 过滤天然排除自身；
    # 追问续跑（外部 session_id）按 id 显式排除
    initial_state["previous_report_snapshot"] = get_previous_completed_session(
        stock_code, session_id
    )

    completed: set[str] = set()
    accumulated: dict = dict(initial_state)
    report_sent = False

    # ADR-0015：手动 root span + session 聚合(仿 quick react_loop / _stream_graph)。
    # v4 CallbackHandler 在 LangGraph graph.stream 不建主 trace,必须手动建 root,
    # 否则内部 generation/数据源 span 各自成孤立 trace。
    from finance_agent.langfuse_tracing import (
        build_eval_metadata,
        eval_analysis_query,
        get_callback_handler,
        get_langfuse,
    )

    _handler = get_callback_handler()
    _lf = get_langfuse()
    _root_cm: contextlib.AbstractContextManager[Any] = contextlib.nullcontext()
    _propagate_cm: contextlib.AbstractContextManager[Any] = contextlib.nullcontext()
    if _lf is not None:
        with contextlib.suppress(Exception):
            _root_cm = _lf.start_as_current_observation(
                as_type="span",
                name=f"deep_analysis:{stock_name_display}",
                input={
                    "stock_code": stock_code,
                    "query": eval_analysis_query(req.query, stock_name_display, stock_code),
                },
            )
        try:
            from langfuse import propagate_attributes

            _propagate_cm = propagate_attributes(
                session_id=session_id, user_id=(req.user_id or None)
            )
        except Exception:  # noqa: S110
            pass
    _root_obs = _root_cm.__enter__()
    _propagate_cm.__enter__()

    _config: dict = {"recursion_limit": 100}
    if _handler is not None:
        _config["callbacks"] = [_handler]

    try:
        for mode, chunk in graph.stream(
            initial_state,
            config=_config,
            stream_mode=["updates", "custom"],
        ):
            # Custom mode: forward thinking tokens from nodes
            if mode == "custom":
                if isinstance(chunk, dict) and chunk.get("type") == "thinking":
                    yield _sse(
                        {
                            "type": "thinking_token",
                            "token": chunk.get("token", ""),
                            "node": chunk.get("node", ""),
                            "timestamp": _now(),
                        }
                    )
                continue

            # Updates mode: existing node progress logic
            for node_name, update in chunk.items():
                if node_name not in _NODE_MAP:
                    if isinstance(update, dict) and update:
                        _merge_update(accumulated, node_name, update)
                    continue

                step_info = _NODE_MAP[node_name]
                yield _sse(
                    {
                        "type": "node_start",
                        "node_id": node_name,
                        "layer": step_info["layer"],
                        "desc": step_info["desc"],
                        "icon": step_info["icon"],
                        "timestamp": _now(),
                    }
                )

                _merge_update(accumulated, node_name, update if isinstance(update, dict) else {})

                idx = _ALL_NODES.index(node_name)
                for i in range(idx + 1):
                    completed.add(_ALL_NODES[i])

                if accumulated.get("stock_name") in (None, "", stock_code):
                    quote = accumulated.get("stock_quote") or {}
                    info = accumulated.get("industry_info") or {}
                    fetched_name = quote.get("name") or info.get("name")
                    if fetched_name:
                        accumulated["stock_name"] = fetched_name

                output = _extract_output(
                    node_name, update if isinstance(update, dict) else {}, accumulated
                )

                yield _sse(
                    {
                        "type": "node_complete",
                        "node_id": node_name,
                        "layer": step_info["layer"],
                        "desc": step_info["desc"],
                        "completed": sorted(completed),
                        "progress": len(completed) / len(LAYER_STEPS),
                        "output": output,
                        "timestamp": _now(),
                    }
                )

            if accumulated.get("final_report") and not report_sent:
                report_sent = True
                file_paths = accumulated.get("file_paths") or {}
                stock_name_final = accumulated.get("stock_name", stock_code)
                duration_ms = int((time.time() - start_time) * 1000)

                report_md = accumulated["final_report"]
                chunks = _stream_report_chunks(report_md)

                for i, chunk_text in enumerate(chunks):
                    yield _sse(
                        {
                            "type": "report_chunk",
                            "chunk_index": i,
                            "total_chunks": len(chunks),
                            "text": chunk_text,
                            "timestamp": _now(),
                        }
                    )

                analyst_summaries = _extract_analyst_summaries(accumulated)
                agent_process = _extract_agent_process(accumulated)
                # add-citation-display：结构化引用随报告下发 + 正文注入引用标记 + 落库
                citations = _citations_payload(accumulated.get("citation_report"))
                report_md = inject_citation_marks(report_md, citations)
                update_session_report(
                    session_id,
                    report_markdown=report_md,
                    chart_data=accumulated.get("chart_data") or {},
                    analyst_reports=_safe_dump(accumulated.get("analyst_reports") or {}),
                    agent_process=agent_process,
                    analyst_summaries=analyst_summaries,
                    duration_ms=duration_ms,
                    file_paths=file_paths,
                    status="completed",
                    citations=citations,
                    # add-report-revision-view：终稿决策随报告落库，供同标的下份报告
                    # 「距上次报告」增量摘要回溯；无终稿（门禁阻断）时落 NULL
                    final_trade_decision=accumulated.get("final_trade_decision"),
                )
                # 旁路落库批准的 TradeDecision(失败仅 ERROR,不阻断报告)
                _persist_decision_log(accumulated, session_id, stock_code, stock_name_final)

                yield _sse(
                    _report_ready_event(
                        analysis_id,
                        session_id,
                        report_md,
                        accumulated.get("chart_data") or {},
                        file_paths,
                        stock_code,
                        stock_name_final,
                        duration_ms,
                        citations=citations,
                    )
                )

        # 管线阻断终态可见化（update-decision-price-gate）：图流正常耗尽但未产出报告
        # （决策价位门禁 fail / 勾稽 FAIL / 其他前置阻断）→ 置 failed + error 终态事件，
        # MUST NOT 停留 running 等超时兜底
        if not report_sent:
            reason, node_id = _blocked_failure_reason(accumulated)
            update_session_status(session_id, "failed", failure_reason=reason)
            yield _sse(
                {
                    "type": "error",
                    "node_id": node_id,
                    "session_id": session_id,
                    "message": reason,
                    "timestamp": _now(),
                }
            )

    except Exception as e:
        # 管线执行体异常终态 MUST 携带 failure_reason 与可读 traceback——
        # 此前无 reason（库中 failure_reason=None）且 traceback 字段存对象
        # repr（"<traceback object at 0x...>"），瞬态异常（如 4c038a41 的
        # DataFrame truth value ambiguous）完全无法定位
        import traceback as _tb

        update_session_status(session_id, "failed", failure_reason=f"{type(e).__name__}: {e}")
        yield _sse(
            {
                "type": "error",
                "node_id": "unknown",
                "session_id": session_id,
                "message": str(e),
                "traceback": _tb.format_exc(),
                "timestamp": _now(),
            }
        )
    finally:
        # 根 span 退出前写入评估数据 metadata + 摘要 output（与 _stream_graph 对齐；
        # post-exit update 会被 Langfuse 丢弃，必须在 __exit__ 前写；
        # metadata/output 各自独立容错——任一段失败不连带另一段）
        if _root_obs is not None:
            _meta: dict = {}
            _out: dict = {}
            with contextlib.suppress(Exception):
                _meta = build_eval_metadata(accumulated)
            with contextlib.suppress(Exception):
                from finance_agent.agent_factory import _build_trace_output

                _out = _build_trace_output(accumulated)
            with contextlib.suppress(Exception):
                _root_obs.update(metadata=_meta, output=_out)
        with contextlib.suppress(Exception):
            _propagate_cm.__exit__(None, None, None)
        with contextlib.suppress(Exception):
            _root_cm.__exit__(None, None, None)
        if _lf is not None:
            with contextlib.suppress(Exception):
                _lf.flush()


# ── Analyze (harness-based ReAct) ──


def _parse_sse_data(sse_str: str) -> dict | None:
    """从 SSE 字符串中解析 data JSON，失败返回 None。"""
    try:
        body = sse_str.strip().split("data: ", 1)[1]
        return json.loads(body)
    except (IndexError, json.JSONDecodeError, ValueError):
        return None


def _summarize_tool_result(result) -> str:
    """将工具结果浓缩为前端可展示的简短文本（与前端逻辑保持一致）。"""
    if isinstance(result, str):
        return result[:150]
    if isinstance(result, list):
        items: list[str] = []
        for r in result[:3]:
            if isinstance(r, dict):
                items.append(
                    r.get("title")
                    or r.get("name")
                    or r.get("code")
                    or json.dumps(r, ensure_ascii=False)[:50]
                )
            else:
                items.append(str(r)[:50])
        return "、".join(items)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)[:150]
    return ""


class _ChatCollector:
    """收集 Agent 流式事件中的最终回复、思考过程与工具调用，用于持久化到 chat_history。"""

    def __init__(self) -> None:
        self.response: str = ""
        self.thinking: str = ""
        self.tool_calls: list[dict] = []
        # 结构化时序（思考/搜索/工具调用交错），镜像前端 applyChatStreamEvent，
        # 持久化到 chat_history 的 agentTimeline 字段
        self.agent_timeline: list[dict] = []

    def feed(self, data: dict) -> None:
        t = data.get("type")
        # 维护结构化时序（timeline 构建器为纯函数，不可变更新后重新赋值）
        self.agent_timeline = apply_chat_event(self.agent_timeline, data)
        if t == "chat_token":
            # 回答增量（content，与 reasoning 分离）
            self.response += data.get("token", "")
        elif t == "thinking_token":
            # 原生思考增量（DeepSeek reasoning_content），直接累积
            self.thinking += data.get("token", "")
        elif t == "tool_call":
            name = data.get("name", "")
            # run_deep_analysis 触发的是管线 UI（非对话流工具调用框），跳过以与前端保持一致
            if name == "run_deep_analysis":
                return
            self.tool_calls.append(
                {
                    "name": name,
                    "args": data.get("args", {}),
                    "result_text": "",
                    "done": False,
                }
            )
        elif t == "tool_result":
            name = data.get("name", "")
            result_text = _summarize_tool_result(data.get("result"))
            for tc in reversed(self.tool_calls):
                if tc["name"] == name and not tc["done"]:
                    tc["result_text"] = result_text
                    tc["done"] = True
                    break


def _upsert_assistant_chat(session_id: str, collector: _ChatCollector) -> None:
    """增量持久化 collector 内容到 chat_history（upsert 语义）。

    在 SSE 循环中每 10 秒调用，确保运行中会话的 assistant 消息
    在用户中途切走后仍可从 chat_history 恢复。与最终持久化共用 upsert 语义，
    避免循环内 upsert 与最终 append 重复落库。
    """
    response = collector.response.strip()
    thinking = collector.thinking.strip() or None
    tool_calls = collector.tool_calls or None
    agent_timeline = collector.agent_timeline or None
    if not (response or thinking or tool_calls or agent_timeline):
        return
    upsert_chat(
        session_id,
        "assistant",
        response,
        thinking=thinking,
        tool_calls=tool_calls,
        agent_timeline=agent_timeline,
    )


# ── ReAct 路径后台生成任务（registry 驱动，生成与连接解耦）──


async def _subscribe_sse(
    session_id: str,
    after_seq: int = 0,
    on_event: Any = None,
) -> AsyncGenerator[str, None]:
    """订阅 registry 事件流并转发为 SSE 字符串。

    客户端断开仅退订（生成器取消），不影响后台生成任务。
    用后台 task 消费 subscribe 生成器，主循环 wait_for 取事件，
    超时发心跳保持 SSE 连接活跃（wait_for 直接作用于生成器会损坏它）。
    on_event：yield 前对事件 dict 的就地修改回调（如 Fast path 补充 done 展示字段）。
    """
    gen = registry.subscribe(session_id, after_seq)
    outQueue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def _consume() -> None:
        try:
            async for ev in gen:
                await outQueue.put(ev)
        except Exception:
            _logger.exception("订阅消费异常 session=%s", session_id)
        finally:
            await outQueue.put(None)

    consumer = asyncio.create_task(_consume())
    try:
        while True:
            try:
                ev = await asyncio.wait_for(outQueue.get(), timeout=10.0)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if ev is None:
                return
            if on_event is not None:
                on_event(ev)
            yield _sse(ev)
            if ev.get("type") in ("done", "interrupted", "error"):
                return
    finally:
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await consumer


# SSE 响应统一头（禁缓存、禁代理缓冲）
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_error_response(message: str) -> StreamingResponse:
    """构造 error + done 的短 SSE 响应（参数校验失败等无法进入任务流程的场景）。"""

    async def _err_gen() -> AsyncGenerator[str, None]:
        yield _sse({"type": "error", "message": message, "timestamp": _now()})
        yield _sse({"type": "done", "timestamp": _now()})

    return StreamingResponse(_err_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _persist_interrupted(session_id: str, collector: _ChatCollector) -> None:
    """中断兜底持久化：collector 已收集的部分回复落库并标注中断。

    spec 要求 chat_history 不出现无 assistant 回复的悬空 user 消息：
    collector 全空（任务刚启动即被取消）时落库占位 assistant 消息。
    """
    if collector.response.strip():
        collector.response += "\n\n[输出中断]"
    elif collector.thinking.strip() or collector.tool_calls or collector.agent_timeline:
        # 仅有思考/工具调用：保留过程，内容标注中断
        collector.response = "[输出中断]"
    else:
        collector.response = "[输出中断]"
    _upsert_assistant_chat(session_id, collector)


async def _run_chat_task(
    session_id: str,
    req: ChatRequest,
    display_name: str | None,
    api_key: str | None,
    llm_config: LLMConfig | None = None,
) -> None:
    """/api/chat 后台生成任务：quick/follow-up ReAct Agent。

    事件经 registry.publish 写入 journal 并 fan-out；客户端断开仅退订。
    中断/异常时部分回复落库、status 流转；done/error 终态由任务自身经
    publish_terminal 显式发布（journal 重试兜底；经 registry.start 包装运行时
    _run_task 的自动终态被终态 CAS 拒绝，不会重复落库）。
    """
    from finance_agent.agent_factory import build_agent, stream_agent_to_sse

    collector = _ChatCollector()
    try:
        # 新会话：session_created 作为首个事件入 journal（恢复重放的事实源）
        if display_name is not None:
            await registry.publish(
                session_id,
                {
                    "type": "session_created",
                    "session_id": session_id,
                    "display_name": display_name,
                    "timestamp": _now(),
                },
            )
        # user 消息在任务内落库：409（single-flight 拒绝）时不追加，避免污染历史
        await asyncio.to_thread(append_chat, session_id, "user", req.message)
        # 状态置 running：恢复端点与前端运行指示依赖
        await asyncio.to_thread(update_session_status, session_id, "running")

        # 模式推断：analysis 会话 -> follow-up（报告追问）；chat 会话 -> quick
        session = await asyncio.to_thread(get_session, session_id) or {}
        mode = "follow-up" if session.get("session_type") == "analysis" else "quick"

        agent = build_agent(
            mode=mode, api_key=api_key, session_id=session_id, llm_config=llm_config
        )

        lastPersistTime = time.time()
        PERSIST_INTERVAL = 10
        async for sse_str in stream_agent_to_sse(
            agent, req.message, session_id=session_id, user_id=req.user_id
        ):
            data = _parse_sse_data(sse_str)
            if data is None:
                # 心跳注释行不进 journal（传输层保活，非业务事件）
                continue
            collector.feed(data)
            await registry.publish(session_id, data)
            # 增量持久化：每 10s upsert，避免运行中切走后 assistant 回复丢失
            now = time.time()
            if now - lastPersistTime >= PERSIST_INTERVAL:
                await asyncio.to_thread(_upsert_assistant_chat, session_id, collector)
                lastPersistTime = now

        # 正常完成：最终持久化 + 状态流转 + done 终态（收拢到 publish_terminal
        # 重试兜底；registry._run_task 的自动 done 被终态 CAS 拒绝）
        await asyncio.to_thread(_upsert_assistant_chat, session_id, collector)
        await asyncio.to_thread(update_session_status, session_id, "completed")
        await registry.publish_terminal(
            session_id, {"type": "done", "session_id": session_id, "timestamp": _now()}
        )
    except asyncio.CancelledError:
        await asyncio.to_thread(_persist_interrupted, session_id, collector)
        await asyncio.to_thread(update_session_status, session_id, "interrupted")
        raise
    except Exception as exc:
        await asyncio.to_thread(_upsert_assistant_chat, session_id, collector)
        await asyncio.to_thread(
            update_session_status,
            session_id,
            "failed",
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
        # error 终态显式发布（收拢到 publish_terminal 重试兜底，与
        # _run_react_analysis except 对齐）。终态 CAS 拒绝返回 0 静默容忍；
        # publish_terminal 重试耗尽的 raise 只记日志——不得遮蔽正在上抛的原始异常。
        try:
            await registry.publish_terminal(
                session_id,
                {
                    "type": "error",
                    "session_id": session_id,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )
        except Exception:
            _logger.exception("error 终态发布失败 session=%s", session_id)
        raise


async def _run_react_analysis(
    session_id: str,
    req: AnalyzeRequest,
    analysis_id: str,
    start_time: float,
    display_name: str | None,
    api_key: str | None,
    llm_config: LLMConfig | None = None,
) -> None:
    """/api/analyze ReAct 路径后台生成任务（无股票代码或追问时的 harness Agent 编排）。

    事件经 registry.publish 写入 journal 并 fan-out；客户端断开仅退订。
    """
    from finance_agent.agent_factory import build_agent, stream_agent_to_sse

    collector = _ChatCollector()
    collectedMetadata: dict = {}
    analysisExecuted = False
    # thinking_token 批量落库（假卡死根因修复）：单条一次 SQLite 事务
    # （fsync 主导）会把消费端限速到事件积压、终态事件迟到
    # （601700 深研会话永远 running）。缓冲后经 publish_many 单事务
    # 批量写入；非 thinking 事件/心跳/缓冲满时冲刷，保持 seq 顺序。
    # 声明与冲刷助手均在 try 外：except 分支的终态前冲刷必须对 try 体内
    # 任意位置的异常可用（如 build_agent 抛错时 error 终态仍须发布）。
    pendingTokens: list[dict] = []
    TOKEN_BATCH_MAX = 32

    async def _flush_tokens() -> None:
        nonlocal pendingTokens
        if pendingTokens:
            await registry.publish_many(session_id, pendingTokens)
            pendingTokens = []

    try:
        if display_name is not None:
            await registry.publish(
                session_id,
                {
                    "type": "session_created",
                    "session_id": session_id,
                    "display_name": display_name,
                    "timestamp": _now(),
                },
            )
        await asyncio.to_thread(append_chat, session_id, "user", req.query)
        await asyncio.to_thread(update_session_status, session_id, "running")

        # 从 session 恢复 focus，与用户输入合并
        session = await asyncio.to_thread(get_session, session_id) or {}
        accumulatedFocus = (session.get("focus") or "").strip()
        currentFocus = (req.focus or "").strip()
        if currentFocus:
            accumulatedFocus = f"{accumulatedFocus}\n{currentFocus}".strip()

        agent = build_agent(
            mode="deep",
            api_key=api_key,
            analysis_type=req.analysis_type,
            peer_codes=req.peer_codes.split(",") if req.peer_codes else None,
            enable_web_search=req.enable_web_search,
            session_id=session_id,
            llm_config=llm_config,
        )

        def on_metadata_cb(metadata: dict) -> None:
            """收集 TOOL_METADATA（chart_data、analyst_reports 等）用于 session 持久化。"""
            collectedMetadata.update(metadata)

        def on_resolved_cb(sc: str, sn: str) -> None:
            """股票代码解析完成回调 -- 更新 session。"""
            asyncio.create_task(
                asyncio.to_thread(
                    update_session_for_clarify,
                    session_id,
                    stock_code=sc,
                    stock_name=sn,
                    display_name=f"{sn}({sc})",
                )
            )

        userQuery = req.query
        # 如果 session 已有 focus，注入用户消息上下文
        if accumulatedFocus:
            userQuery = f"{userQuery}\n\n[已收集的用户关注点/澄清回答：\n{accumulatedFocus}]"

        lastPersistTime = time.time()
        PERSIST_INTERVAL = 10

        async for sse_str in stream_agent_to_sse(
            agent,
            userQuery,
            on_metadata=on_metadata_cb,
            on_resolved=on_resolved_cb,
            session_id=session_id,
            user_id=req.user_id,
        ):
            data = _parse_sse_data(sse_str)
            if data is None:
                # SSE 心跳注释：借机冲刷批量缓冲，空闲期 token 不滞留 journal
                await _flush_tokens()
                continue
            collector.feed(data)
            if data.get("type") == "report_ready":
                analysisExecuted = True
                data["session_id"] = session_id
                data["duration_ms"] = int((time.time() - start_time) * 1000)
            if data.get("type") == "thinking_token":
                pendingTokens.append(data)
                if len(pendingTokens) >= TOKEN_BATCH_MAX:
                    await _flush_tokens()
            else:
                await _flush_tokens()
                if data.get("type") == "report_ready":
                    # report_ready 视同终态关键路径（承载报告产物）：走
                    # publish_terminal 获得重试兜底（非 CAS 集合，必放行）
                    await registry.publish_terminal(session_id, data)
                else:
                    await registry.publish(session_id, data)
            now = time.time()
            if now - lastPersistTime >= PERSIST_INTERVAL:
                await asyncio.to_thread(_upsert_assistant_chat, session_id, collector)
                lastPersistTime = now

        # 流结束：冲刷残余缓冲
        await _flush_tokens()

        # 最终持久化（upsert 语义，避免与增量重复落库）
        await asyncio.to_thread(_upsert_assistant_chat, session_id, collector)

        # 如果 Agent 调用了 run_deep_analysis，说明已进入分析阶段
        if collectedMetadata.get("report_markdown") or collectedMetadata.get("stock_code"):
            analysisExecuted = True

        if not analysisExecuted:
            # 分析未执行：Agent 仍在澄清阶段，保存状态并通知前端等待输入。
            # 管线超时/异常已置 failed 的会话除外——failed 是终态语义，
            # 被 clarifying 覆盖会丢失失败原因且误发 awaiting_input（601700 复盘）。
            _session_now = await asyncio.to_thread(get_session, session_id) or {}
            if _session_now.get("status") != "failed":
                if currentFocus:
                    await asyncio.to_thread(
                        update_session_for_clarify,
                        session_id,
                        focus=accumulatedFocus,
                        status="clarifying",
                    )
                else:
                    await asyncio.to_thread(
                        update_session_for_clarify, session_id, status="clarifying"
                    )
                await registry.publish(
                    session_id,
                    {
                        "type": "awaiting_input",
                        "session_id": session_id,
                        "pending_intent": "awaiting_focus",
                        "timestamp": _now(),
                    },
                )
            # 澄清轮显式刷出 trace：root observation 随流关闭才闭合，不主动 flush 的话
            # 澄清 trace 要等 SDK 批处理周期(~5s)才可见——用户此刻正在 Langfuse 里找它
            _lf_clarify = get_langfuse()
            if _lf_clarify is not None:
                with contextlib.suppress(Exception):
                    _lf_clarify.flush()
        # 分析已执行：session 状态已被 _run_graph_streaming 更新为 completed

        # done 终态（完整展示字段；registry._run_task 的自动 done 被终态 CAS 拒绝）。
        # 发布前冲刷残余 pending（保 seq 单调），走 publish_terminal 获得重试兜底
        await _flush_tokens()
        await registry.publish_terminal(
            session_id,
            {
                "type": "done",
                "analysis_id": analysis_id,
                "session_id": session_id,
                "duration_ms": int((time.time() - start_time) * 1000),
                "timestamp": _now(),
            },
        )
    except asyncio.CancelledError:
        await asyncio.to_thread(_persist_interrupted, session_id, collector)
        await asyncio.to_thread(update_session_status, session_id, "interrupted")
        raise
    except Exception as exc:
        await asyncio.to_thread(_upsert_assistant_chat, session_id, collector)
        await asyncio.to_thread(
            update_session_status,
            session_id,
            "failed",
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
        # error 终态显式发布（收拢到 publish_terminal 重试兜底，发布前冲刷残余
        # pending）。经 registry.start 包装运行时，_run_task 的自动 error 被
        # 终态 CAS 拒绝，不会重复落库；publish_terminal 重试耗尽的 raise 只记
        # 日志——不得遮蔽正在上抛的原始异常。
        try:
            await _flush_tokens()
            await registry.publish_terminal(
                session_id,
                {
                    "type": "error",
                    "session_id": session_id,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )
        except Exception:
            _logger.exception("error 终态发布失败 session=%s", session_id)
        raise


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    """深度分析 -- Fast path 直走管线，ReAct 路径由 registry 后台任务驱动。

    生成与连接解耦（resume-stream-on-session-switch）：
    - Fast path（stock_code 已知）：PipelineRunner 后台线程，事件桥接 journal
    - ReAct 路径（无股票代码/追问）：registry.start 后台任务 + 订阅转发，
      运行中重复请求返回 409 session_busy
    """
    analysis_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    if not req.query:
        return _sse_error_response("请输入股票代码或名称")

    # API key: 优先用请求中的，无效则回退到环境变量
    api_key = req.api_key if req.api_key and req.api_key.startswith("sk-") else None
    # 请求级 LLM 配置（LLMConfigRequest → LLMConfig），None 时回退环境变量
    llm_config = _to_llm_config(req.llm_config)

    # ── Session 生命周期：从首次输入开始 ──
    session_id = req.session_id
    display_name: str | None = None
    if session_id:
        session = await asyncio.to_thread(get_session, session_id)
        if not session:
            return _sse_error_response("Session not found")
    else:
        display_name = req.query.strip()[:30] or "深度分析"
        session_id = await asyncio.to_thread(
            create_session,
            stock_code="",
            stock_name="",
            display_name=display_name,
            status="clarifying",
            session_type="analysis",
        )

    stock_code = req.stock_code.strip()
    stock_name = req.stock_name or ""

    # ── Fast path: stock_code 已知，直接走管线 ──
    # 新会话（未传 session_id）且已解析出股票代码时走 fast path；
    # 追问/复用旧会话时走 ReAct 路径
    if stock_code and not req.session_id:
        # 追加用户输入到 chat_history
        await asyncio.to_thread(append_chat, session_id, "user", req.query)
        # 持久化管线触发锚点：fast path 下 chat_history 仅一条 user，锚点 = 1
        await asyncio.to_thread(set_pipeline_anchor, session_id)
        await asyncio.to_thread(
            update_session_for_clarify,
            session_id,
            stock_code=stock_code,
            stock_name=stock_name,
            status="running",
        )
        # session_created 同步写入 journal（恢复端点重放的事实源；
        # 在线客户端经 subscribe 重放收到，无需单独直发）。
        # 同步写避免 await 点：确保 PipelineRunner.start 在客户端断开前执行，
        # 且 session_created 先于管线事件入 journal（重放顺序正确）
        append_session_event(
            session_id,
            {
                "type": "session_created",
                "session_id": session_id,
                "display_name": display_name,
                "timestamp": _now(),
            },
        )
        # 管线后台执行：事件经 PipelineRunner._run 的 publish 桥接到 journal，
        # 客户端断开不中断管线，终态由 _run 的 finally 发布
        loop = asyncio.get_running_loop()
        PipelineRunner.start(
            session_id,
            lambda: _run_graph_streaming(
                stock_code,
                stock_name,
                req,
                analysis_id,
                start_time,
                session_id=session_id,
                llm_config=llm_config,
            ),
            {
                "layerTree": build_layer_tree(),
                "currentNodeId": "",
                "progress": 0.0,
                "updatedAt": int(time.time() * 1000),
                # 管线启动时间戳（毫秒）：前端刷新重建用作「已用时」计时起点
                "pipeline_start_ts": int(start_time * 1000),
            },
            loop=loop,
        )

        # done 终态补充展示字段：duration_ms 等由端点注入
        # （前端 App.tsx 依赖 duration_ms 显示"分析完成 · 耗时 X 秒"）
        def _enrich_done(ev: dict) -> None:
            if ev.get("type") == "done":
                ev["analysis_id"] = analysis_id
                ev["duration_ms"] = int((time.time() - start_time) * 1000)
                ev["timestamp"] = _now()

        return StreamingResponse(
            _subscribe_sse(session_id, on_event=_enrich_done),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    # ── ReAct 路径：registry 后台任务 + 订阅转发（生成与连接解耦）──
    started = await registry.start(
        session_id,
        _run_react_analysis(
            session_id,
            req,
            analysis_id,
            start_time,
            display_name,
            api_key,
            llm_config=llm_config,
        ),
    )
    if not started:
        # single-flight：会话已有活跃任务，拒绝新消息（不追加 user 消息）
        return JSONResponse(status_code=409, content={"error": "session_busy"})
    # 追问时跳过历史事件重放：用当前 journal 最大 seq 作为 after_seq，
    # 避免上一轮 done 终态事件导致 registry.subscribe 提前 return（SSE 流终止）
    afterSeq = await asyncio.to_thread(get_max_event_seq, session_id)
    return StreamingResponse(
        _subscribe_sse(session_id, after_seq=afterSeq),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ── Streaming Chat ──


@app.post("/api/chat")
async def quick_chat(req: ChatRequest):
    """Quick mode / Follow-up mode - registry 后台任务驱动的 ReAct Agent。

    生成与连接解耦（resume-stream-on-session-switch）：registry.start 后台任务
    持有生成逻辑，SSE 响应仅订阅转发；运行中重复请求返回 409 session_busy。
    """
    # API key: 优先用请求中的，无效则回退到环境变量
    api_key = req.api_key if req.api_key and req.api_key.startswith("sk-") else None
    # 请求级 LLM 配置（LLMConfigRequest → LLMConfig），None 时回退环境变量
    llm_config = _to_llm_config(req.llm_config)

    # ADR-0015：新对话时先创建 session，使 Langfuse session 聚合可用
    # （session_id 须在 agent 运行前确定，否则 propagate_attributes 拿不到）
    session_id = req.session_id
    display_name: str | None = None
    if session_id:
        session = await asyncio.to_thread(get_session, session_id)
        if not session:
            return _sse_error_response("Session not found")
    else:
        display_name = req.message.strip()[:30] or "快速问答"
        session_id = await asyncio.to_thread(create_chat_session, display_name)

    # 追问时跳过历史事件重放：用当前 journal 最大 seq 作为 after_seq，
    # 避免上一轮 done 终态事件导致 registry.subscribe 提前 return（SSE 流终止），
    # 与 /api/analyze ReAct 路径对齐。
    # 须在 registry.start 前取值：新会话的 session_created 由任务发布，
    # start 后再取 max_seq 可能跳过该事件，导致前端拿不到 session_id
    afterSeq = await asyncio.to_thread(get_max_event_seq, session_id)
    started = await registry.start(
        session_id,
        _run_chat_task(session_id, req, display_name, api_key, llm_config=llm_config),
    )
    if not started:
        # single-flight：会话已有活跃任务，拒绝新消息（不追加 user 消息）
        return JSONResponse(status_code=409, content={"error": "session_busy"})
    return StreamingResponse(
        _subscribe_sse(session_id, after_seq=afterSeq),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ── LLM 配置端点（add-custom-llm-api）──


@app.get("/api/llm-config")
async def get_llm_config():
    """返回后端默认 LLM 配置（环境变量），供前端设置面板展示 placeholder。

    不返回 apiKey（安全：不向后端暴露密钥）。
    """
    return {
        "model": os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        "baseUrl": os.getenv("LLM_BASE_URL", ""),
        "thinking": os.getenv("LLM_THINKING", "enabled"),
    }


@app.post("/api/llm-config/models")
async def list_models(req: ModelsRequest):
    """调用 OpenAI 兼容的 GET {baseUrl}/models 拉取模型列表。

    baseUrl / apiKey 回退环境变量。端点不支持时返回空列表 + error 提示。
    """
    import httpx

    baseUrl = (req.baseUrl or "").strip()
    # apiKey 允许回退环境变量（用户可能只填了 baseUrl 想探测端点，密钥用已配置的）
    apiKey = req.apiKey or os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))

    # 模型发现是「探测用户指定端点」的工具：baseUrl 为空时**不回退环境变量**，
    # 否则会悄悄用环境端点（如 LLM_BASE_URL）返回该端点模型，违背用户直觉（决策 A）。
    # 分析链路 call_llm 的回退不受影响（仍是 请求配置 → 环境变量 → 默认值）。
    if not baseUrl:
        return {"models": [], "error": "请先配置 API Base URL 再刷新模型列表"}

    url = baseUrl.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {apiKey}"} if apiKey else {}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return {"models": [], "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"models": [], "error": f"{type(e).__name__}: {e}"}

    # OpenAI 兼容格式：{"data": [{"id": "model-name"}, ...]}
    models = []
    for item in data.get("data", []):
        modelId = item.get("id") if isinstance(item, dict) else None
        if modelId:
            models.append(modelId)

    return {"models": sorted(models), "error": None}


@app.post("/api/llm-config/test")
async def test_llm_config(req: LLMConfigRequest):
    """五项 capability probe（delta 4.2，设计档案 §16）：返回能力矩阵。

    升级自「极简 Hi 连通性」：同时探测 non_stream/stream/tool_call/
    tool_followup/json_output，区分「能聊天」与「能跑 Agent」的假可用
    profile。返回 capability + effective 配置 + warnings。
    """
    import time as _time

    from finance_agent.llm.probe_cache import cache_key, get_probe_cache
    from finance_agent.llm.probes import run_live_probes

    cfg = _to_llm_config(req)
    startMs = _time.time()
    usedModel = (cfg.model if cfg else None) or os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
    usedApiKey = (cfg.apiKey if cfg else None) or None
    usedBaseUrl = (cfg.baseUrl if cfg else None) or None
    # 模型前缀归一（设计档案 §6）：自定义 baseUrl + 裸模型名 → 强制 openai/，
    # 与 resolver._ensure_prefix 同一语义；同时令缓存键与 resolve 侧一致
    # （裸模型探测结果否则永不命中 resolver 读取，终审 #77）。
    from finance_agent.llm.resolver import UnknownProviderPrefixError, _ensure_prefix

    try:
        usedModel = _ensure_prefix(usedModel, usedBaseUrl)
    except UnknownProviderPrefixError as e:
        # 配置类错误结构化返回（不再 500 裸栈）；异常文案本身含已知前缀列表
        # 与 openai/<model> 指引
        return {
            "success": False,
            "latencyMs": int((_time.time() - startMs) * 1000),
            "model": usedModel,
            "error": str(e),
            "errorType": "model_prefix_invalid",
        }
    probeModel = usedModel
    try:
        report = run_live_probes(
            model=usedModel,
            api_key=usedApiKey,
            base_url=usedBaseUrl,
        )
        if report.error is None:
            # 探测成功写入缓存：后续 resolve_profile 同键命中即合并 probe 事实
            get_probe_cache().put(
                cache_key(model=probeModel, base_url=usedBaseUrl, api_key=usedApiKey),
                report,
            )
        latencyMs = int((_time.time() - startMs) * 1000)
        return {
            "success": (report.non_stream or report.error is None),
            "latencyMs": latencyMs or report.latency_ms,
            "model": usedModel,
            "capability": {
                "non_stream": report.non_stream,
                "stream": report.stream,
                "tool_call": report.tool_call,
                "tool_followup": report.tool_followup,
                "json_output": report.json_output,
            },
            "warnings": report.warnings,
            "error": report.error,
        }
    except Exception as e:  # noqa: BLE001
        latencyMs = int((_time.time() - startMs) * 1000)
        errorType = _classify_llm_error(e)
        return {
            "success": False,
            "latencyMs": latencyMs,
            "model": usedModel,
            "error": f"{type(e).__name__}: {e}",
            "errorType": errorType,
        }


def _classify_llm_error(e: Exception) -> str:
    """将 LLM 调用异常分类为 auth / network / model_not_found / unknown。

    当 NotFoundError 的响应体为 HTML（如网站 404 页面）时，判定为 base_url 错误
    而非模型不存在——请求打到了网页服务器而非 API 端点。
    """
    errorName = type(e).__name__
    errorMessage = str(e).lower()

    # 认证错误
    if "auth" in errorName.lower() or "authentication" in errorName.lower():
        return "auth"
    if "401" in errorMessage or "invalid api key" in errorMessage:
        return "auth"

    # base_url 错误：NotFoundError 但响应体是 HTML（打到了网页服务器而非 API 端点）
    if "notfound" in errorName.lower() and ("<!doctype" in errorMessage or "<html" in errorMessage):
        return "network"

    # 模型不存在
    if "notfound" in errorName.lower():
        return "model_not_found"
    if "model" in errorMessage and "not found" in errorMessage:
        return "model_not_found"

    # 网络/连接错误
    if "connection" in errorName.lower() or "timeout" in errorName.lower():
        return "network"
    if (
        "connection" in errorMessage
        or "timeout" in errorMessage
        or "unreachable" in errorMessage
        or "refused" in errorMessage
    ):
        return "network"

    return "unknown"


_FMT_TO_KEY = {"pdf": "pdf", "word": "docx", "markdown": "md"}


@app.post("/api/export")
async def export_report_file(req: ExportRequest):
    """按需导出：从会话 report_markdown 现场生成单一文件，返回可下载 URL。

    404：会话不存在或无报告内容；400：不支持的导出格式；500：转换失败。
    """
    from finance_agent.export.service import export_report as do_export

    fmt_key = _FMT_TO_KEY.get(req.fmt)
    if fmt_key is None:
        raise HTTPException(status_code=400, detail="不支持的导出格式，可选：pdf / word / markdown")

    session = await asyncio.to_thread(get_session, req.session_id)
    report_md = (session or {}).get("report_markdown") or ""
    if not report_md:
        raise HTTPException(status_code=404, detail="会话不存在或无报告内容")

    stock_code = (session or {}).get("stock_code") or "unknown"
    stock_name = (session or {}).get("stock_name") or ""

    # 转换在独立线程执行，避免阻塞事件循环（现有 API 同款模式）
    result = await asyncio.to_thread(do_export, report_md, stock_code, stock_name, (fmt_key,))
    path = result.get(fmt_key)

    if not path:
        raise HTTPException(status_code=500, detail="报告导出失败，请稍后重试")

    file_name = str(Path(path).name)
    return {"file_name": file_name, "url": f"/api/files/{file_name}"}


@app.get("/api/files")
async def list_export_files():
    """扫描 REPORTS_DIR 返回导出文件元信息，按创建时间倒序（add-download-center）。"""
    if not REPORTS_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for entry in REPORTS_DIR.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in EXPORT_EXTENSIONS:
            continue
        st = entry.stat()
        items.append(
            {
                "file_name": entry.name,
                "file_type": entry.suffix.lower().lstrip("."),
                "size_bytes": st.st_size,
                "created_at": int(st.st_ctime * 1000),
            }
        )
    items.sort(key=lambda x: int(x["created_at"]), reverse=True)
    return items


@app.get("/api/files/{filename}")
async def download_file(filename: str):
    """Download generated report files."""
    file_path = resolve_reports_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=file_path.name)


@app.delete("/api/files/{filename}")
async def delete_export_file(filename: str):
    """删除 REPORTS_DIR 下的指定导出文件（add-download-center）。"""
    file_path = resolve_reports_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    file_path.unlink()
    return {"deleted": file_path.name}


@app.get("/api/decisions")
async def get_decisions(
    ticker: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """expose-decision-outcomes:只读决策列表(决策战绩页数据源)。非法 status 返回 422。"""
    if status is not None and status not in DECISION_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status: {status}")
    return await asyncio.to_thread(list_decisions, ticker=ticker, status=status, limit=limit)


@app.get("/api/decisions/stats")
async def get_decision_stats() -> dict[str, Any]:
    """expose-decision-outcomes:聚合战绩统计(胜率/均值只基于已结算记录)。"""
    return await asyncio.to_thread(decision_stats)


# ── track-record 战绩体系(add-track-record):predictions 只读接口 ──
_DISCLAIMER = "历史业绩不代表未来表现"


def _track_as_of() -> str:
    """北京时间日期(YYYY-MM-DD),战绩口径 as_of。"""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


@app.get("/api/v1/track-record/overview")
async def track_record_overview(
    source: str | None = None, version: int | None = None
) -> dict[str, Any]:
    """add-track-record:战绩总览(胜率/超额/样本量 + as_of + 免责声明)。

    显著性门槛:已结算 < 10 时 win_rate 置 null 并带 insufficient_sample 标注
    (前端显示「样本积累中」)。回测/实盘经 source 参数分离,不合并不展示。
    stage-b:总览追加组合指标块(年化/波动/夏普/回撤/风险分,来自 agent_metrics_daily
    最新快照;无快照时 portfolio.available=false)。
    stage-c(P6):统计按版本分段——version 缺省取当前活跃 agent 版本
    (无登记 agent 时统计全部);响应带 version_seq 与可用版本列表。
    Δ2(update-decision-settlement-contract):头条口径限 T+20(`caliber_horizon`),
    252 存量经 `legacy_settled` 披露;neutral 回避正确率独立成 `avoidance` 字段
    (门控与胜率一致:settled<10 不展示),不混入胜率。
    """
    if version is None:
        active = await asyncio.to_thread(get_active_agent)
        version = active["version_seq"] if active else None
    stats = await asyncio.to_thread(
        prediction_stats,
        source_type=source,
        version_seq=version,
        horizon_days=DEFAULT_HORIZON_DAYS,
    )
    settled = stats["settled"]
    insufficient = settled < 10
    avoidance = await asyncio.to_thread(avoidance_stats, source, None, version)
    legacy_all = await asyncio.to_thread(prediction_stats, source, None, version)
    metrics = await asyncio.to_thread(get_latest_metrics)
    # as_of = 指标所依据净值数据日期，SHALL NOT 用快照写入日期冒充（incident 032）。
    latest_curve_date = await asyncio.to_thread(latest_equity_date)
    portfolio = {
        "available": metrics is not None and latest_curve_date is not None,
        "annual_return": metrics.get("annual_return") if metrics else None,
        "volatility": metrics.get("volatility") if metrics else None,
        "sharpe": metrics.get("sharpe") if metrics else None,
        "max_drawdown": metrics.get("max_drawdown") if metrics else None,
        "risk_score": metrics.get("risk_score") if metrics else None,
        "risk_label": metrics.get("risk_label") if metrics else None,
        "beta": metrics.get("beta") if metrics else None,
        "jensen_alpha": metrics.get("jensen_alpha") if metrics else None,
        "as_of": latest_curve_date,
    }
    return {
        **stats,
        "source_type": source,
        "insufficient_sample": insufficient,
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
        "win_rate": None if insufficient else stats["win_rate"],
        "portfolio": portfolio,
        "version_seq": version,
        "versions": await asyncio.to_thread(list_agents),
        # Δ2 口径字段：回避正确率独立（门控同胜率）+ 头条窗口 + 跨口径存量披露
        # 门槛 10 = evals/outcome/caliber.MIN_SETTLED_FOR_WINRATE（生产侧不可 import evals，
        # 故此处按字面同步；口径变更先改 metrics.md §1.9④ 再同步本常量与 evals 侧）。
        "avoidance": {
            **avoidance,
            "avoidance_rate": None if avoidance["settled"] < 10 else avoidance["avoidance_rate"],
        },
        "caliber_horizon": DEFAULT_HORIZON_DAYS,
        "legacy_settled": legacy_all["settled"] - stats["settled"],
        # update-track-record-display-clarity：旧口径 open 计数（差值法，与 legacy_settled 对称；
        # 披露行双计数治「无存量」与全部 tab 内旧口径行的观感矛盾）
        "legacy_open": max(0, legacy_all["open"] - stats["open"]),
    }


@app.get("/api/v1/track-record/equity-curve")
async def track_record_equity_curve() -> dict[str, Any]:
    """add-track-record-stage-b:净值曲线(agent 组合 vs 基准,首个盯市日归一 1.0)。

    数据来自 equity_curve 表(daily-marking 日批落库);无净值点时返回空数组。
    输出字段统一 {date, agent_nav, benchmark_nav} 与指标引擎 nav_points 对齐。
    """
    rows = await asyncio.to_thread(list_equity_curve)
    points = [
        {
            "date": row["curve_date"],
            "agent_nav": row["agent_nav"],
            "benchmark_nav": row["benchmark_nav"],
        }
        for row in rows
    ]
    return {
        "points": points,
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.get("/api/v1/track-record/index-compare")
async def track_record_index_compare(span: str = "all") -> dict[str, Any]:
    """add-index-performance-compare:跑赢指数对比读数(展示层)。

    组合区间收益取 equity_curve 窗口首尾净值,指数收益取 index_closes 同窗口
    首尾收盘;win/loss 判定口径不变(仍锚 000300,见 docs/evals/metrics.md)。
    """
    if span not in ("all", "3m", "6m", "1y"):
        raise HTTPException(status_code=422, detail="span 必须为 all/3m/6m/1y")
    from finance_agent.outcome.track_record.index_compare import build_index_compare

    data = await asyncio.to_thread(build_index_compare, span)
    return {**data, "as_of": _track_as_of(), "disclaimer": _DISCLAIMER}


@app.get("/api/v1/track-record/calibration")
async def track_record_calibration() -> dict[str, Any]:
    """add-track-record-stage-c:置信度校准（分桶 + Brier Score）。

    桶含中值/样本数/实际命中率；Brier 越低校准越好。命中值按终态取：
    resolved_win→1.0 / resolved_loss→0.0 / resolved_neutral→neutral_prob（默认 0.5）；
    neutral 回避终态（status='avoidance'）按 avoidance_status 映射——
    avoidance_win→1.0 / avoidance_loss→0.0 / avoidance_neutral→neutral_prob；
    avoidance_status 为空（未判定）与 unresolvable/open 不进桶。
    """
    preds = await asyncio.to_thread(list_predictions, limit=10000)
    result = await asyncio.to_thread(calibration_table, preds)
    return {
        "buckets": result.buckets,
        "brier": result.brier,
        "sample_size": result.sample_size,
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.get("/api/v1/track-record/significance")
async def track_record_significance() -> dict[str, Any]:
    """结算显著性与信号一致性只读端点（add-prediction-pool-integrity；口径 §1.9-v2）。

    只读、不触发写操作。IC 只消费日主口径已判定行：先滤 resolution_rule=
    'duplicate_of_day'，再以 duplicate_prediction_ids() 二次防御（§1.9-v2：
    duplicate 永不进入）；long/short 方向 IC 与 neutral 回避序列分别成列。

    蒙特卡洛零模型：本端点恒为 available=false + reason（首批 T+20 结算时由结算
    报告任务产出，需个股行情面板，端点只披露可用状态）。红线「日主可判定样本
    <10 零模型 SHALL NOT 产出」由 11 月结算报告任务在消费 simulate_random_excess
    前执行（预登记 §1.9-v2），不在本端点判定；抽样 10,000 次为预登记默认值，
    本端点不提供配置项。「结论必附分位」契约由响应形态保证：excess 点估计与
    quantile/p_value 字段必须成对出现，本端点永不单独返回点估计。
    """
    from finance_agent.outcome.track_record.model import duplicate_prediction_ids, list_predictions
    from finance_agent.outcome.track_record.significance import icir, monthly_ic

    rows = await asyncio.to_thread(list_predictions, limit=10000)
    dup_ids = await asyncio.to_thread(duplicate_prediction_ids)
    clean = [
        r
        for r in rows
        if r.get("resolution_rule") != "duplicate_of_day" and r["prediction_id"] not in dup_ids
    ]
    series = monthly_ic(clean, kind="direction")
    avoidance = monthly_ic(clean, kind="avoidance")
    return {
        "ic_series": series,
        "icir": icir(series),
        "avoidance_series": avoidance,
        "monte_carlo": {
            "available": False,
            "reason": "首批 T+20 结算时由结算报告任务产出（需个股行情面板）",
        },
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.get("/api/v1/track-record/segments")
async def track_record_segments() -> dict[str, Any]:
    """add-track-record-stage-c:四维切片指标（持有期/行业/市值/市场环境）。

    市值与市场环境需外部数据（市值快照/基准均线），当前无数据时对应桶归
    「未知」并如实标注——不伪造数据。
    """
    preds = await asyncio.to_thread(list_predictions, limit=10000)

    def _run() -> list[dict[str, Any]]:
        dims = segment_all(preds)
        return [
            {
                "dimension": d.dimension,
                "total": d.total,
                "settled": d.settled,
                "buckets": [
                    {
                        "name": b.name,
                        "sample_size": b.sample_size,
                        "win_rate": b.win_rate,
                        "avg_excess": b.avg_excess,
                        "insufficient": b.insufficient,
                    }
                    for b in d.buckets
                ],
            }
            for d in dims
        ]

    dimensions = await asyncio.to_thread(_run)
    return {
        "dimensions": dimensions,
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.get("/api/v1/track-record/predictions/{prediction_id}")
async def track_record_prediction_detail(prediction_id: str) -> dict[str, Any]:
    """add-track-record-stage-c:观点详情（快照只读 + 判定信息 + 审计轨迹 + 盯市叠加）。"""
    prediction = await asyncio.to_thread(get_prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="prediction not found")
    audit = await asyncio.to_thread(list_audit, prediction_id)
    marks = await asyncio.to_thread(list_daily_marks, prediction_id=prediction_id)
    return {
        "prediction": prediction,
        "audit": audit,
        "marks": marks,
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.get("/api/v1/track-record/predictions")
async def track_record_predictions(
    status: str | None = None,
    symbol: str | None = None,
    source: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """add-track-record:观点日志列表(默认全部状态,含 loss)。
    add-track-record-sort-filter:可选 sort_by/sort_dir/keyword/date_from/date_to,
    缺省回退 created_at DESC;total 反映过滤后子集。分页上限 50。"""
    page = max(1, page)
    limit = max(1, min(page_size, 50))
    offset = (page - 1) * limit
    items = await asyncio.to_thread(
        list_predictions,
        ticker=symbol,
        status=status,
        source_type=source,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )
    total = await asyncio.to_thread(
        count_predictions,
        ticker=symbol,
        status=status,
        source_type=source,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )
    return {
        "predictions": items,
        "page": page,
        "page_size": limit,
        "total": total,
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.get("/api/v1/track-record/current")
async def track_record_current(source: str | None = None) -> dict[str, Any]:
    """add-current-stance-view:当前立场视图——每股最新一条 open 观点。

    纯展示收敛,不改变任何统计口径(分母仍按日主规则);dup/已结算/unresolvable
    不代表当前立场,一律排除。行内携带 source_type 供回测/实盘区分,可选 source
    参数过滤,不产出无法区分口径的合并视图。
    """
    rows = await asyncio.to_thread(list_current_predictions, source)
    return {
        "current": rows,
        "total": len(rows),
        "as_of": _track_as_of(),
        "disclaimer": _DISCLAIMER,
    }


@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest) -> dict[str, Any]:
    """add-user-feedback:用户点赞/点踩落 Langfuse trace(按 session 解析最近一次运行)。

    旁路铁律:Langfuse 不可达/trace 不存在/session 无关联 trace 时仅 WARN,不报 5xx;
    前端不因后端失败报错(本地 toggle 语义保留)。value 非法经 Pydantic 校验 422。
    """
    if req.value not in ("like", "dislike"):
        raise HTTPException(status_code=422, detail="value must be like|dislike")
    trace_id = await asyncio.to_thread(get_session_trace_id, req.session_id)
    if not trace_id:
        _logger.warning("feedback 跳过: session %s 无关联 trace", req.session_id)
        return {"ok": True, "submitted": False}
    lf = get_langfuse()
    if lf is None:
        _logger.warning("feedback 跳过: Langfuse 未配置")
        return {"ok": True, "submitted": False}
    try:
        lf.create_score(
            name="user_feedback",
            value=1 if req.value == "like" else 0,
            trace_id=trace_id,
            data_type="BOOLEAN",
        )
        lf.flush()
        return {"ok": True, "submitted": True}
    except Exception:  # noqa: BLE001 - 旁路铁律
        _logger.warning("feedback 上报失败(trace 不可查?): %s", trace_id, exc_info=True)
        return {"ok": True, "submitted": False}


def _now() -> str:
    """返回北京时间字符串，与 agent_factory._now() 保持一致"""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


# ── Agent 设置中心（add-agent-settings-center）──


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    """返回当前 git 仓库 HEAD 短哈希（前 12 位）；非 git 环境返回 None。

    从 api.py 所在目录向上逐级寻找含 .git 的仓库根（不同部署布局下
    __file__ 深度可能不同，故用向上搜索而非固定 parents 下标）。
    """
    root = Path(__file__).resolve().parent
    for _ in range(8):  # 向上最多找 8 层，防御性边界
        if (root / ".git").exists():
            try:
                out = subprocess.run(  # noqa: S603 - 固定命令列表，无用户输入
                    ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607 - 用 PATH 中的 git
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if out.returncode == 0:
                    return out.stdout.strip()[:12]
            except Exception:  # noqa: BLE001 - git 不可用视为无版本信息
                return None
            return None
        parent = root.parent
        if parent == root:
            break
        root = parent
    return None


@app.get("/api/cache/stats")
async def cache_stats() -> dict:
    """缓存统计：数据缓存（cache.db）与能力探测缓存（内存）+ 数据源监控快照。"""
    data = await asyncio.to_thread(get_shared_cache().stats)
    probe = await asyncio.to_thread(get_probe_cache().stats)
    return {"data": data, "probe": probe, "monitor": get_monitor().snapshot()}


@app.post("/api/cache/clear")
async def cache_clear(req: CacheClearRequest) -> dict:
    """清理数据缓存：按类别 / 按代码 / 全清。

    高危红线：scope=all 未携带 confirm=true 时拒绝（400），缓存保持不变。
    """
    cache = get_shared_cache()
    if req.scope == "all":
        if not req.confirm:
            raise HTTPException(status_code=400, detail="clear all requires confirm=true")
        removed = await asyncio.to_thread(cache.clear_all)
        return {"scope": "all", "removed": removed}
    if req.scope == "type":
        if not req.data_type:
            raise HTTPException(status_code=400, detail="data_type required for scope=type")
        removed = await asyncio.to_thread(cache.delete_by_type, req.data_type)
        return {"scope": "type", "data_type": req.data_type, "removed": removed}
    if req.scope == "code":
        if not req.code:
            raise HTTPException(status_code=400, detail="code required for scope=code")
        removed = await asyncio.to_thread(cache.delete_by_code, req.code)
        return {"scope": "code", "code": req.code, "removed": removed}
    raise HTTPException(status_code=400, detail="unknown scope")


@app.post("/api/cache/probe-cache/clear")
async def probe_cache_clear() -> dict:
    """清除能力探测缓存（内存）：下次请求将重新执行能力探测。"""
    await asyncio.to_thread(get_probe_cache().clear)
    return {"cleared": True}


@app.post("/api/sessions/clear-all")
async def clear_all_sessions_endpoint() -> dict:
    """清空全部会话及其事件日志（级联删除），返回删除的会话数。"""
    n = await asyncio.to_thread(clear_all_sessions)
    return {"cleared": n}


@app.get("/api/run-info")
async def run_info() -> dict:
    """只读运行信息：默认 LLM 配置 / 健康状态 / Langfuse / git 版本。

    安全红线：绝不含 apiKey（与 GET /api/llm-config 不回显密钥一致）。
    """
    model = os.environ.get("LLM_MODEL") or DEFAULT_LLM_MODEL
    base_url = os.environ.get("LLM_BASE_URL") or ""
    thinking = os.environ.get("LLM_THINKING") or "enabled"
    langfuse_host = os.environ.get("LANGFUSE_HOST") or "http://localhost:3000"
    langfuse_enabled = bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    )
    version = "0.1.0"
    with contextlib.suppress(Exception):  # 包未安装时回退默认版本
        version = importlib.metadata.version("finance-agent")
    return {
        "model": model,
        "base_url": base_url,
        "thinking": thinking,
        "langfuse_host": langfuse_host,
        "langfuse_enabled": langfuse_enabled,
        "version": version,
        "git_commit": await asyncio.to_thread(_git_commit),
        "health": "ok",
    }


@app.get("/api/data-source/status")
async def data_source_status() -> dict:
    """数据源状态：命中/未命中/失败计数 + 数据新鲜度列表。"""
    data = await asyncio.to_thread(get_shared_cache().stats)
    return {"monitor": get_monitor().snapshot(), "freshness": data}


# ── AG-UI 协议通道（add-assistant-ui-thread PoC，双轨隔离：仅挂载，不接入 registry）──
from finance_agent.agui.endpoint import router as agui_router  # noqa: E402

app.include_router(agui_router)

# ── 评估运维控制台端点族（delta add-eval-ops-console：/api/v1/ops/*）──
from finance_agent.ops_api import router as ops_router  # noqa: E402

app.include_router(ops_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
