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
from collections.abc import AsyncGenerator, Callable, Generator
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

_logger = logging.getLogger("finance_agent.api")

# 主事件循环引用：调度线程（APScheduler）需要把自动选股分析的入队动作投递回
# uvicorn 主循环，才能复用 AnalyzeQueue 的 _promote/_watch 与 SSE/journal 通道。
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None

load_dotenv()  # 加载 .env，须在 finance_agent 模块导入前执行（llm.py 等在 import 时读取环境变量）

# 默认 LLM 模型（无环境变量 LLM_MODEL 时的回退值）。
# 与 /api/llm-config 占位保持一致；实际管线默认在 agent_factory，后续如需统一另行裁决。
DEFAULT_LLM_MODEL = "deepseek/deepseek-v4-pro"

from finance_agent import analysis_concurrency  # noqa: E402
from finance_agent.data.cache import get_shared_cache  # noqa: E402
from finance_agent.data.monitoring import get_monitor  # noqa: E402
from finance_agent.data.snapshot import (  # noqa: E402
    compute_snapshot_status,
    get_all_meta,
    latest_snapshot_date,
)
from finance_agent.execution.brokers.base import ORDER_STATUSES, STATUS_REJECTED  # noqa: E402
from finance_agent.factors.builtin import BUILTIN_FACTORS  # noqa: E402
from finance_agent.factors.harvest import DEFAULT_MAX_BATCHES_PER_RUN  # noqa: E402
from finance_agent.factors.library import (  # noqa: E402
    FACTOR_STATES,
    list_events,
    list_factors,
    list_ic_series,
    list_ledger_entries,
)
from finance_agent.factors.style_exposure import (  # noqa: E402
    compute_style_exposure,
    style_exposure_payload,
)
from finance_agent.factors.tools import IC_WINDOW as FACTOR_IC_WINDOW  # noqa: E402
from finance_agent.graph import build_5layer_graph  # noqa: E402
from finance_agent.langfuse_tracing import get_langfuse  # noqa: E402
from finance_agent.llm import LLMConfig  # noqa: E402
from finance_agent.llm.probe_cache import get_probe_cache  # noqa: E402
from finance_agent.memory.semantic import (  # noqa: E402
    CLAUSE_STATUSES,
    confirm_clause,
    list_clauses,
    reject_clause,
)
from finance_agent.pipeline_runner import PipelineRunner, build_layer_tree  # noqa: E402
from finance_agent.screening.freshness import screening_freshness_removals  # noqa: E402
from finance_agent.screening.pipeline import (  # noqa: E402
    latest_successful_pool,
)
from finance_agent.screening.pool_analysis import (  # noqa: E402
    prepare_pool_analysis_batch,
)
from finance_agent.screening.queue import AnalyzeQueue  # noqa: E402
from finance_agent.screening.ranker import recent_ic_stats  # noqa: E402
from finance_agent.screening.store import (  # noqa: E402
    DEFAULT_BATCH_HISTORY_LIMIT,
    MAX_BATCH_HISTORY_LIMIT,
    latest_screening_batch,
    latest_successful_batch,
    list_batches,
)
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
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()
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
        _MAIN_LOOP = None
        # 旁路铁律:退出时必须尝试关闭 scheduler;关闭失败记 ERROR 不阻断 API 关停
        try:
            stop_scheduler(_scheduler)
        except Exception:
            _logger.exception("decision settle scheduler 关闭失败(忽略)")


app = FastAPI(title="Investment Research Agents API", lifespan=_lifespan)

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
from finance_agent.outcome.events.model import (  # noqa: E402
    get_watchlist_item,
    init_events_db,
    list_watchlist,
    mark_watchlist_analyzing,
)
from finance_agent.outcome.events.watchlist import (  # noqa: E402
    WATCHLIST_SOURCE_CBB,
)
from finance_agent.outcome.regime.payload import build_regime_payload  # noqa: E402
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
        ("events", init_events_db),
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


def _seed_broker_account(seed: dict[str, Any]) -> None:
    """broker 造数（delta add-broker-console T1.2）：cash/positions/orders 经既有落库入口写入。

    ``order_id`` 缺省按 ``PaperBroker.order_id_for`` 同源派生
    （``signal_id:symbol:side``）——与生产订单 id 口径一致，避免造数与真实重跑口径
    分叉；缺 ``cash`` 时按 PaperBroker 初始资金惰性开户（``ensure_account``，不覆盖
    已有余额）。状态/原因码/证据链照 seed 行给定（不臆造）。库路径于调用期读
    EXECUTION_DB_PATH（store 各入口 env 口径；E2E 指向独立测试库）。
    """
    from finance_agent.execution.brokers.base import OrderRecord
    from finance_agent.execution.store import (
        ensure_account,
        execution_conn,
        insert_order,
        write_cash,
        write_position,
    )

    now = _now()
    with execution_conn() as conn:
        cash = seed.get("cash")
        if cash is None:
            ensure_account(conn, 1_000_000.0, now)
        else:
            write_cash(conn, float(cash), now)
        for row in seed.get("positions") or []:
            write_position(
                conn,
                str(row["symbol"]),
                int(row["qty"]),
                float(row["avg_cost"]),
                str(row.get("updated_at") or now),
            )
        for order_row in seed.get("orders") or []:
            symbol = str(order_row["symbol"])
            side = str(order_row["side"])
            signal_id = str(order_row["signal_id"])
            insert_order(
                conn,
                OrderRecord(
                    order_id=str(order_row.get("order_id") or f"{signal_id}:{symbol}:{side}"),
                    symbol=symbol,
                    side=side,
                    qty=int(order_row["qty"]),
                    status=str(order_row["status"]),
                    reason=order_row.get("reason"),
                    fill_price=order_row.get("fill_price"),
                    fee=float(order_row.get("fee", 0.0)),
                    decision_id=str(order_row["decision_id"]),
                    signal_id=signal_id,
                    created_at=str(order_row.get("created_at") or now),
                    attempts=int(order_row.get("attempts", 0)),
                ),
            )


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
        predictions, paper_equity}）写战绩页造数表（add-index-performance-compare /
        add-portfolio-beta-alpha / add-prediction-pool-integrity / add-paper-broker），
        无需 chat_history 也可单独使用，且不创建会话；track_record_reset（bool：清
        战绩造数面五表（predictions/daily_marks/equity_curve/agent_metrics_daily/
        index_closes）+ paper_equity，供战绩页 E2E 套件全量复位）同不受前置约束；
        顶层可选 snapshot（dict：写 snapshot_meta 键）与 snapshot_reset（bool：先清
        快照三表）造行情快照告警态（add-market-snapshot D6），同样无需 chat_history。
        顶层可选 screening（dict：``batch`` 造批次行（含 error 造失败横幅）、``pool``
        造精选池行、``queue`` 造批量分析队列徽标态）与 screening_reset（bool：清
        screening 五表**并丢弃进程内队列单例**）造选股页确定性状态
        （add-screening-pipeline S6）；库路径于调用期读 SCREENING_DB_PATH（导入期
        冻结会让 E2E 注入失效）。
        顶层可选 memory（dict：``clauses`` 造条款，逐条经既有落库 + 状态机置
        pending/confirmed/rejected；可带 source 与 evidence（JSON 文本，结算样本）
        造记忆确认页确定性状态）与 memory_reset（bool：清条款表）造记忆确认页
        确定性状态（delta add-memory-confirmation T1/2.3）；库路径于调用期读
        SESSIONS_DB_PATH（semantic 模块 env 口径，E2E 指向独立测试库）。
        顶层可选 factors（dict：``factors`` 造因子，逐条经既有落库 +
        状态机走到目标态（active 记 ADMITTED，与准入路径同源；degraded/retired 的
        detail 由行内 ``detail`` 给出；``via_degraded`` 为真时 retired 先经
        degraded(DECAY) 落衰减史，供「衰减标记」列造态）；行内可带 ic
        （[[trade_date, rank_ic]...] 经 record_ic 幂等写入）与
        pool_increment/alignment_score/ast_json）与 factors_reset（bool：清因子库
        三表）造因子库页确定性状态（delta add-factor-library-page T1.2）；库路径
        于调用期读 FACTOR_LIBRARY_DB_PATH（library 模块 env 口径，E2E 指向独立
        测试库，绝不落到开发库 data/factor_library.db）。
        顶层可选 broker（dict：``cash`` 写账户余额、``positions`` 造持仓、
        ``orders`` 造订单（四态/原因码/证据链照填，``order_id`` 缺省按
        PaperBroker.order_id_for 同源派生））与 broker_reset（bool：清账户三表
        orders/positions/cash）造执行台账页确定性状态（delta add-broker-console
        T1.2）；库路径于调用期读 EXECUTION_DB_PATH（E2E 指向独立测试库）。
        顶层可选 regime（list：逐行按 ``regime_daily`` DDL 列名造数（含
        ``cap_source``，缺省字段如实落 NULL），经 upsert_regime_daily 写入）与
        regime_reset（bool：清 ``regime_daily``）造选股页市场温度计三态/空态
        （delta add-crowding-regime-positioning T8）；库路径于调用期读
        REGIME_DB_PATH（E2E 指向独立测试库 data/test-e2e-regime.db，绝不落到
        开发库 data/regime.db）。
        顶层可选 events（dict：``forecasts``/``triage``/``watchlist``/``journal``
        逐条经既有落库入口写入：预告 upsert（幂等）/分诊/清单（去重）/记账）与
        events_reset（bool：清事件线四表）造选股页事件观察区块三态/空态
        （delta add-event-driven-lane）；库路径于调用期读 EVENTS_DB_PATH
        （E2E 指向独立测试库 data/test-e2e-events.db，绝不落到开发库 data/events.db）。
        """
        # 旧版 smoke 断言（{symbol}）保持占位响应，避免破坏既有契约；
        # track_record / snapshot / screening / memory / factors / broker / regime
        # 造数块不受 chat_history 前置约束
        if (
            "chat_history" not in req
            and not isinstance(req.get("track_record"), dict)
            and not isinstance(req.get("snapshot"), dict)
            and not req.get("snapshot_reset")
            and not isinstance(req.get("screening"), dict)
            and not req.get("screening_reset")
            and not req.get("track_record_reset")
            and not isinstance(req.get("memory"), dict)
            and not req.get("memory_reset")
            and not isinstance(req.get("factors"), dict)
            and not req.get("factors_reset")
            and not isinstance(req.get("broker"), dict)
            and not req.get("broker_reset")
            and not isinstance(req.get("regime"), list)
            and not req.get("regime_reset")
            and not isinstance(req.get("events"), dict)
            and not req.get("events_reset")
            and not isinstance(req.get("daily_brief"), dict)
        ):
            return {"status": "ok", "mode": "testing"}
        # add-paper-broker：战绩造数复位（清 equity_curve + paper_equity 两表）。双曲线
        # E2E 每例前后 reset，避免种子残留破坏同套件其它 spec 的空态前提（隔离纪律与
        # screening_reset 同款）。库路径于调用期读 SESSIONS_DB_PATH / EXECUTION_DB_PATH。
        if req.get("track_record_reset"):
            from finance_agent.execution.store import delete_all_paper_equity
            from finance_agent.outcome.track_record.model import delete_all_track_record_state

            # 全量复位（2026-10-13 E2E 修复）：此前只清 equity_curve + paper_equity，
            # 残留的 predictions/daily_marks 令「库内只有自己造的行」类断言误红；
            # 现清五张造数面表 + paper_equity，与 CI 全新起库等价。
            delete_all_track_record_state()
            delete_all_paper_equity()
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
            # add-paper-broker：paper 净值造数（战绩页双曲线 E2E）——写 execution 库
            # （库路径调用期读 EXECUTION_DB_PATH，见 execution/store.py 的 env 口径）
            seed_paper = track_seed.get("paper_equity")
            if isinstance(seed_paper, list):
                from finance_agent.execution.store import upsert_paper_equity

                for row in seed_paper:
                    upsert_paper_equity(
                        row["trade_date"],
                        row["cash"],
                        row["positions_value"],
                        row["equity"],
                    )
        # add-market-snapshot（D6/T8）：行情快照告警造态——可选先 reset（清三表）再写
        # meta。两处都在**调用期**经 snapshot 模块 _default_db_path() 读
        # SNAPSHOT_DB_PATH（导入期冻结会让 E2E 的 env 注入失效）。
        if req.get("snapshot_reset"):
            from finance_agent.data.snapshot import reset_snapshot_db

            reset_snapshot_db()
        snapshot_seed = req.get("snapshot")
        if isinstance(snapshot_seed, dict) and snapshot_seed:
            from zoneinfo import ZoneInfo

            from finance_agent.data.snapshot import set_meta_many

            snapshot_meta = {str(key): str(value) for key, value in snapshot_seed.items()}
            # D5：fresh-DB 分支（无 last_attempt_at）不告警——造 stale 态必须补上
            snapshot_meta.setdefault(
                "last_attempt_at", datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            )
            set_meta_many(snapshot_meta)
        # add-screening-pipeline S6：选股页造数——可选先 reset（清五表 + 丢弃进程内
        # 队列单例）再写批次/池/队列。库路径调用期读 SCREENING_DB_PATH（store 各入口
        # env 口径）；队列为进程内单例（徽标态造数，不启动轮询/补位）。
        if req.get("screening_reset"):
            from finance_agent.screening.store import reset_screening_db

            reset_screening_db()
            # 队列状态在内存（库外）：清库不换实例会让旧徽标条目跨 reset 残留
            _reset_analyze_queue()
        screening_seed = req.get("screening")
        if isinstance(screening_seed, dict):
            from finance_agent.screening.store import record_screening_batch as _seed_batch

            # add-daily-ops-page T1.2：``batches`` = 历史多批次（每行可自带 ``pool``，
            # pool_size 随该行的池行数）；先落历史、后落单数 ``batch``——同日撞键时
            # 单数（当前批次）胜出，既有 payload 语义零漂移。
            batches_seed = screening_seed.get("batches")
            if isinstance(batches_seed, list):
                for history_row in batches_seed:
                    trade_date = history_row["trade_date"]
                    _seed_batch(
                        trade_date,
                        status=history_row.get("status", "ok"),
                        error=history_row.get("error"),
                        started_at=history_row.get("started_at", _now()),
                        finished_at=history_row.get("finished_at", _now()),
                        l1_passed=history_row.get("l1_passed", 0),
                        l2_top=history_row.get("l2_top", 0),
                        l3_keep=history_row.get("l3_keep", 0),
                        pool_rows=history_row.get("pool") or [],
                        selection_mode=history_row.get("selection_mode", "factor"),
                    )
            batch_seed = screening_seed.get("batch")
            if isinstance(batch_seed, dict):
                trade_date = batch_seed["trade_date"]
                _seed_batch(
                    trade_date,
                    status=batch_seed.get("status", "ok"),
                    error=batch_seed.get("error"),
                    started_at=batch_seed.get("started_at", _now()),
                    finished_at=batch_seed.get("finished_at", _now()),
                    l1_passed=batch_seed.get("l1_passed", 0),
                    l2_top=batch_seed.get("l2_top", 0),
                    l3_keep=batch_seed.get("l3_keep", 0),
                    pool_rows=screening_seed.get("pool") or [],
                    selection_mode=batch_seed.get("selection_mode", "factor"),
                )
            queue_seed = screening_seed.get("queue")
            if isinstance(queue_seed, list):
                _get_analyze_queue().seed(queue_seed)
        # 每日汇报造数（add-auto-pool-analysis-daily-brief）：E2E/前端测试用；
        # 同日期 upsert，不产生重复行。
        daily_brief_seed = req.get("daily_brief")
        if isinstance(daily_brief_seed, dict):
            from finance_agent.outcome.daily_brief import upsert_daily_brief as _seed_brief

            _seed_brief(
                str(daily_brief_seed.get("date") or datetime.now().strftime("%Y-%m-%d")),
                str(daily_brief_seed.get("status") or "ok"),
                str(daily_brief_seed.get("markdown") or ""),
                model=daily_brief_seed.get("model"),
                prompt_version=daily_brief_seed.get("prompt_version"),
                error=daily_brief_seed.get("error"),
            )
        # 语义记忆造数（delta add-memory-confirmation T1）：可选先 reset（清条款表）再写
        # 条款——逐条经既有落库（去重口径同生产：同文本已存在则跳过）后再按 status 走
        # 既有状态机置态，与真实确认/驳回路径同源。库路径调用期读 SESSIONS_DB_PATH。
        if req.get("memory_reset"):
            from finance_agent.memory.semantic import reset_clauses

            reset_clauses()
        memory_seed = req.get("memory")
        if isinstance(memory_seed, dict):
            from finance_agent.memory.semantic import (
                confirm_clause as _seed_confirm_clause,
            )
            from finance_agent.memory.semantic import (
                reject_clause as _seed_reject_clause,
            )
            from finance_agent.memory.semantic import (
                store_candidate_clauses as _seed_store_clauses,
            )

            for clause_row in memory_seed.get("clauses") or []:
                new_ids = _seed_store_clauses(
                    [str(clause_row["text"])],
                    source=clause_row.get("source", "monthly_batch"),
                    # evidence 原样落库（生产为 JSON 文本）：供记忆页结算样本展示造数
                    evidence=clause_row.get("evidence"),
                )
                # 同文本已存在（去重口径）→ 不重复入库，seed 保持幂等
                if not new_ids:
                    continue
                if clause_row.get("status") == "confirmed":
                    _seed_confirm_clause(new_ids[0])
                elif clause_row.get("status") == "rejected":
                    _seed_reject_clause(new_ids[0])
        # 因子库造数（delta add-factor-library-page T1.2）：可选先 reset（清三表）再
        # 写因子——逐条经既有落库 + 状态机走到目标态（candidate→active→degraded/
        # retired，与生产准入/退役路径同源），IC 经 record_ic 幂等写入。库路径调用期
        # 读 FACTOR_LIBRARY_DB_PATH（library 模块 env 口径）。
        if req.get("factors_reset"):
            from finance_agent.factors.library import reset_factor_library

            reset_factor_library()
        factors_seed = req.get("factors")
        if isinstance(factors_seed, dict):
            from finance_agent.factors.library import add_factor as _seed_add_factor
            from finance_agent.factors.library import record_ic as _seed_record_ic
            from finance_agent.factors.library import (
                set_pool_increment as _seed_set_pool_increment,
            )
            from finance_agent.factors.library import set_status as _seed_set_status

            for factor_row in factors_seed.get("factors") or []:
                target = str(factor_row.get("status", "candidate"))
                if target not in FACTOR_STATES:
                    # fail loud：静默回落会让 E2E 断言对着一条状态不符预期的行变红难归因
                    raise ValueError(f"非法种子状态 {target!r}；允许: {list(FACTOR_STATES)}")
                factor_id = _seed_add_factor(
                    expression=str(factor_row["expression"]),
                    hypothesis=str(factor_row.get("hypothesis", "")),
                    category=str(factor_row.get("category", "seed")),
                    status="candidate",
                    ast_json=factor_row.get("ast_json"),
                    source=str(factor_row.get("source", "proposal")),
                    alignment_score=factor_row.get("alignment_score"),
                )
                if factor_row.get("pool_increment") is not None:
                    _seed_set_pool_increment(factor_id, float(factor_row["pool_increment"]))
                if target != "candidate":
                    # active 的闸门结论与 validate.admit_candidates 逐字同源（ADMITTED）
                    _seed_set_status(factor_id, "active", detail={"reason": "ADMITTED"})
                if target == "retired" and factor_row.get("via_degraded"):
                    # 衰减后退役（真实路径 degraded → retired）：先落 DECAY 降级事件
                    # （detail 缺省 {"reason": "DECAY"}）再退役——供「衰减标记」列造态
                    _seed_set_status(
                        factor_id,
                        "degraded",
                        detail=factor_row.get("degraded_detail") or {"reason": "DECAY"},
                    )
                if target in ("degraded", "retired"):
                    # detail 由 seed 行给出（缺省 None——不臆造原因）
                    _seed_set_status(factor_id, target, detail=factor_row.get("detail"))
                for trade_date, rank_ic in factor_row.get("ic") or []:
                    _seed_record_ic(factor_id, str(trade_date), float(rank_ic))
        # 执行台账造数（delta add-broker-console T1.2）：可选先 reset（清 orders/
        # positions/cash 三表）再写 cash/positions/orders——逐条经既有落库入口
        # （ensure_account/write_cash/write_position/insert_order），与 paper 生产写
        # 路径同源；库路径调用期读 EXECUTION_DB_PATH（E2E 指向独立测试库
        # data/test-e2e-execution.db，绝不落到开发库 data/execution.db）。
        if req.get("broker_reset"):
            from finance_agent.execution.store import delete_all_broker_state

            delete_all_broker_state()
        broker_seed = req.get("broker")
        if isinstance(broker_seed, dict):
            _seed_broker_account(broker_seed)
        # 市场温度计造数（delta add-crowding-regime-positioning T8）：可选先 reset
        # （清 regime_daily 全表）再写行——行字段与 regime_daily DDL 列名逐字一致
        # （含 cap_source），经 upsert_regime_daily（生产同源写入口；trade_date 唯一
        # → 同日覆盖）落库；缺省字段如实落 NULL（不编造）。库路径调用期读
        # REGIME_DB_PATH（model._default_db_path env 口径，E2E 指向独立测试库
        # data/test-e2e-regime.db，绝不落到开发库 data/regime.db）。
        if req.get("regime_reset"):
            from finance_agent.outcome.regime.model import reset_regime_db

            reset_regime_db()
        regime_seed = req.get("regime")
        if isinstance(regime_seed, list):
            from finance_agent.outcome.regime.indicators import (
                INDICATOR_KEYS as _REGIME_INDICATOR_KEYS,
            )
            from finance_agent.outcome.regime.model import (
                PCTILE_COLUMNS as _REGIME_PCTILE_COLUMNS,
            )
            from finance_agent.outcome.regime.model import (
                upsert_regime_daily as _seed_regime_daily,
            )

            for regime_row in regime_seed:
                _seed_regime_daily(
                    str(regime_row["trade_date"]),
                    {key: regime_row.get(key) for key in _REGIME_INDICATOR_KEYS},
                    {key: regime_row.get(key) for key in _REGIME_PCTILE_COLUMNS},
                    computed_at=str(regime_row.get("computed_at") or _now()),
                    cap_source=regime_row.get("cap_source"),
                )
        # delta add-event-driven-lane：事件线造数（四表经既有落库入口写入：预告
        # upsert 幂等、分诊覆盖、清单去重、记账唯一）。库路径调用期读 EVENTS_DB_PATH
        # （model._default_db_path env 口径，E2E 指向独立测试库 data/test-e2e-events.db，
        # 绝不落到开发库 data/events.db）。events_reset 先清四表，供同套件多 spec 复位。
        if req.get("events_reset"):
            from finance_agent.outcome.events.model import delete_all_events_state

            delete_all_events_state()
        events_seed = req.get("events")
        if isinstance(events_seed, dict):
            from finance_agent.outcome.events.model import (
                insert_journal as _seed_event_journal,
            )
            from finance_agent.outcome.events.model import (
                insert_watchlist as _seed_event_watchlist,
            )
            from finance_agent.outcome.events.model import (
                record_triage as _seed_event_triage,
            )
            from finance_agent.outcome.events.model import (
                settle_journal as _seed_event_settle,
            )
            from finance_agent.outcome.events.model import (
                upsert_forecasts as _seed_forecasts,
            )

            forecasts = events_seed.get("forecasts") or []
            if forecasts:
                _seed_forecasts([dict(row) for row in forecasts])
            for triage_row in events_seed.get("triage") or []:
                _seed_event_triage(
                    str(triage_row["forecast_key"]),
                    verdict=str(triage_row["verdict"]),
                    reason=str(triage_row.get("reason") or ""),
                    evidence=triage_row.get("evidence") or [],
                    model=triage_row.get("model"),
                    prompt_fingerprint=triage_row.get("prompt_fingerprint"),
                    triaged_at=triage_row.get("triaged_at"),
                )
            for item in events_seed.get("watchlist") or []:
                _seed_event_watchlist(
                    str(item["forecast_key"]),
                    source=str(item.get("source") or "earnings"),
                    status=str(item.get("status") or "pending"),
                    session_id=item.get("session_id"),
                    created_at=item.get("created_at"),
                )
            for row in events_seed.get("journal") or []:
                row_id = _seed_event_journal(
                    forecast_key=str(row["forecast_key"]),
                    source=str(row.get("source") or "earnings"),
                    symbol=str(row["symbol"]),
                    entry_date=str(row["entry_date"]),
                    entry_price=float(row["entry_price"]),
                    horizon_days=int(row.get("horizon_days") or 5),
                    note=row.get("note"),
                )
                if str(row.get("status") or "open") == "settled":
                    _seed_event_settle(
                        row_id,
                        settle_date=str(row["settle_date"]),
                        settle_price=float(row["settle_price"]),
                        raw_return=float(row["raw_return"]),
                        note=row.get("note"),
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
    # 追问时跳过历史事件重放：用当前 journal 最大 seq 作为 after_seq，
    # 避免上一轮 done 终态事件导致 registry.subscribe 提前 return（SSE 流终止）。
    # 须在 registry.start 前取值：新会话的 session_created 由任务发布，
    # start 后再取 max_seq 可能跳过该事件，前端拿不到 session_id
    # （当前会话 ID 缺失 → 运行中管线卡被隐藏，issue #424/#328）。
    afterSeq = await asyncio.to_thread(get_max_event_seq, session_id)
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


# add-paper-broker：双曲线损耗标注文案（逐字，前端与接口共用口径）
_PAPER_CURVE_NOTE = "两条曲线同一批决策同一基准期；差值为执行损耗"


def _paper_overview_block(as_of: str) -> dict[str, Any] | None:
    """paper 仿真净值块：既有 equity_curve（虚拟结算）与 paper_equity 共同日期对齐。

    对齐规则（计划 B3）：仅共同日期；两序列都只取 ``≤ as_of`` 的行（基期只向
    过去回退的防前视纪律对两条曲线同样适用）。paper_equity 是绝对权益（cash +
    持仓市值），虚拟序列是归一净值——按共同基期把 paper 缩放到虚拟净值同一
    标尺（基期两线重合），差值才可比；``loss_pct`` = 最新共同日
    ``(paper/virtual − 1) × 100`` 保留 1 位。无 paper 数据或无可对齐日期 → None
    （前端隐藏系列）。
    """
    from finance_agent.execution.store import list_paper_equity

    paper_rows = [r for r in list_paper_equity() if str(r["trade_date"]) <= as_of]
    if not paper_rows:
        return None
    virtual = {
        str(r["curve_date"]): float(r["agent_nav"])
        for r in list_equity_curve()
        if str(r["curve_date"]) <= as_of and r["agent_nav"]
    }
    aligned = [
        (str(r["trade_date"]), virtual[str(r["trade_date"])], float(r["equity"]))
        for r in paper_rows
        if str(r["trade_date"]) in virtual and float(r["equity"]) != 0.0
    ]
    if not aligned:
        return None
    base_date, virtual_base, paper_base = aligned[0]
    series: list[dict[str, Any]] = [
        {
            "date": date,
            "virtual_equity": round(nav, 4),
            "paper_equity": round(equity / paper_base * virtual_base, 4),
        }
        for date, nav, equity in aligned
    ]
    latest = series[-1]
    loss_pct = round((latest["paper_equity"] / latest["virtual_equity"] - 1) * 100, 1)
    return {
        "series": series,
        "loss_pct": loss_pct,
        "base_date": base_date,
        "note": _PAPER_CURVE_NOTE,
    }


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
    add-paper-broker(B3):追加 `paper` 块(虚拟 eq 曲线 vs 仿真净值共同日期对齐 +
    执行损耗 loss_pct);无 paper 数据 → `paper: null`。
    """
    if version is None:
        active = await asyncio.to_thread(get_active_agent)
        version = active["version_seq"] if active else None
    as_of = _track_as_of()
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
        "as_of": as_of,
        "disclaimer": _DISCLAIMER,
        "win_rate": None if insufficient else stats["win_rate"],
        "portfolio": portfolio,
        # add-paper-broker（B3）：虚拟 vs 仿真双曲线（共同日期对齐 + 执行损耗）
        "paper": await asyncio.to_thread(_paper_overview_block, as_of),
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


# ── 策略族成绩单（delta add-strategy-family-scorecards）：只读四槽读数 ──
#
# 口径权威：docs/evals/metrics.md §1.13（2026-10-18 登记）。本端点为纯读层：族注册表
# 驱动（未注册数据源不出现），四槽缺数据一律 null + 原因码（MUST NOT 折算 0）；
# 胜率仅展示位（四槽之后），MUST NOT 进入自动处置/门禁/告警。库路径由 outcome 侧
# 调用期读 env（SESSIONS_DB_PATH / EXECUTION_DB_PATH / SNAPSHOT_DB_PATH）。


@app.get("/api/scorecards")
async def get_scorecards_endpoint() -> dict[str, Any]:
    """策略族成绩单（只读）：注册表全族四槽读数 + 口径注释；空数据族如实返回。"""
    from finance_agent.outcome.scorecards import get_scorecards

    return await asyncio.to_thread(get_scorecards)


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


def _git_commit() -> str | None:
    """返回当前版本短哈希（前 12 位）；非 git 环境返回 None。

    构建期注入的 ``GIT_COMMIT``（Dockerfile ARG → ENV）优先于 git 探测：镜像里
    没有 ``.git``，原实现恒返回 None（``/api/run-info`` 的 git_commit 长期为空，
    2026-10-22 观测项）。注入值同样截 12 位，与本地探测口径一致。

    环境变量在调用时读取（不进 lru_cache）：构建期注入是静态的，但测试与
    本地热切换需要可观察；仓库探测（子进程）单独缓存，避免每请求 fork git。
    """
    built = (
        os.environ.get("GIT_COMMIT") or os.environ.get("FINANCE_AGENT_GIT_COMMIT") or ""
    ).strip()
    if built:
        return built[:12]
    return _git_commit_from_repo()


@functools.lru_cache(maxsize=1)
def _git_commit_from_repo() -> str | None:
    """向上搜索仓库根并读 HEAD（本地开发兜底；容器内无 .git 返回 None）。

    不同部署布局下 ``__file__`` 深度可能不同，故用向上搜索而非固定 parents 下标。
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
        version = importlib.metadata.version("investment-research-agents")
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


@app.get("/api/snapshot/status")
async def snapshot_status() -> dict:
    """行情快照新鲜度（add-market-snapshot D5；前端横幅 60s 轮询端点）。

    **永不 500**（ops 纪律）：读库/解析任何异常 → 降级返回
    ``last_error="status_unavailable"`` 且 ``stale=False``（宁可静默一轮，不打断
    前端轮询循环）。正常载荷 ``{latest_date, stale, message, last_error,
    last_error_date}``，文案由 ``compute_snapshot_status`` 逐字给定。
    """

    def _payload() -> dict:
        return compute_snapshot_status(get_all_meta())

    try:
        return await asyncio.to_thread(_payload)
    except Exception:
        _logger.exception("快照状态读取失败，降级返回 status_unavailable")
        return {
            "latest_date": None,
            "stale": False,
            "message": None,
            "last_error": "status_unavailable",
            "last_error_date": None,
        }


@app.get("/api/regime")
async def regime_status() -> dict:
    """市场拥挤度 regime 只读端点（add-crowding-regime-positioning）。

    返回最新判定（``state`` / ``multiplier`` / ``reasons``）+ 四指标（当日值 +
    历史分位）+ 近 250 交易日历史序列 + ``as_of`` / ``age_days`` / ``stale``；
    无记录或不可解析 → 200 + ``available=false`` + 原因码（``missing`` /
    ``corrupt``），MUST NOT 500 或以伪数据冒充。只读：不写/不改任何状态
    （阻塞 IO 走线程池，不占事件循环）。
    """
    return await asyncio.to_thread(build_regime_payload)


# ── 选股（add-screening-pipeline T3；S3/S4：池 / 状态 / 批量深度分析） ──

# 批量分析并发上限：设置中心「会话管理」可配（delta add-analysis-concurrency-setting）；
# 来源优先级 = 设置文件 > env SCREENING_ANALYZE_CONCURRENCY > 默认 10。
# 进程内队列单例（懒创建：值于调用期读取；单 worker 前提同 StreamRegistry）
_analyze_queue: AnalyzeQueue | None = None

_SCREENING_STATUS_DEGRADED: dict[str, Any] = {
    "latest_success_date": None,
    "failed_date": None,
    "message": None,
    "stale": False,
}


def _analyze_concurrency() -> int:
    """并发上限：设置文件 > env > 默认 10（来源解析见 analysis_concurrency）。"""
    return int(analysis_concurrency.resolve()["value"])


def _start_screening_analysis(symbol: str, session_id: str) -> None:
    """队列补位启动器（S3：至终态后启动下一个）：为已建会话启动后台管线。

    与 ``/api/analyze`` fast path 同款 ``PipelineRunner.start`` 装配（事件经
    journal 桥接，SSE 侧零改动）；由 ``AnalyzeQueue`` 在补位时调用（事件循环内）。
    """
    loop = asyncio.get_running_loop()
    analysis_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    PipelineRunner.start(
        session_id,
        _screening_event_source(symbol, session_id, analysis_id, start_time),
        {
            "layerTree": build_layer_tree(),
            "currentNodeId": "",
            "progress": 0.0,
            "updatedAt": int(time.time() * 1000),
            "pipeline_start_ts": int(start_time * 1000),
        },
        loop=loop,
    )


def _get_analyze_queue() -> AnalyzeQueue:
    """进程内分析队列单例（懒创建：env 调用期读取；测试可整体替换）。

    ``starter`` 注入「补位即启动后台管线」——并发上限约束的是真实在跑的分析数，
    排队条目在建会话后**不启动**，等空位（S3）。
    """
    global _analyze_queue
    if _analyze_queue is None:
        _analyze_queue = AnalyzeQueue(
            max_concurrency=_analyze_concurrency(), starter=_start_screening_analysis
        )
    return _analyze_queue


def _reset_analyze_queue() -> None:
    """丢弃进程内队列单例（TESTING ``screening_reset`` 用；下次取用懒建新实例）。

    队列状态在进程内存里（库外）：只清 screening 库无法让徽标态归零——reset 后页面
    仍会显示此前 seed/累积的条目。进程在跑的真实分析会话不受影响（仅队列条目清空）。
    """
    global _analyze_queue
    _analyze_queue = None


# ── 批量分析并发设置（delta add-analysis-concurrency-setting）──


class AnalysisConcurrencyUpdate(BaseModel):
    """保存入参：用 Any 接收，再由 analysis_concurrency.validate 统一校验（非法 → 400）。"""

    value: Any = None


@app.get("/api/settings/analysis-concurrency")
async def get_analysis_concurrency() -> dict[str, Any]:
    """并发设置（只读）：生效值 + 来源（file/env/default）+ 合法区间 + 环境变量披露。"""
    return await asyncio.to_thread(analysis_concurrency.resolve)


@app.put("/api/settings/analysis-concurrency")
async def put_analysis_concurrency(req: AnalysisConcurrencyUpdate) -> dict[str, Any]:
    """保存并发设置：校验 1–20 → 原子落盘 → 立即应用到当前队列（提高即补位）。"""
    try:
        payload = await asyncio.to_thread(analysis_concurrency.save, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    queue = _get_analyze_queue()
    if queue.max_concurrency != payload["value"]:
        queue.set_max_concurrency(int(payload["value"]))
    return payload


# 降级披露文案（add-screening-empty-library-fallback：单源在后端，前端原样渲染；
# MUST 明示「非因子信号」——spec「无可打分因子降级通道」披露义务）
_SCREENING_FALLBACK_NOTICE = (
    "因子库无可打分因子：本次为降级模式，按近 20 日日均成交额排序（非因子信号）"
)


def _screening_pool_payload(queue_snapshot: list[dict]) -> dict[str, Any]:
    """池端点载荷（同步体，供 to_thread 包裹）：最近成功池 + 最近尝试状态 + 队列。

    披露（add-screening-empty-library-fallback）：``selection_mode`` 取自最近成功
    批次；``fallback`` 时附 ``notice``（明示非因子信号），``factor`` 时为 ``None``。
    """
    latest = latest_screening_batch()
    successful = latest_successful_pool()
    selection_mode = successful["batch"].get("selection_mode") if successful else None
    return {
        "batch_date": successful["batch"]["trade_date"] if successful else None,
        "batch_status": latest["status"] if latest else None,
        # 最近一次尝试非 ok（含失败）→ stale：页面横幅可见，池仍展示最近成功批次
        "stale": bool(latest is not None and latest["status"] != "ok"),
        "selection_mode": selection_mode,
        "notice": _SCREENING_FALLBACK_NOTICE if selection_mode == "fallback" else None,
        "pool": successful["pool"] if successful else [],
        "queue": queue_snapshot,
    }


def _screening_event_source(
    symbol: str, session_id: str, analysis_id: str, start_time: float
) -> Callable[[], Generator[str, None, None]]:
    """批量分析的管线事件源（与 /api/analyze fast path 同款 ``_run_graph_streaming`` 装配）。"""

    def _source() -> Generator[str, None, None]:
        return _run_graph_streaming(
            symbol,
            "",
            AnalyzeRequest(query=f"{symbol} 深度分析", stock_code=symbol),
            analysis_id,
            start_time,
            session_id=session_id,
            llm_config=None,
        )

    return _source


@app.get("/api/screening/pool")
async def screening_pool() -> dict[str, Any]:
    """选股页取数（S4）：最近成功批次池（含贡献分解）+ 批次状态 + 队列快照。

    无成功批次 → ``batch_date=None`` + 空池；最近一次尝试失败 → ``stale=True``，
    池仍为最近成功批次（spec：页面标注其日期）。
    """
    queue_snapshot = _get_analyze_queue().snapshot()
    return await asyncio.to_thread(_screening_pool_payload, queue_snapshot)


@app.get("/api/screening/status")
async def screening_status() -> dict[str, Any]:
    """选股批次失败横幅通道（S4）：**永不 500**，读库异常降级 200。

    正常载荷 ``{latest_success_date, failed_date, message, stale}``；失败文案逐字
    「选股批次失败（最近成功: YYYY-MM-DD）」（无成功批次时括注「无」）。
    """

    def _payload() -> dict[str, Any]:
        latest = latest_screening_batch()
        success = latest_successful_batch()
        success_date = success["trade_date"] if success else None
        if latest is None or latest["status"] == "ok":
            return {
                "latest_success_date": success_date,
                "failed_date": None,
                "message": None,
                "stale": False,
            }
        return {
            "latest_success_date": success_date,
            "failed_date": latest["trade_date"],
            "message": f"选股批次失败（最近成功: {success_date or '无'}）",
            "stale": True,
        }

    try:
        return await asyncio.to_thread(_payload)
    except Exception:  # noqa: BLE001 -- 横幅通道永不 500（前端轮询不得被后端故障打断）
        _logger.exception("选股批次状态读取失败，降级返回")
        return dict(_SCREENING_STATUS_DEGRADED)


@app.get("/api/screening/batches")
async def screening_batches(limit: int = DEFAULT_BATCH_HISTORY_LIMIT) -> list[dict[str, Any]]:
    """选股批次历史（delta add-daily-ops-page T1；spec「批次历史查询」）。

    按交易日期倒序返回近 ``limit`` 批次（默认 20），字段 = 日期 / 状态（ok/failed）/
    错误 / 起止时间 / 各层计数（l1_passed/l2_top/l3_keep）/ 池数（pool_size）。
    空库 → 200 + 空列表（不以错误冒充「无历史」）；``limit`` 钳制到
    ``[1, MAX_BATCH_HISTORY_LIMIT]``（越界不 422——越界值只影响窗口大小，
    钳制后再交给 store 的严格校验）。只读端点：无任何写操作暴露。
    """
    clamped = max(1, min(limit, MAX_BATCH_HISTORY_LIMIT))
    return await asyncio.to_thread(list_batches, clamped)


def _screening_freshness_removals(
    pool_rows: list[dict[str, Any]], batch_date: str | None
) -> list[dict[str, Any]]:
    """深度分析前的信号时效性校验（spec「信号时效性校验」；T2 校验接入 analyze）。

    以**最新可得快照**（``latest_snapshot_date``）为基准日，对池内符号重校验停牌/
    一字涨跌停/新事件（``check_signal_freshness``）；不通过者返回 findings
    （``[{symbol, reason, detail}]``）并落 ``screening_removals`` 审计行（基准日 =
    快照日；审计写失败记 warning 不阻断返回）。

    数据不可得 → 返回 ``[]``（记日志，不臆造移除）：① 快照库无行（无「最新可得
    状态」可核对）；② 快照最新日期早于池批次日（无更新状态）。读快照失败同样
    降级——本校验是重检（不是主门禁），可用性故障不得把批量分析端点打成 500、
    也不得以空 findings 冒充「已校验通过」。三条降级路径（含跳过）**一律记
    WARNING 及以上**：spec 要求的重校验被跳过着对运维必须可见，不得静默通过。
    """
    return screening_freshness_removals(pool_rows, batch_date)


def _enqueue_pool_analysis(symbol: str, session_id: str) -> bool:
    """把自动批次的一个标的投递到主循环的 AnalyzeQueue（线程安全）。

    调度器 job 在 APScheduler 线程里运行，``AnalyzeQueue._promote`` 需要运行中的
    事件循环；用 ``call_soon_threadsafe`` 把入队动作送回 uvicorn 主循环，才能复用
    ``_start_screening_analysis`` 的 ``PipelineRunner.start(loop=...)`` 与 SSE/journal。
    """
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return False
    loop.call_soon_threadsafe(_get_analyze_queue().enqueue, symbol, session_id)
    return True


def submit_pool_analysis_batch(
    *, max_symbols: int | None = None, timeout: float = 120.0
) -> dict[str, Any]:
    """调度线程入口：在 uvicorn 主循环上准备并投递自动分析批次。

    只等待「建会话 + 入队」完成（通常 <1s/标的），不等待分析本身；分析由
    ``AnalyzeQueue`` 在后台补位执行并复用既有 SSE/journal。
    """
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        raise RuntimeError("主事件循环不可用：自动选股分析只能在后端进程内触发")

    async def _run() -> dict[str, Any]:
        return await asyncio.to_thread(
            prepare_pool_analysis_batch,
            max_symbols=max_symbols,
            enqueue=_enqueue_pool_analysis,
        )

    return asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=timeout)


def submit_report_trigger_batch(
    *, dry_run: bool = False, doc_keys: list[str] | None = None, timeout: float = 120.0
) -> dict[str, Any]:
    """调度线程入口：研报触发关联分析（默认关闭由 run_report_trigger_daily 判定）。"""
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        raise RuntimeError("主事件循环不可用：研报触发分析只能在后端进程内触发")

    async def _run() -> dict[str, Any]:
        from finance_agent.screening.report_trigger import (
            prepare_report_analysis_batch,
            run_report_trigger_daily,
        )

        if doc_keys is None and not dry_run:
            return await asyncio.to_thread(run_report_trigger_daily, enqueue=_enqueue_pool_analysis)
        return await asyncio.to_thread(
            prepare_report_analysis_batch,
            doc_keys=doc_keys,
            dry_run=dry_run,
            enqueue=None if dry_run else _enqueue_pool_analysis,
        )

    return asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=timeout)


@app.post("/api/research/reports/{doc_key}/trigger-analysis")
async def trigger_report_analysis(doc_key: str, dry_run: bool = False) -> Any:
    """按投喂文档触发关联标的分析（add-report-triggered-analysis）。

    ``dry_run=true`` 只返回 ``would_trigger`` 与跳过原因，不建会话、不写库、不调 LLM。
    """
    from finance_agent.screening.report_trigger import prepare_report_analysis_batch

    return await asyncio.to_thread(
        prepare_report_analysis_batch,
        doc_keys=[doc_key],
        dry_run=dry_run,
        enqueue=None if dry_run else _enqueue_pool_analysis,
    )


@app.post("/api/screening/analyze")
async def screening_analyze() -> Any:
    """精选池批量深度分析（S3/S4 + 时效性校验）：独立 session + 复用管线，并发上限 2。

    - 入队前**以最新可得快照状态重校验**（spec「信号时效性校验」）：停牌/一字
      涨跌停/新事件标的不入队（``removed`` 返回 + 审计落库），其余标的照常；
    - 每个标的经 ``create_session`` + ``append_chat`` + ``set_pipeline_anchor`` +
      ``update_session_for_clarify``（与 ``/api/analyze`` 新会话路径同款）建独立
      session 并入队；**补位者才真正启动**（``PipelineRunner.start`` 由队列的
      starter 在空位时调用——并发上限约束真实在跑的分析数，事件仍经既有
      journal/subscribe SSE 通道，本端点不改流式纪律）；
    - 池空 → 409 显式错误（不静默返回空队列）；
    - 返回 ``{started: [running...], queued: [queued...], removed: [校验移除...]}``；
      终态由队列按 session status 轮询驱动补位（并发始终 ≤ 上限）。
    """
    successful = await asyncio.to_thread(latest_successful_pool)
    pool = successful["pool"] if successful else []
    if not pool:
        return JSONResponse(
            status_code=409,
            content={
                "error": "screening_pool_empty",
                "message": "精选池为空：无可分析的标的（先跑选股日批）",
            },
        )
    batch_date = successful["batch"]["trade_date"] if successful else None
    removals = await asyncio.to_thread(_screening_freshness_removals, pool, batch_date)
    removed_symbols = {str(item["symbol"]) for item in removals}
    queue = _get_analyze_queue()
    for row in pool:
        symbol = str(row["symbol"])
        if symbol in removed_symbols:
            continue  # 时效性校验不通过：本次不移入深度分析（原因见 removed/审计行）
        display_name = f"{symbol} 选股分析"
        session_id = await asyncio.to_thread(
            create_session,
            stock_code="",
            stock_name="",
            display_name=display_name,
            status="clarifying",
            session_type="analysis",
        )
        await asyncio.to_thread(append_chat, session_id, "user", f"对 {symbol} 进行深度分析")
        await asyncio.to_thread(set_pipeline_anchor, session_id)
        await asyncio.to_thread(
            update_session_for_clarify,
            session_id,
            stock_code=symbol,
            stock_name="",
            status="running",
        )
        append_session_event(
            session_id,
            {
                "type": "session_created",
                "session_id": session_id,
                "display_name": display_name,
                "timestamp": _now(),
            },
        )
        queue.enqueue(symbol, session_id)
    snapshot = queue.snapshot()
    return {
        "started": [item for item in snapshot if item["state"] == "running"],
        "queued": [item for item in snapshot if item["state"] == "queued"],
        "removed": removals,
    }


# ── 事件观察清单（delta add-event-driven-lane T5：只读清单 + 深度分析接线） ──


def _watchlist_evidence(raw: Any) -> Any:
    """证据 JSON 文本 → 解析后的列表；损坏如实返回 ``None``（不伪装成 []、不编造）。"""
    if raw is None:
        return None
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        _logger.warning("观察清单证据 JSON 损坏，按 null 返回: %.80s", raw)
        return None
    return parsed


def _cbb_note_payload(raw: Any) -> dict[str, Any] | None:
    """条款事件条目的规则化注记（``event_watchlist.note`` JSON）→ dict。

    损坏/非对象 → ``None`` + WARNING（四键中倒计时如实 null，不伪装成 0 或编造理由）。
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        _logger.warning("条款事件注记 JSON 损坏，按 null 返回: %.80s", raw)
        return None
    if not isinstance(parsed, dict):
        _logger.warning("条款事件注记非对象，按 null 返回: %.80s", raw)
        return None
    return parsed


def _event_watchlist_payload(row: dict[str, Any]) -> dict[str, Any]:
    """清单行（库列名）→ API 载荷（前端契约字段名）。

    条款事件条目（``source=cbb``）额外携带**冻结四键**（跨变更契约，2026-10-18 裁决）：
    ``price``（元，最新交易日行情）/ ``premium_rate``（%，标的表）/ ``remaining_size``
    （亿元，事件行剩余规模）/ ``call_countdown_days``（交易日个数，构建时按日历推导后落注记）
    ——缺值一律 null（前端 ``—``），MUST NOT 折算；``reason`` = 规则化理由文本。
    """
    payload = {
        "id": row.get("id"),
        "forecast_key": row.get("forecast_key"),
        "source": row.get("source"),
        "status": row.get("status"),
        "session_id": row.get("session_id"),
        "created_at": row.get("created_at"),
        "code": row.get("code"),
        "name": row.get("name"),
        "report_period": row.get("report_period"),
        "forecast_type": row.get("forecast_type"),
        "change_low": row.get("change_low"),
        "change_high": row.get("change_high"),
        "announce_date": row.get("announce_date"),
        "available_at": row.get("available_at"),
        "verdict": row.get("verdict"),
        "reason": row.get("reason"),
        "evidence": _watchlist_evidence(row.get("evidence_json")),
        "model": row.get("model"),
        "prompt_fingerprint": row.get("prompt_fingerprint"),
        "triaged_at": row.get("triaged_at"),
    }
    if row.get("source") == WATCHLIST_SOURCE_CBB:
        note = _cbb_note_payload(row.get("note")) or {}
        payload["code"] = row.get("cb_code")
        payload["name"] = row.get("cb_name")
        payload["announce_date"] = row.get("cb_announce_date")
        payload["price"] = row.get("cb_close")
        payload["premium_rate"] = row.get("cb_premium_rate")
        payload["remaining_size"] = row.get("cb_remain_scale")
        payload["call_countdown_days"] = note.get("call_countdown_days")
        payload["reason"] = note.get("reason")
    return payload


@app.get("/api/events/watchlist")
async def events_watchlist(source: str | None = None) -> dict[str, Any]:
    """事件观察清单（只读）：预告要素 / 分诊结论与理由 / 证据引用 / 公告日 / 状态 / session。

    ``?source=`` 过滤清单来源（缺省全量）；空库 → 200 + 空列表（不 500）。
    **零写入**：本端点不创建/修改任何业务状态（分诊与清单落库只在日批通道）。
    """
    rows = await asyncio.to_thread(list_watchlist, source=source)
    return {"items": [_event_watchlist_payload(row) for row in rows]}


@app.post("/api/events/watchlist/{item_id}/analyze")
async def events_watchlist_analyze(item_id: int) -> Any:
    """发起深度分析（复用既有筛选队列，`source=event` 语义）：

    - 建会话段与 ``/api/screening/analyze`` **逐字同款**（create_session +
      append_chat + set_pipeline_anchor + update_session_for_clarify +
      append_session_event + ``queue.enqueue``）——复用 ``_get_analyze_queue()``
      单例，同并发上限，MUST NOT 另建分析/并发通道；
    - 重复点击（条已有 ``session_id``）→ 幂等返回既有状态（``already_started=true``），
      MUST NOT 重复入队；
    - **条款事件条目（``source=cbb``）v1 禁用本入口 → 409**（评审 F3 控制器裁决）：
      cbb 行的标的代码在 ``cb_code``（业绩预告侧 ``code`` 为 NULL），若放行会以空标的
      建会话并入队（``symbol=""``）——空标的分析无意义且污染队列，故 v1 明拒；
    - 未知 id → 404；成功后条目置 ``analyzing`` 并关联 session。
    """
    item = await asyncio.to_thread(get_watchlist_item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"未知观察清单条目: {item_id}")
    if item.get("source") == WATCHLIST_SOURCE_CBB:
        raise HTTPException(status_code=409, detail="条款事件暂不支持发起深度分析（v1）")
    queue = _get_analyze_queue()
    existing_session = item.get("session_id")
    if existing_session:
        # 幂等：重复点击不重复建会话、不重复入队，返回既有状态
        snapshot = queue.snapshot()
        queue_state = next(
            (e["state"] for e in snapshot if e["session_id"] == existing_session), None
        )
        return {
            "id": item_id,
            "session_id": existing_session,
            "status": item.get("status"),
            "queue_state": queue_state,
            "already_started": True,
        }

    symbol = str(item.get("code") or "")
    display_name = f"{symbol} 事件分析"
    session_id = await asyncio.to_thread(
        create_session,
        stock_code="",
        stock_name="",
        display_name=display_name,
        status="clarifying",
        session_type="analysis",
    )
    await asyncio.to_thread(append_chat, session_id, "user", f"对 {symbol} 进行深度分析")
    await asyncio.to_thread(set_pipeline_anchor, session_id)
    await asyncio.to_thread(
        update_session_for_clarify,
        session_id,
        stock_code=symbol,
        stock_name="",
        status="running",
    )
    append_session_event(
        session_id,
        {
            "type": "session_created",
            "session_id": session_id,
            "display_name": display_name,
            "source": "event",  # 来源标记（spec：事件线发起复用既有队列，标记 event）
            "timestamp": _now(),
        },
    )
    queue.enqueue(symbol, session_id)
    await asyncio.to_thread(mark_watchlist_analyzing, item_id, session_id=session_id)
    snapshot = queue.snapshot()
    queue_state = next((e["state"] for e in snapshot if e["session_id"] == session_id), None)
    return {
        "id": item_id,
        "session_id": session_id,
        "status": "analyzing",
        "queue_state": queue_state,
        "already_started": False,
    }


# ── 语义记忆确认（delta add-memory-confirmation：审批队列暴露 + 人工确认门禁） ──

# 条款载荷字段：id / 正文 / 来源 / 提炼时间 / 状态 / 结算样本（evidence，可空）。
# 状态机与注入门禁（仅 confirmed 注入）全部沿用 memory.semantic——本层零业务复制。


def _memory_clause_payload(row: dict[str, Any]) -> dict[str, Any]:
    """条款行（库列名）→ API 载荷（前端契约字段名）。"""
    return {
        "id": str(row.get("clause_id")),
        "text": row.get("text"),
        "status": row.get("status"),
        "source": row.get("source"),
        "created_at": row.get("created_at"),
        "evidence": row.get("evidence"),
    }


@app.get("/api/memory/clauses")
async def memory_clause_list(status: str | None = None) -> dict[str, Any]:
    """语义记忆条款列表（只读）：``?status=`` 三态过滤，默认 pending（审批队列默认视图）。

    未知 status → 400 显式拒绝（不静默回落默认或全量：静默回落会让调用方把结果
    误读成所请求的状态）。缺库/无表 → 空列表（读路径既有 fail-open 语义）。

    函数名刻意避开条款库表名的连写：门禁测试要求该表名只出现在
    memory/semantic.py（无第二读入口），命名撞串会让门禁对 api.py 误报。
    """
    effective = status if status is not None else "pending"
    if effective not in CLAUSE_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知条款状态: {effective}")
    rows = await asyncio.to_thread(list_clauses, effective)
    return {"status": effective, "clauses": [_memory_clause_payload(row) for row in rows]}


@app.post("/api/memory/clauses/{clause_id}/confirm")
async def memory_clause_confirm(clause_id: str) -> dict[str, Any]:
    """人工确认：pending → confirmed（此后即刻参与风控辩论注入）。

    幂等：对已确认条款重复确认仍 200 并返回当前状态（既有状态机 UPDATE 命中即 True）；
    未知 id → 404（写语义 MUST NOT 静默成功）。
    """
    if not await asyncio.to_thread(confirm_clause, clause_id):
        raise HTTPException(status_code=404, detail=f"未知条款: {clause_id}")
    return {"id": clause_id, "status": "confirmed"}


@app.post("/api/memory/clauses/{clause_id}/reject")
async def memory_clause_reject(clause_id: str) -> dict[str, Any]:
    """人工驳回：pending → rejected（永不注入）。幂等与 404 语义同确认端点。"""
    if not await asyncio.to_thread(reject_clause, clause_id):
        raise HTTPException(status_code=404, detail=f"未知条款: {clause_id}")
    return {"id": clause_id, "status": "rejected"}


# ── 因子库只读查询（delta add-factor-library-page：暴露 + 呈现，零业务复制） ──
#
# 写路径铁律：本层只包装 factors.library 既有只读面（list_factors / list_events /
# list_ic_series），不暴露任何写操作——因子入库、状态变更、参数调整仍只经五道闸与
# 衰减监控路径。派生字段口径：
#   - icir_60d / ic_mean_60d：复用 screening.ranker.recent_ic_stats（近
#     FACTOR_IC_WINDOW=60 个交易日，与 tools.IC_WINDOW / DECAY_WINDOW 同口径）；
#     ic_points 为窗口内 IC 记录数——无记录时 icir 按既有口径记 0.0，界面据此显示「—」
#     而非把 0.0 冒充为真实读数；
#   - name：builtin 注册词表（builtin.BUILTIN_FACTORS）作品名；非 builtin 或表达式
#     不在词表 → None（库内无名称列，不臆造；界面回退显示因子 id）；
#   - last_event：最近一条事件（准入/降级/退役及时间）——闸门结论落库的唯一形态
#     （准入经 set_status(active, detail={"reason": "ADMITTED"})，见 validate.py）；
#   - decayed：事件历史中出现过 to_status='degraded'（衰减监控降级史；退役后标记仍在）。


def _factor_name(row: dict[str, Any]) -> str | None:
    """builtin 因子的注册名（词表逐字）；非 builtin 来源 / 未命中 → None（不臆造）。"""
    if str(row.get("source") or "") != "builtin":
        return None
    expression = str(row.get("expression") or "")
    return next((item.name for item in BUILTIN_FACTORS if item.expression == expression), None)


def _factor_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """事件行 → API 载荷（detail 为 JSON 解析后的 dict，reason 提升为平铺字段）。"""
    detail = event.get("detail")
    reason = detail.get("reason") if isinstance(detail, dict) else None
    return {
        "id": event.get("id"),
        "factor_id": event.get("factor_id"),
        "event": event.get("event"),
        "from_status": event.get("from_status"),
        "to_status": event.get("to_status"),
        "reason": reason,
        "detail": detail,
        "created_at": event.get("created_at"),
    }


def _factor_summary_payload(
    row: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    ic_stats: dict[int, tuple[float, float]],
    ic_points: int,
) -> dict[str, Any]:
    """因子行 + 事件 + IC 统计 → 列表/详情共用的摘要载荷。"""
    factor_id = int(row["id"])
    ic_mean, icir = ic_stats.get(factor_id, (0.0, 0.0))
    return {
        # 行原值（库内列逐字透传；前端只消费其需要的子集）
        "id": factor_id,
        "expression": row.get("expression"),
        "hypothesis": row.get("hypothesis"),
        "category": row.get("category"),
        "status": row.get("status"),
        "ast_json": row.get("ast_json"),
        "source": row.get("source"),
        "alignment_score": row.get("alignment_score"),
        "pool_increment": row.get("pool_increment"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # 派生字段（口径见本节顶部注释）
        "name": _factor_name(row),
        "ic_mean_60d": ic_mean,
        "icir_60d": icir,
        "ic_points": ic_points,
        "decayed": any(str(event.get("to_status")) == "degraded" for event in events),
        "last_event": _factor_last_event_summary(events),
    }


def _factor_events_by_factor() -> dict[int, list[dict[str, Any]]]:
    """全库事件按因子分组（list_events 按写入序返回；组内保持时序）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for event in list_events():
        grouped.setdefault(int(event["factor_id"]), []).append(event)
    return grouped


def _factor_last_event_summary(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最近一条事件的紧凑摘要（事件/状态迁移/闸门结论/时间）。

    列表载荷不带完整 ``detail``：衰减降级事件 detail 含滚动窗口数组，逐行展开会
    让列表响应无谓膨胀——完整事件历史由详情端点给出。
    """
    if not events:
        return None
    event = _factor_event_payload(events[-1])
    return {
        "event": event["event"],
        "from_status": event["from_status"],
        "to_status": event["to_status"],
        "reason": event["reason"],
        "created_at": event["created_at"],
    }


def _factors_payload() -> list[dict[str, Any]]:
    """列表载荷（同步体，供 to_thread）：按 ICIR 降序固定排序（并列按 id 升序）。

    因子量级为十位级：逐因子各一次 IC 序列读（``ic_points`` 计数）与既有
    ``recent_ic_stats`` 同量级，无需批量化。
    """
    rows = list_factors()
    events_by_factor = _factor_events_by_factor()
    ic_stats = recent_ic_stats(rows, window=FACTOR_IC_WINDOW)
    payload = [
        _factor_summary_payload(
            row,
            events=events_by_factor.get(int(row["id"]), []),
            ic_stats=ic_stats,
            ic_points=len(list_ic_series(int(row["id"]), limit=FACTOR_IC_WINDOW)),
        )
        for row in rows
    ]
    payload.sort(key=lambda item: (-item["icir_60d"], item["id"]))
    return payload


def _factor_detail_payload(factor_id: int) -> dict[str, Any] | None:
    """详情载荷：摘要字段 + IC 序列（近 60 点升序）+ 事件历史；无该因子 → None。"""
    row = next((item for item in list_factors() if int(item["id"]) == factor_id), None)
    if row is None:
        return None
    events = list_events(factor_id)
    ic_series = list_ic_series(factor_id, limit=FACTOR_IC_WINDOW)
    detail = _factor_summary_payload(
        row,
        events=events,
        ic_stats=recent_ic_stats([row], window=FACTOR_IC_WINDOW),
        ic_points=len(ic_series),
    )
    detail["ic_series"] = [
        {"trade_date": trade_date, "rank_ic": rank_ic} for trade_date, rank_ic in ic_series
    ]
    detail["events"] = [_factor_event_payload(event) for event in events]
    detail["style_exposure"] = _factor_style_exposure_payload(row)
    return detail


def _factor_style_exposure_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    """风格暴露披露（只读诊断，add-factor-style-exposure）。

    在准入面板上现算因子与 builtin 风格代理篮子的 IC 相关。面板不可得/表达式不可求值
    → **返回 None**（前端如实展示「数据不足」，MUST NOT 用 0 或虚构值冒充）。
    """
    try:
        from finance_agent.factors.dsl import parse as dsl_parse
        from finance_agent.factors.eval_engine import (
            evaluate as dsl_evaluate,
        )
        from finance_agent.factors.eval_engine import (
            forward_returns,
            rank_ic_series,
        )
        from finance_agent.factors.monthly import _panel_from_snapshot

        panel = _panel_from_snapshot(None)
        fwd = forward_returns(panel.fields["close"])
        factor_ic = rank_ic_series(dsl_evaluate(dsl_parse(str(row["expression"])), panel), fwd)
        return style_exposure_payload(compute_style_exposure(factor_ic, panel, fwd=fwd))
    except (OSError, ValueError) as exc:  # 面板不可得/深度不足/表达式不可求值 → 诚实置空
        _logger.warning("因子风格暴露不可计算 factor_id=%s: %s", row.get("id"), exc)
        return None


@app.get("/api/factors")
async def factor_library_list() -> dict[str, Any]:
    """因子库列表（只读）：名称/表达式/状态/来源/ICIR/最近闸门结论/衰减标记。

    空库 → 200 + 空数组（不 500、不用伪数据冒充）；库路径调用期读
    ``FACTOR_LIBRARY_DB_PATH``（library 模块 env 口径）。
    """
    return {"factors": await asyncio.to_thread(_factors_payload)}


@app.get("/api/factors/{factor_id}")
async def factor_library_detail(factor_id: int) -> dict[str, Any]:
    """因子详情（只读）：完整 DSL 表达式 + IC 序列 + 事件历史；未知 id → 404。"""
    payload = await asyncio.to_thread(_factor_detail_payload, factor_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"未知因子: {factor_id}")
    return payload


# ── 因子研发监控（delta add-factor-research-monitor：快照透传 + 陈旧派生；
#    delta add-factor-research-control：控制请求端点 + control 块）──
#
# 只读面：读取 distro 采集器（collect_factor_status.py）经宿主 data/ 桥写入的 JSON
# 快照，零写入、零业务复制。路径调用期读 ``FACTOR_RESEARCH_STATUS_PATH``
# （E2E env 注入即生效，同 SNAPSHOT_DB_PATH 纪律）。缺失/损坏二分如实——
# 监控页需要第一类「未接入」状态，MUST NOT 以 500 或伪快照冒充。
#
# 控制面（add-factor-research-control）：``PUT /api/factor-research/control`` 原子写控制
# 文件（宿主 data/ 桥反向，distro 侧研发代理轮询调和）；零命令面——仅布尔期望态，
# 无任意命令/脚本字段。快照缺失/损坏/陈旧 = 控制通道无存活证据 → 409 拒绝（不留
# 无人受理的请求）。
FACTOR_RESEARCH_STALE_AFTER_S = 180  # 3× 采集周期（契约 ≤60s）；阈值单源
FACTOR_RESEARCH_STALL_AFTER_S = 900  # 会话日志静默阈值（实测历史最大静默 602s 的 1.5 倍）
FACTOR_RESEARCH_LAUNCHER_FRESH_AFTER_S = 300  # 2× 启动器节拍（120s）+ 余量
FACTOR_RESEARCH_LAUNCHER_HONOR_ETA_S = 180  # 离线受理的披露上界（与陈旧阈值同值）

# 研发节奏配置（delta add-factor-research-settings）：四个旋钮的默认值与合法区间。
# 与 distro 侧 factor_research_agent.DEFAULT_CONFIG / CONFIG_BOUNDS 字面同步。
FACTOR_RESEARCH_CONFIG_DEFAULTS: dict[str, int] = {
    "snapshot_interval_s": 15,
    "session_gap_s": 60,
    "short_session_backoff_s": 600,
    "short_session_threshold_s": 120,
}
FACTOR_RESEARCH_CONFIG_BOUNDS: dict[str, tuple[int, int]] = {
    "snapshot_interval_s": (5, 120),
    "session_gap_s": (0, 3600),
    "short_session_backoff_s": (0, 7200),
    "short_session_threshold_s": (30, 3600),
}


def _factor_research_progress_block(snapshot: dict[str, Any]) -> dict[str, Any]:
    """进度新鲜度派生块（阈值单源：FACTOR_RESEARCH_STALL_AFTER_S）。

    ``stalled`` 需四条件同时成立：快照可用且非陈旧 + 看护运行中 + 会话存活 + 静默超阈值。
    看护未运行（日志本就不更新）或会话非存活（正常会话间隔）MUST NOT 判僵死。
    """
    session = snapshot.get("current_session")
    raw = session.get("log_age_s") if isinstance(session, dict) else None
    log_age = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None
    supervisor = snapshot.get("supervisor")
    running = bool(supervisor.get("running")) if isinstance(supervisor, dict) else False
    alive = bool(session.get("alive")) if isinstance(session, dict) else False
    stalled = bool(
        snapshot.get("available")
        and not snapshot.get("stale")
        and running
        and alive
        and log_age is not None
        and log_age > FACTOR_RESEARCH_STALL_AFTER_S
    )
    return {"log_age_seconds": log_age, "stalled": stalled}


def _factor_research_snapshot_payload() -> dict[str, Any]:
    """快照面：透传 + 派生 ``available``/``age_seconds``/``stale``/``progress``。"""
    path = Path(os.environ.get("FACTOR_RESEARCH_STATUS_PATH", "data/factor_research/status.json"))
    if not path.is_file():
        return {
            "available": False,
            "reason": "missing",
            "progress": {"log_age_seconds": None, "stalled": False},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("snapshot is not an object")
        epoch = data.get("collected_at_epoch")
        if epoch is None:
            # epoch 缺失 → 回退解析 ISO（采集器契约两者均写；fixture/旧快照容错）
            iso = data.get("collected_at")
            if not isinstance(iso, str):
                raise ValueError("no timestamp")
            epoch = datetime.fromisoformat(iso).timestamp()
        epoch = float(epoch)
    except (OSError, ValueError, TypeError):
        return {
            "available": False,
            "reason": "corrupt",
            "progress": {"log_age_seconds": None, "stalled": False},
        }
    age = max(0, int(time.time() - epoch))
    payload = dict(data)
    payload.update(
        {
            "available": True,
            "age_seconds": age,
            "stale": age > FACTOR_RESEARCH_STALE_AFTER_S,
        }
    )
    payload["progress"] = _factor_research_progress_block(payload)
    return payload


def _factor_research_launcher_payload() -> dict[str, Any]:
    """启动器心跳：透传 + 派生 ``available`` / ``age_seconds`` / ``fresh``。"""
    path = Path(
        os.environ.get("FACTOR_RESEARCH_LAUNCHER_PATH", "data/factor_research/launcher.json")
    )
    if not path.is_file():
        return {"available": False, "reason": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("launcher heartbeat is not an object")
        epoch = data.get("checked_at_epoch")
        if epoch is None:
            raise ValueError("no checked_at_epoch")
        epoch = float(epoch)
    except (OSError, ValueError, TypeError):
        return {"available": False, "reason": "corrupt"}
    age = max(0, int(time.time() - epoch))
    payload = dict(data)
    payload.update(
        {
            "available": True,
            "age_seconds": age,
            "fresh": age <= FACTOR_RESEARCH_LAUNCHER_FRESH_AFTER_S,
        }
    )
    return payload


FACTOR_RESEARCH_DISTRO_PROBE_FRESH_AFTER_S = 300  # 2× 启动器节拍（120s）+ 余量


def _factor_research_distro_payload() -> dict[str, Any]:
    """distro 探活块：透传 + 派生 ``available`` / ``age_seconds`` / ``fresh``。

    只读披露：本函数 MUST NOT 触发任何处置（不重启、不终止、不写控制文件）。
    """
    path = Path(
        os.environ.get(
            "FACTOR_RESEARCH_DISTRO_PROBE_PATH", "data/factor_research/distro_probe.json"
        )
    )
    if not path.is_file():
        return {"available": False, "reason": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("distro probe is not an object")
        epoch = float(data.get("probed_at_epoch"))
    except (OSError, ValueError, TypeError):
        return {"available": False, "reason": "corrupt"}
    age = max(0, int(time.time() - epoch))
    payload = dict(data)
    payload.update(
        {
            "available": True,
            "age_seconds": age,
            "fresh": age <= FACTOR_RESEARCH_DISTRO_PROBE_FRESH_AFTER_S,
        }
    )
    return payload


def _factor_research_honor(
    snapshot: dict[str, Any], launcher: dict[str, Any]
) -> tuple[str, int | None]:
    """控制受理能力：快照新鲜 → live；启动器新鲜且代理就绪 → launcher；否则 unavailable。"""
    if snapshot.get("available") and not snapshot.get("stale"):
        return "live", 0
    if launcher.get("available") and launcher.get("fresh") and launcher.get("agent_ok") is True:
        return "launcher", FACTOR_RESEARCH_LAUNCHER_HONOR_ETA_S
    return "unavailable", None


def _read_control_file(path: Path) -> dict[str, Any]:
    """控制文件读取：缺失/不可解析/字段非法 → 对应字段 ``None``（如实不猜）。"""
    empty = {
        "desired_running": None,
        "requested_at": None,
        "restart_requested_at": None,
        "restart_requested_at_epoch": None,
        "restart_request_id": None,
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty
    if not isinstance(data, dict):
        return empty
    desired = data.get("desired_running")
    requested_at = data.get("requested_at")
    restart_at = data.get("restart_requested_at")
    restart_epoch = data.get("restart_requested_at_epoch")
    restart_id = data.get("restart_request_id")
    return {
        "desired_running": desired if isinstance(desired, bool) else None,
        "requested_at": requested_at if isinstance(requested_at, str) else None,
        "restart_requested_at": restart_at if isinstance(restart_at, str) else None,
        "restart_requested_at_epoch": restart_epoch
        if isinstance(restart_epoch, int) and not isinstance(restart_epoch, bool)
        else None,
        "restart_request_id": restart_id if isinstance(restart_id, str) else None,
    }


def _restart_pending(control: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """重启请求是否仍未获处理：有请求 + 快照新鲜 + 无同一 request_id 的终态结果。

    旧版代理不写 ``agent.restart`` → 恒为真（MUST NOT 凭快照推进判成功）。
    快照陈旧/不可用 → 恒假（实态不可证，MUST NOT 判「重启中」）。
    """
    request_id = control.get("restart_request_id")
    if not isinstance(request_id, str):
        return False
    if not (snapshot.get("available") and not snapshot.get("stale")):
        return False
    agent = snapshot.get("agent")
    handled = agent.get("restart") if isinstance(agent, dict) else None
    return not (
        isinstance(handled, dict)
        and handled.get("request_id") == request_id
        and handled.get("result") in ("restarted", "restart-failed")
    )


def _factor_research_actual_state(snapshot: dict[str, Any]) -> str:
    """实态三态：running / stopped / unverified（持久化意图不参与判定）。"""
    if snapshot.get("available") and not snapshot.get("stale"):
        return "running" if (snapshot.get("supervisor") or {}).get("running") else "stopped"
    return "unverified"


def _normalize_pipeline_config(data: Any) -> tuple[dict[str, int], list[str]]:
    """节奏配置逐字段归一化：非法/越界 → 默认值并列入 invalid_fields。"""
    values = dict(FACTOR_RESEARCH_CONFIG_DEFAULTS)
    invalid: list[str] = []
    if not isinstance(data, dict):
        return values, invalid
    for key, (low, high) in FACTOR_RESEARCH_CONFIG_BOUNDS.items():
        if key not in data:
            continue
        raw = data.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and low <= raw <= high:
            values[key] = raw
        else:
            invalid.append(key)
    return values, invalid


def _factor_research_config_path() -> Path:
    return Path(os.environ.get("FACTOR_RESEARCH_CONFIG_PATH", "data/factor_research/pipeline.json"))


def _factor_research_config_block() -> dict[str, Any]:
    """配置块：生效值 + 来源（file/defaults/corrupt）+ 非法字段 + 更新时间。"""
    path = _factor_research_config_path()
    defaults = dict(FACTOR_RESEARCH_CONFIG_DEFAULTS)
    if not path.is_file():
        return {"values": defaults, "source": "defaults", "invalid_fields": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"values": defaults, "source": "corrupt", "invalid_fields": [], "updated_at": None}
    if not isinstance(data, dict):
        return {"values": defaults, "source": "corrupt", "invalid_fields": [], "updated_at": None}
    values, invalid = _normalize_pipeline_config(data)
    updated_at = data.get("updated_at")
    return {
        "values": values,
        "source": "file",
        "invalid_fields": invalid,
        "updated_at": updated_at if isinstance(updated_at, str) else None,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """原子写（唯一 tmp 名 + replace）：宿主容器与 distro 侧脚本可能并发写同一文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _factor_research_control_block(
    snapshot: dict[str, Any], launcher: dict[str, Any] | None = None
) -> dict[str, Any]:
    """control 块：期望态 + pending + 重启请求 + honor（受理能力与 ETA 的单一判定源）。

    陈旧/不可用快照 → 实态不可证 → ``pending`` / ``restart_pending`` 恒假
    （MUST NOT 据陈旧数据判「切换中/重启中」）。
    """
    control = _read_control_file(
        Path(os.environ.get("FACTOR_RESEARCH_CONTROL_PATH", "data/factor_research/control.json"))
    )
    pending = False
    if (
        control["desired_running"] is not None
        and snapshot.get("available")
        and not snapshot.get("stale")
    ):
        running = bool((snapshot.get("supervisor") or {}).get("running"))
        pending = control["desired_running"] != running
    honor, eta = _factor_research_honor(snapshot, launcher or {})
    return {
        **control,
        "pending": pending,
        "honor": honor,
        "honor_eta_seconds": eta,
        "restart_pending": _restart_pending(control, snapshot),
    }


def _factor_research_status_payload() -> dict[str, Any]:
    snapshot = _factor_research_snapshot_payload()
    launcher = _factor_research_launcher_payload()
    return {
        **snapshot,
        "actual_state": _factor_research_actual_state(snapshot),
        "config": _factor_research_config_block(),
        "launcher": launcher,
        "control": _factor_research_control_block(snapshot, launcher),
        "distro": _factor_research_distro_payload(),
    }


class FactorResearchControlRequest(BaseModel):
    """控制请求：仅接受布尔期望态（StrictBool——无字符串/数字强转；多余字段 422）。"""

    model_config = ConfigDict(extra="forbid")

    desired_running: StrictBool


class FactorResearchRestartRequest(BaseModel):
    """重启请求：空对象（零命令面；多余字段 422）。"""

    model_config = ConfigDict(extra="forbid")


class FactorResearchConfigRequest(BaseModel):
    """研发节奏配置：四个可选严格整数（范围校验；未知字段 422；空对象 422）。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_interval_s: StrictInt | None = Field(default=None, ge=5, le=120)
    session_gap_s: StrictInt | None = Field(default=None, ge=0, le=3600)
    short_session_backoff_s: StrictInt | None = Field(default=None, ge=0, le=7200)
    short_session_threshold_s: StrictInt | None = Field(default=None, ge=30, le=3600)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> FactorResearchConfigRequest:
        if all(getattr(self, key) is None for key in FACTOR_RESEARCH_CONFIG_BOUNDS):
            raise ValueError("at least one field is required")
        return self


def _factor_research_control_update(desired_running: bool) -> dict[str, Any]:
    """写控制文件（原子 tmp + replace）；无任何受理能力 → 409，MUST NOT 落文件。

    显式启停写入不含 restart 字段的新文件——清除未执行的重启请求。
    """
    snapshot = _factor_research_snapshot_payload()
    launcher = _factor_research_launcher_payload()
    honor, _eta = _factor_research_honor(snapshot, launcher)
    if honor == "unavailable":
        raise HTTPException(status_code=409, detail="control_unavailable")
    now = datetime.now().astimezone()
    payload = {
        "desired_running": desired_running,
        "requested_at": now.isoformat(),
        "requested_at_epoch": int(now.timestamp()),
        "request_id": uuid.uuid4().hex[:12],
    }
    _atomic_write_json(
        Path(os.environ.get("FACTOR_RESEARCH_CONTROL_PATH", "data/factor_research/control.json")),
        payload,
    )
    return {
        "control": _factor_research_control_block(
            _factor_research_snapshot_payload(), _factor_research_launcher_payload()
        )
    }


def _factor_research_restart_update() -> dict[str, Any]:
    """写重启请求（desired_running=true + restart 标记）；通道无证据 → 409。"""
    snapshot = _factor_research_snapshot_payload()
    launcher = _factor_research_launcher_payload()
    honor, _eta = _factor_research_honor(snapshot, launcher)
    if honor == "unavailable":
        raise HTTPException(status_code=409, detail="control_unavailable")
    now = datetime.now().astimezone()
    payload = {
        "desired_running": True,
        "requested_at": now.isoformat(),
        "requested_at_epoch": int(now.timestamp()),
        "request_id": uuid.uuid4().hex[:12],
        "restart_requested_at": now.isoformat(),
        "restart_requested_at_epoch": int(now.timestamp()),
        "restart_request_id": uuid.uuid4().hex[:12],
    }
    _atomic_write_json(
        Path(os.environ.get("FACTOR_RESEARCH_CONTROL_PATH", "data/factor_research/control.json")),
        payload,
    )
    return {
        "control": _factor_research_control_block(
            _factor_research_snapshot_payload(), _factor_research_launcher_payload()
        )
    }


def _factor_research_config_update(req: FactorResearchConfigRequest) -> dict[str, Any]:
    """合并写节奏配置（四级校验后原子落盘）；通道无证据 → 409。"""
    snapshot = _factor_research_snapshot_payload()
    launcher = _factor_research_launcher_payload()
    honor, _eta = _factor_research_honor(snapshot, launcher)
    if honor == "unavailable":
        raise HTTPException(status_code=409, detail="control_unavailable")
    current = _factor_research_config_block()["values"]
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    now = datetime.now().astimezone()
    payload = {
        **current,
        **updates,
        "updated_at": now.isoformat(),
        "updated_at_epoch": int(now.timestamp()),
        "request_id": uuid.uuid4().hex[:12],
    }
    _atomic_write_json(_factor_research_config_path(), payload)
    return {"config": _factor_research_config_block()}


# ── 收割链路只读端点（add-factor-harvest-ui）────────────────────────────────
#
# 数据面：harvest_state.json（逐候选裁决）+ candidates.json（队列）+ 因子库账本
# （批次/入库/拒收）。零写入、零业务复制；文件缺失/损坏 → available=false，
# 各块降级为 null，MUST NOT 编造数字。
FACTOR_HARVEST_STATE_PATH_DEFAULT = "data/factor_research/harvest_state.json"
FACTOR_HARVEST_CANDIDATES_PATH_DEFAULT = "data/factor_research/candidates.json"
FACTOR_HARVEST_RECENT_REJECTIONS = 10
FACTOR_HARVEST_RECENT_ADMISSIONS = 5


def _harvest_ts(value: Any) -> str:
    """ISO 时间比较键（截到秒；容忍 ``T`` 分隔与是否带偏移）。"""
    return str(value or "")[:19]


def _harvest_job_block() -> dict[str, Any]:
    """ops 任务行（factor_harvest_daily）→ job 块；不可得如实 null。"""
    block: dict[str, Any] = {
        "status": None,
        "started_at": None,
        "finished_at": None,
        "schedule": None,
        "next_fire_time": None,
    }
    try:
        from finance_agent.ops_api import _next_fire_time
        from finance_agent.outcome.ops.jobs import SCHEDULES
        from finance_agent.outcome.ops.model import last_job_run

        run = last_job_run("factor_harvest_daily") or {}
        block.update(
            {
                "status": run.get("status"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "schedule": dict(SCHEDULES["factor_harvest_daily"]),
                "next_fire_time": _next_fire_time("factor_harvest_daily"),
            }
        )
    except Exception:  # noqa: BLE001 - 任务块不可得不得拖垮整个区块
        _logger.warning("harvest job block unavailable", exc_info=True)
    return block


def _harvest_heartbeat_block() -> dict[str, Any] | None:
    """批内逐候选心跳（harvest 每处理一条写一次）；缺失/损坏 → None。"""
    path = Path(
        os.environ.get(
            "FACTOR_RESEARCH_HEARTBEAT_PATH", "data/factor_research/harvest_heartbeat.json"
        )
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    processed = data.get("processed_in_batch")
    total = data.get("total_in_batch")
    if not isinstance(processed, int) or not isinstance(total, int):
        return None
    updated = str(data.get("updated_at") or "")
    age_s: int | None = None
    if updated:
        try:
            updated_dt = datetime.fromisoformat(updated)
            now = datetime.now(updated_dt.tzinfo) if updated_dt.tzinfo else datetime.now()
            age_s = max(0, int((now - updated_dt).total_seconds()))
        except (TypeError, ValueError):
            age_s = None
    return {
        "processed_in_batch": processed,
        "total_in_batch": total,
        "phase": str(data.get("phase") or ""),
        "updated_at": updated or None,
        "age_s": age_s,
    }


def _factor_research_harvest_payload() -> dict[str, Any]:
    """收割链路只读载荷：job / progress / funnel / 最近入库与拒收。"""
    job = _harvest_job_block()
    empty: dict[str, Any] = {
        "available": False,
        "job": job,
        "progress": None,
        "funnel": None,
        "recent_admitted": [],
        "recent_rejected": [],
    }
    state_path = Path(
        os.environ.get("FACTOR_RESEARCH_HARVEST_STATE_PATH", FACTOR_HARVEST_STATE_PATH_DEFAULT)
    )
    candidates_path = Path(
        os.environ.get("FACTOR_RESEARCH_CANDIDATES_PATH", FACTOR_HARVEST_CANDIDATES_PATH_DEFAULT)
    )
    try:
        state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        processed = state_payload.get("processed") or {}
        candidates = candidates_payload.get("candidates") or []
    except (OSError, ValueError, AttributeError):
        return empty
    if not isinstance(processed, dict) or not isinstance(candidates, list):
        return empty

    status_counts = {"admitted": 0, "rejected": 0, "untranslatable": 0, "transcription_error": 0}
    rejected_by_gate = {str(gate): 0 for gate in range(6)}
    rejections: list[dict[str, Any]] = []
    for verdict in processed.values():
        if not isinstance(verdict, dict):
            continue
        status = str(verdict.get("status") or "")
        if status in status_counts:
            status_counts[status] += 1
        if status != "rejected":
            continue
        gate = verdict.get("gate")
        if isinstance(gate, int) and 0 <= gate <= 5:
            rejected_by_gate[str(gate)] += 1
        rejections.append(
            {
                "expression": verdict.get("expression"),
                "gate": gate if isinstance(gate, int) else None,
                "reason": verdict.get("reason"),
                "at": verdict.get("at"),
            }
        )
    rejections.sort(key=lambda item: _harvest_ts(item.get("at")), reverse=True)

    pending = sum(
        1 for item in candidates if isinstance(item, dict) and item.get("name") not in processed
    )
    reached_gates = status_counts["admitted"] + status_counts["rejected"]

    started_at = job.get("started_at")
    start_key = _harvest_ts(started_at)
    ledger = list_ledger_entries() if start_key else []
    in_run = [row for row in ledger if _harvest_ts(row.get("created_at")) >= start_key]
    batches_done = len({str(row.get("batch_id")) for row in in_run})
    admitted_in_run = sum(1 for row in in_run if row.get("admitted"))
    rejected_in_run = len(in_run) - admitted_in_run

    processed_in_run = 0
    if start_key:
        processed_in_run = sum(
            1
            for verdict in processed.values()
            if isinstance(verdict, dict) and _harvest_ts(verdict.get("at")) >= start_key
        )
    eta_s: int | None = None
    if start_key and processed_in_run > 0 and pending > 0:
        try:
            started_dt = datetime.fromisoformat(str(started_at))
            now = datetime.now(started_dt.tzinfo) if started_dt.tzinfo else datetime.now()
            elapsed = max(1.0, (now - started_dt).total_seconds())
            rate = processed_in_run / elapsed
            eta_s = int(pending / rate) if rate > 0 else None
        except (TypeError, ValueError):
            eta_s = None

    rows = sorted(
        list_factors(),
        key=lambda row: _harvest_ts(row.get("created_at")),
        reverse=True,
    )[:FACTOR_HARVEST_RECENT_ADMISSIONS]
    ic_stats = recent_ic_stats(rows, window=FACTOR_IC_WINDOW) if rows else {}
    recent_admitted = [
        {
            "expression": row.get("expression"),
            "factor_id": int(row["id"]),
            "created_at": row.get("created_at"),
            "icir_60d": ic_stats.get(int(row["id"]), (0.0, 0.0))[1],
        }
        for row in rows
    ]

    return {
        "available": True,
        "job": job,
        "progress": {
            "batches_done": batches_done,
            "batch_limit": DEFAULT_MAX_BATCHES_PER_RUN,
            "processed": len(processed),
            "pending": pending,
            "admitted_in_run": admitted_in_run,
            "rejected_in_run": rejected_in_run,
            "started_at": started_at,
            "eta_s": eta_s,
            "in_batch": _harvest_heartbeat_block(),
        },
        "funnel": {
            "candidates": len(candidates),
            "processed": len(processed),
            "untranslatable": status_counts["untranslatable"],
            "transcription_error": status_counts["transcription_error"],
            "reached_gates": reached_gates,
            "admitted": status_counts["admitted"],
            "rejected_by_gate": rejected_by_gate,
        },
        "recent_admitted": recent_admitted,
        "recent_rejected": rejections[:FACTOR_HARVEST_RECENT_REJECTIONS],
    }


@app.get("/api/factor-research/status")
async def factor_research_status() -> dict[str, Any]:
    """因子研发监控快照（只读）：快照 + actual_state + config + launcher + control。"""
    return await asyncio.to_thread(_factor_research_status_payload)


@app.get("/api/factor-research/harvest")
async def factor_research_harvest() -> dict[str, Any]:
    """收割链路只读载荷：job / progress / funnel / 最近入库与拒收（零写入）。"""
    return await asyncio.to_thread(_factor_research_harvest_payload)


@app.put("/api/factor-research/control")
async def factor_research_control(req: FactorResearchControlRequest) -> dict[str, Any]:
    """看护启停控制：期望态写入控制文件（distro 代理调和）；快照缺失/损坏/陈旧 → 409。"""
    return await asyncio.to_thread(_factor_research_control_update, req.desired_running)


@app.post("/api/factor-research/restart")
async def factor_research_restart(
    req: FactorResearchRestartRequest | None = None,
) -> dict[str, Any]:
    """管线重启：写重启请求（代理按 request_id 去重执行）；通道无证据 → 409。"""
    return await asyncio.to_thread(_factor_research_restart_update)


@app.put("/api/factor-research/config")
async def factor_research_config(req: FactorResearchConfigRequest) -> dict[str, Any]:
    """研发节奏配置：四个旋钮写 pipeline.json（代理周期读取）；通道无证据 → 409。"""
    return await asyncio.to_thread(_factor_research_config_update, req)


# ── 执行台账只读查询（delta add-broker-console：暴露 + 呈现，零业务复制） ──
#
# 写路径铁律：本层只包装执行侧既有只读面（``store.list_orders`` /
# ``PaperBroker.query_cash`` / ``query_positions``），不暴露任何写操作——下单/撤单
# 仍只经日批驱动器（可审计、可重放），手工入口会绕过信号/门禁契约。派生字段口径：
#   - 持仓市值：最新快照日 ``close_raw``（与 ``paper.run_paper_settle_daily`` 同源
#     口径：``_snapshot_code`` 去后缀 + ``_valid_close`` 有效性判定）；
#   - 快照缺失 / 标的缺价：持仓市值、总资产、浮盈记 ``null`` 并附可判读标注
#     （``market_value_note`` + ``missing_symbols``）——MUST NOT 以 0 冒充。
#     0 只在「无持仓」时为真实读数（空集求和不需要价格）；
#   - 门禁日志 = orders 中 status=rejected（add-execution-chain-driver 落库形态，
#     零 schema 变更）；
#   - 空账户如实：资金 = 初始 100 万（PaperBroker 惰性开户口径）、列表为空。

_BROKER_NOTE_NO_SNAPSHOT = "快照缺失（无行情数据），持仓市值不可得，未以 0 冒充"


def _broker_snapshot_marks(symbols: list[str]) -> tuple[str | None, dict[str, float], list[str]]:
    """持仓标的最新快照 ``close_raw`` 映射 → ``(快照交易日, {symbol: 价}, 缺价标的)``。

    快照库为空 / 该日无行 / 标的缺价 → 逐项如实返回（不抛错、不编造价格）：API 是
    只读面，缺价由调用方以 ``null`` + 标注表达（``settle_daily`` 的 fail loud 是
    日批落库语义，两者口径不同）。
    """
    from finance_agent.data.snapshot import SnapshotError, read_bars
    from finance_agent.execution.brokers.paper import _snapshot_code, _valid_close

    as_of = latest_snapshot_date()
    if as_of is None:
        return None, {}, sorted(symbols)
    codes = sorted({_snapshot_code(symbol) for symbol in symbols})
    try:
        frame = read_bars(codes, as_of, as_of, as_of=as_of)
    except SnapshotError:
        # 该日无快照行（含空库）：全部标的按缺价处理（E2E 快照库常态）
        return as_of, {}, sorted(symbols)
    by_code = {
        str(row["code"]): price
        for row in frame[["code", "close_raw"]].to_dict("records")
        if (price := _valid_close(row["close_raw"])) is not None
    }
    closes = {
        symbol: by_code[code] for symbol in symbols if (code := _snapshot_code(symbol)) in by_code
    }
    missing = sorted(symbol for symbol in symbols if symbol not in closes)
    return as_of, closes, missing


def _broker_overview_payload() -> dict[str, Any]:
    """总览载荷（同步体，供 to_thread）：资金 + 持仓市值/总资产/浮盈（快照 mark 口径）。

    无持仓 → 市值/浮盈为 0.0（空集求和的真实读数）；有持仓但快照不可得 → 三者记
    ``null`` + 标注（``position_cost`` 仍来自账本，不依赖快照）。
    """
    from finance_agent.execution.brokers.paper import PaperBroker

    broker = PaperBroker()
    cash = broker.query_cash()
    positions = broker.query_positions()
    if not positions:
        return {
            "cash": cash,
            "positions_value": 0.0,
            "equity": cash,
            "unrealized_pnl": 0.0,
            "position_cost": 0.0,
            "positions_count": 0,
            "snapshot_date": None,
            "missing_symbols": [],
            "market_value_note": None,
        }
    cost_total = round(sum(int(row["qty"]) * float(row["avg_cost"]) for row in positions), 2)
    as_of, closes, missing = _broker_snapshot_marks([str(row["symbol"]) for row in positions])
    if missing:
        note = (
            _BROKER_NOTE_NO_SNAPSHOT
            if as_of is None
            else f"快照 {as_of} 缺收盘价：{', '.join(missing)}；持仓市值不可得，未以 0 冒充"
        )
        return {
            "cash": cash,
            "positions_value": None,
            "equity": None,
            "unrealized_pnl": None,
            "position_cost": cost_total,
            "positions_count": len(positions),
            "snapshot_date": as_of,
            "missing_symbols": missing,
            "market_value_note": note,
        }
    positions_value = round(
        sum(int(row["qty"]) * closes[str(row["symbol"])] for row in positions), 2
    )
    return {
        "cash": cash,
        "positions_value": positions_value,
        "equity": round(cash + positions_value, 2),
        "unrealized_pnl": round(positions_value - cost_total, 2),
        "position_cost": cost_total,
        "positions_count": len(positions),
        "snapshot_date": as_of,
        "missing_symbols": [],
        "market_value_note": None,
    }


def _broker_positions_payload() -> dict[str, Any]:
    """持仓载荷（同步体，供 to_thread）：标的/数量/成本/现价（close_raw）/市值/浮盈。

    现价不可得 → 该行 ``close``/``market_value``/``unrealized_pnl`` 为 ``null``（界面
    显示占位符并标注快照口径），MUST NOT 填 0。
    """
    from finance_agent.execution.brokers.paper import PaperBroker

    positions = PaperBroker().query_positions()
    if positions:
        as_of, closes, _missing = _broker_snapshot_marks([str(row["symbol"]) for row in positions])
    else:
        as_of, closes = None, {}
    rows: list[dict[str, Any]] = []
    for row in positions:
        symbol = str(row["symbol"])
        qty = int(row["qty"])
        avg_cost = float(row["avg_cost"])
        close = closes.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "qty": qty,
                "avg_cost": avg_cost,
                "updated_at": row.get("updated_at"),
                "close": close,
                "market_value": round(qty * close, 2) if close is not None else None,
                "unrealized_pnl": (
                    round(qty * (close - avg_cost), 2) if close is not None else None
                ),
            }
        )
    return {"snapshot_date": as_of, "positions": rows}


def _broker_orders_payload(status: str | None) -> list[dict[str, Any]]:
    """订单行（store 只读；含状态/原因码/decision_id/signal_id 证据链，零业务复制）。"""
    from finance_agent.execution.store import list_orders

    return list_orders(status=status)


@app.get("/api/broker/overview")
async def broker_overview() -> dict[str, Any]:
    """执行台账总览（只读）：可用资金 / 持仓市值 / 总资产 / 浮动盈亏。

    持仓市值按最新快照 ``close_raw`` mark；快照缺失 → 市值/总资产/浮盈 ``null`` +
    标注（MUST NOT 以 0 冒充）。空账户如实：资金 = 初始 100 万、市值/浮盈为 0。
    库路径调用期读 ``EXECUTION_DB_PATH`` / ``SNAPSHOT_DB_PATH``（E2E env 注入即生效）。
    """
    return await asyncio.to_thread(_broker_overview_payload)


@app.get("/api/broker/positions")
async def broker_positions() -> dict[str, Any]:
    """持仓列表（只读）：标的/数量/成本/现价/市值/浮盈；现价不可得逐行为 ``null``。"""
    return await asyncio.to_thread(_broker_positions_payload)


@app.get("/api/broker/orders")
async def broker_orders(status: str | None = None) -> dict[str, Any]:
    """订单列表（只读）：四态（pending/filled/rejected/expired）+ 原因码 + 证据链。

    ``?status=`` 过滤；非法状态 → 400（fail loud，不静默返回空列表误导归因）。
    """
    if status is not None and status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法订单状态: {status}")
    return {"orders": await asyncio.to_thread(_broker_orders_payload, status)}


@app.get("/api/broker/gate-log")
async def broker_gate_log() -> dict[str, Any]:
    """门禁拒绝视图（只读）：= orders 中 status=rejected 的记录（含原因码）。

    风控门禁拒绝经 ``store.record_order_rejection`` 落库后即在此回查（含 ST_FLAG
    等结构化原因码与关联 ``decision_id``/``signal_id``）——零 schema 变更。
    """
    return {"orders": await asyncio.to_thread(_broker_orders_payload, STATUS_REJECTED)}


# ── AG-UI 协议通道（add-assistant-ui-thread PoC，双轨隔离：仅挂载，不接入 registry）──
from finance_agent.agui.endpoint import router as agui_router  # noqa: E402

app.include_router(agui_router)

# ── 评估运维控制台端点族（delta add-eval-ops-console：/api/v1/ops/*）──
from finance_agent.ops_api import router as ops_router  # noqa: E402

app.include_router(ops_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
