"""选股日批编排（add-screening-pipeline T1；S1 逐字）。

流程：① 快照新鲜度（``latest_snapshot_date() == as_of``，``as_of`` 缺省 = 最新
快照日）→ ② L1 确定性硬过滤 → ③ L2 打分（Top-N **全量**落库，供稳定性查询；当日榜
**原子替换**——同日重跑换小集合时旧行不得残留）→ ④ L3 LLM 粗筛（quick profile，
事件注入）→ ⑤ 精选池（``keep`` ∩ 连续在榜门槛）→ ⑥ 批次行 ok + 计数。

**fail-closed 纪律**：任一步失败 → ``screening_batches.status = "failed"`` + error
文案，当日精选池**零行**——批次行与池行经 ``store.record_screening_batch`` 单事务
写入（ok 与池同生共死；失败路径经同一函数清空当日池行）。

其它纪律：

- 面板/事件/triage client 全部可注入（测试零网络）：``panel`` 缺省从快照库重建
  最近 ``DEFAULT_PANEL_LOOKBACK_DAYS`` 个交易日；``events_by_symbol`` 缺省经
  events 模块逐候选装配（WebSearch → 预设库 → 兜底）；``triage_client`` 缺省
  ``GatewayTriageClient``（purpose 钉死 quick）。
- L3 单只失败/违约 → keep 降级、不阻断批次（change-9 C3 合同不变）；但**全部候选
  降级**（warnings 数 == 候选数，且候选非空）视为 LLM 网关整体不可用 → 抛错 →
  批次 failed（spec 场景「中途失败不产残缺池」）。
- 快照 meta 缺 ``trade_calendar`` → 抛错（L1 上市天数与事件时效判定都依赖交易日
  轴，缺历不猜）。
- ``seed``：签名保留参数——本管道无随机数（ranker/triage 全确定），当前无消费点。

返回批次结果 dict（同时已落库）：
``{"trade_date", "status", "error", "l1_passed", "l2_top", "l3_keep", "pool_size"}``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.data.snapshot import (
    SnapshotNotFreshError,
    get_all_meta,
    latest_snapshot_date,
    read_universe,
)
from finance_agent.factors.eval_engine import PANEL_FIELDS as _PANEL_FIELDS
from finance_agent.factors.eval_engine import MarketPanel, build_panel_from_rows
from finance_agent.factors.library import list_active_factors
from finance_agent.screening import constraints
from finance_agent.screening.candidate_llm import (
    GatewayTriageClient,
    TriageLLMClient,
    run_triage,
)
from finance_agent.screening.filters import build_candidate_record, filter_universe
from finance_agent.screening.ranker import (
    Candidate,
    has_scorable_factors,
    liquidity_fallback_rank,
    rank_universe,
    recent_ic_stats,
)
from finance_agent.screening.store import (
    DEFAULT_MIN_DAYS,
    consecutive_days_in_top,
    eligible_for_selection,
    latest_successful_batch,
    list_selection_pool,
    record_screening_batch,
    rewrite_day_l2_board,
)

_logger = logging.getLogger("finance_agent.screening.pipeline")

# L2 全量落库 Top-N（Global Constraints：Top-100）
L2_TOP_N = 100
# 精选池在榜门槛（默认连续 3 个交易日；口径单一来源 = store.DEFAULT_MIN_DAYS）
POOL_MIN_DAYS = DEFAULT_MIN_DAYS
# 缺省面板回看交易日数（覆盖 L2 内置因子最长 20 日窗口 + 流动性 20 日窗口；
# 未来引入更长回看因子时调大本常量即可）
DEFAULT_PANEL_LOOKBACK_DAYS = 60


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now().isoformat()


def _canonical_date(value: str, field_name: str) -> str:
    """严格规范 ``YYYY-MM-DD``（非规范 → ``ValueError``；不落漂移日期）。"""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 须为规范 YYYY-MM-DD: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} 须为规范 YYYY-MM-DD: {value!r}")
    return value


def _parse_calendar_entries(raw: str | None) -> list[str]:
    """``trade_calendar`` 原始值 → 规范 ``YYYY-MM-DD`` 升序去重列表。

    缺失/空/坏 JSON/非数组 → ``[]``；数组内不可解析项跳过（与 filters 日历消费
    同纪律）。严格版（日批）与容错版（时效性校验）共用本解析。
    """
    days: list[str] = []
    if raw:
        try:
            decoded = json.loads(raw)
        except ValueError:
            return []
        if isinstance(decoded, list):
            for entry in decoded:
                text = str(entry)
                try:
                    parsed = date.fromisoformat(text)
                except ValueError:
                    continue
                if parsed.isoformat() == text:
                    days.append(text)
    return sorted(set(days))


def _read_trade_calendar(snapshot_db_path: str | Path | None) -> list[str]:
    """快照 meta ``trade_calendar``（JSON 数组）→ 规范交易日升序列表（严格版）。

    缺失/不可解析/非数组/无有效日期项 → ``ValueError``（fail loud：L1 上市天数与
    事件时效判定都依赖交易日轴，缺历不猜）。
    """
    days = _parse_calendar_entries(get_all_meta(snapshot_db_path).get("trade_calendar"))
    if not days:
        raise ValueError(
            "快照 meta 缺 trade_calendar（交易日历）：拒绝在没有交易日轴的情况下跑日批"
        )
    return days


def read_snapshot_calendar(snapshot_db_path: str | Path | None = None) -> list[str] | None:
    """快照 meta ``trade_calendar`` 的**容错**读取（分析前时效性校验用）。

    与日批严格版（``_read_trade_calendar``）的区别：缺失/不可解析/无有效项 →
    ``None``——调用方的时效性校验在无日历时把「新事件」下界退化为当日（保守下限
    不猜，见 ``_recent_event_bound``），不因缺历拒绝整次分析。
    """
    return _parse_calendar_entries(get_all_meta(snapshot_db_path).get("trade_calendar")) or None


def _build_default_panel(
    as_of: str, calendar: Sequence[str], snapshot_db_path: str | Path | None
) -> MarketPanel:
    """缺省面板：快照库最近 ``DEFAULT_PANEL_LOOKBACK_DAYS`` 个交易日逐日读取拼装。"""
    dates = [day for day in calendar if day <= as_of][-DEFAULT_PANEL_LOOKBACK_DAYS:]
    if as_of not in dates:
        raise ValueError(
            f"交易日历不含 as_of={as_of}：无法重建面板（快照新鲜度检查已通过，不应发生）"
        )
    chunks = [
        build_panel_from_rows(pd.DataFrame(read_universe(day, db_path=snapshot_db_path)))
        for day in dates
    ]
    return MarketPanel(
        fields={
            name: pd.concat([chunk.fields[name] for chunk in chunks]).sort_index()
            for name in _PANEL_FIELDS
        }
    )


def _amounts_by_code(
    panel: MarketPanel, as_of: str, *, history_days: int
) -> dict[str, list[float]]:
    """面板 ``amount`` 帧 → 各 code 截至 ``as_of`` 的近 ``history_days`` 日成交额（升序）。

    窗口含 NaN 时原样保留（``filters.avg_amount_20d`` 判定非有限值 → None →
    ``DATA_MISSING``，fail-closed 不猜）；面板缺 ``amount`` 字段 → ``ValueError``。
    """
    frame = panel.fields.get("amount")
    if frame is None:
        raise ValueError("面板缺 'amount' 字段：无法派生 L1 流动性指标（fail-closed）")
    window = frame.loc[:as_of].tail(history_days)
    return {
        str(code): [float(value) for value in window[code].to_numpy()] for code in window.columns
    }


def _assemble_events_by_symbol(candidates: Sequence[Candidate]) -> dict[str, list[dict]]:
    """生产缺省事件装配：逐候选经 events 模块取数（WebSearch → 预设库 → 兜底）。

    显式注入 ``events_by_symbol`` 的调用方（测试/回放）不触发本路径（零网络）。
    本函数是 events 模块与日批之间的唯一接线点，便于整体替换/钉死（测试）。
    """
    from finance_agent.events.pipeline import fetch_key_events

    return {candidate.symbol: fetch_key_events(candidate.symbol) for candidate in candidates}


def _candidate_contribs(candidate: Candidate) -> list[dict[str, Any]]:
    """贡献分解 → 池行 JSON 结构（前端浮层按 |contribution| 排序渲染）。"""
    return [
        {
            "factor_id": contrib.factor_id,
            "zscore": contrib.zscore,
            "weight": contrib.weight,
            "contribution": contrib.contribution,
        }
        for contrib in candidate.factor_contribs
    ]


# ── 日批入口（S1） ──────────────────────────────────────────────────────────


def run_screening_batch(
    as_of: str | None = None,
    *,
    panel: MarketPanel | None = None,
    events_by_symbol: dict[str, list[dict]] | None = None,
    triage_client: TriageLLMClient | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    library_db_path: str | Path | None = None,
    seed: int = 20261013,
) -> dict[str, Any]:
    """日批入口（S1）：快照确认 → L1 → L2 → L3 → 精选池，fail-closed 落库。

    参数：``as_of`` 缺省 = 最新快照日；``panel``/``events_by_symbol``/
    ``triage_client`` 可注入（缺省从快照库/events 模块/gateway 构造）；
    ``db_path`` = screening 库（缺省调用期读 ``SCREENING_DB_PATH``）；
    ``snapshot_db_path``/``library_db_path`` 同上（缺省走各模块 env 口径）；
    ``seed`` 保留（本管道确定，无消费点）。

    批次内部失败**不抛出**：批次行记 failed + error 文案并返回结果 dict（调用方
    据 ``status`` 判定）；仅「快照库为空且 as_of 未指定」（无 trade_date 可落批
    次行）与非规范 ``as_of`` 直接抛错。
    """
    _ = seed  # 签名保留：管道全确定（无 RNG），仅记录设计口径
    started_at = _now_iso()
    latest = latest_snapshot_date(snapshot_db_path)
    if as_of is None:
        if latest is None:
            raise SnapshotNotFreshError(
                "快照库为空：无法运行选股日批（先跑 market_snapshot_daily）"
            )
        trade_date = _canonical_date(latest, "latest_snapshot_date")
    else:
        trade_date = _canonical_date(as_of, "as_of")
    if latest != trade_date:
        error = (
            f"{SnapshotNotFreshError.__name__}: "
            f"快照最新数据日期为 {latest}，不接受 as_of={trade_date}"
        )
        _logger.warning("选股日批失败（%s）：%s", trade_date, error)
        record_screening_batch(
            trade_date,
            status="failed",
            error=error,
            started_at=started_at,
            finished_at=_now_iso(),
            selection_mode="factor",  # 快照新鲜度未过：选择路径未判定，按默认标注
            db_path=db_path,
        )
        return {
            "trade_date": trade_date,
            "status": "failed",
            "error": error,
            "l1_passed": 0,
            "l2_top": 0,
            "l3_keep": 0,
            "pool_size": 0,
            "selection_mode": "factor",
        }

    l1_passed = 0
    l2_top = 0
    l3_keep = 0
    selection_mode = "factor"
    try:
        calendar = _read_trade_calendar(snapshot_db_path)
        active_panel = (
            panel
            if panel is not None
            else _build_default_panel(trade_date, calendar, snapshot_db_path)
        )
        # ② L1：快照行 × 派生量 → 确定性硬过滤
        rows = read_universe(trade_date, db_path=snapshot_db_path)
        amounts = _amounts_by_code(
            active_panel, trade_date, history_days=constraints.MIN_LIQUIDITY_HISTORY_DAYS
        )
        records = [
            build_candidate_record(
                row=row,
                amounts=amounts.get(str(row.get("code") or "").strip(), []),
                calendar=calendar,
                as_of=trade_date,
            )
            for row in rows
        ]
        passed = [decision.code for decision in filter_universe(records) if decision.passed]
        l1_passed = len(passed)
        # ③ L2：active 因子 × 近 60 日 IC → Top-N（全量落库；当日榜原子替换——
        # 同日重跑换小集合时旧行不得残留，否则虚增 consecutive_days_in_top）。
        # 无可打分因子（无 active 因子或全无 IC 记录，镜像打分器拒绝条件）→ 显式
        # 降级：L2 跳过，按近 N 日日均成交额降序过渡排序（spec「无可打分因子降级
        # 通道」；MUST NOT 静默等权/伪造权重）
        factors = list_active_factors(db_path=library_db_path)
        ic_stats = recent_ic_stats(factors, db_path=library_db_path)
        if has_scorable_factors(factors, ic_stats):
            candidates = rank_universe(
                panel=active_panel,
                as_of=trade_date,
                passed_codes=passed,
                factors=factors,
                ic_stats=ic_stats,
                top_n=L2_TOP_N,
            )
        else:
            selection_mode = "fallback"
            _logger.warning(
                "选股日批降级（%s）：因子库无可打分因子（active=%d），L2 显式跳过，"
                "按近 %d 日日均成交额降级排序（selection_mode=fallback）",
                trade_date,
                len(factors),
                constraints.MIN_LIQUIDITY_HISTORY_DAYS,
            )
            candidates = liquidity_fallback_rank(
                amounts_by_code=amounts,
                passed_codes=passed,
                as_of=trade_date,
                top_n=L2_TOP_N,
            )
        rewrite_day_l2_board(trade_date, candidates, db_path=db_path)
        l2_top = len(candidates)
        # ④ L3：quick profile 粗筛（事件注入；单只失败降级不阻断）
        client: TriageLLMClient = (
            triage_client if triage_client is not None else GatewayTriageClient()
        )
        events = (
            events_by_symbol
            if events_by_symbol is not None
            else _assemble_events_by_symbol(candidates)
        )
        triage_result = run_triage(
            candidates,
            events_by_symbol=events,
            client=client,
            trade_date=trade_date,
            db_path=db_path,
        )
        decisions = triage_result.decisions
        l3_keep = sum(1 for decision in decisions if decision.verdict == "keep")
        if decisions and len(triage_result.warnings) == len(decisions):
            raise RuntimeError(
                f"L3 粗筛全部降级（{len(decisions)}/{len(decisions)}），疑似 LLM 网关整体不可用："
                "批次按 failed 处理（当日精选池零行）"
            )
        # ⑤ 精选池：keep ∩ 连续在榜门槛达标
        by_symbol = {candidate.symbol: candidate for candidate in candidates}
        pool_rows: list[dict[str, Any]] = []
        for decision in decisions:
            if decision.verdict != "keep":
                continue
            if not eligible_for_selection(
                decision.symbol, trade_date, min_days=POOL_MIN_DAYS, db_path=db_path
            ):
                continue
            candidate = by_symbol[decision.symbol]
            pool_rows.append(
                {
                    "symbol": candidate.symbol,
                    "composite_score": candidate.composite_score,
                    "rank": candidate.rank,
                    "consecutive_days": consecutive_days_in_top(
                        candidate.symbol, trade_date, db_path=db_path
                    ),
                    "l3_verdict": decision.verdict,
                    "l3_reason": decision.reason,
                    "factor_contribs": _candidate_contribs(candidate),
                }
            )
        # ⑥ 批次 ok + 池行（单事务：ok 与池同生共死）
        pool_size = record_screening_batch(
            trade_date,
            status="ok",
            error=None,
            started_at=started_at,
            finished_at=_now_iso(),
            l1_passed=l1_passed,
            l2_top=l2_top,
            l3_keep=l3_keep,
            pool_rows=pool_rows,
            selection_mode=selection_mode,
            db_path=db_path,
        )
        return {
            "trade_date": trade_date,
            "status": "ok",
            "error": None,
            "l1_passed": l1_passed,
            "l2_top": l2_top,
            "l3_keep": l3_keep,
            "pool_size": pool_size,
            "selection_mode": selection_mode,
        }
    except Exception as exc:  # noqa: BLE001 -- fail-closed：任一步失败 → 批次 failed + 零池
        error = f"{type(exc).__name__}: {exc}"
        _logger.warning("选股日批失败（%s）：%s", trade_date, error)
        record_screening_batch(
            trade_date,
            status="failed",
            error=error,
            started_at=started_at,
            finished_at=_now_iso(),
            l1_passed=l1_passed,
            l2_top=l2_top,
            l3_keep=l3_keep,
            selection_mode=selection_mode,
            db_path=db_path,
        )
        return {
            "trade_date": trade_date,
            "status": "failed",
            "error": error,
            "l1_passed": l1_passed,
            "l2_top": l2_top,
            "l3_keep": l3_keep,
            "pool_size": 0,
            "selection_mode": selection_mode,
        }


def run_screening_daily() -> dict[str, Any]:
    """ops 日批入口（add-screening-batch-scheduling）：封装 ``run_screening_batch``。

    - 批次 ``status="failed"``（快照过期等内部失败）→ **上抛** ``RuntimeError``：
      ``run_job`` 记 ``failed`` 运行行，运维界面（评估运维/日批运维）据此可见——
      批次自身的 failed 行（screening_batches）与运行历史两侧一致；
    - 快照库为空且未指定 as_of → 管线直接抛 ``SnapshotNotFreshError``（原样透传，
      同样落 failed 运行行）；
    - 成功 → 摘要原样透传（``trade_date/status/l1_passed/l2_top/l3_keep/pool_size``）。
    """
    result = run_screening_batch()
    if isinstance(result, dict) and result.get("status") == "failed":
        raise RuntimeError(str(result.get("error") or "选股日批失败"))
    return result


def latest_successful_pool(*, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """最近成功批次 + 其当日精选池；无成功批次 → None（前端回退空态/旧日期标注）。

    返回 ``{"batch": {...}, "pool": [池行...]}``；池行含解析后的 ``factor_contribs``。
    """
    batch = latest_successful_batch(db_path)
    if batch is None:
        return None
    return {"batch": batch, "pool": list_selection_pool(batch["trade_date"], db_path=db_path)}


# ── 信号时效性校验（T2 / S2） ───────────────────────────────────────────────

# 原因码（Global Constraints 逐字；供前端/审计消费）
FRESHNESS_SUSPENDED = "SUSPENDED"
FRESHNESS_LIMIT_LOCKED = "LIMIT_LOCKED"
FRESHNESS_NEW_EVENT = "NEW_EVENT"


def _canonical_date_or_none(value: Any) -> str | None:
    """宽松解析（事件日期等外部数据）：非规范/缺失 → None（不猜、不抛）。"""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return text if parsed.isoformat() == text else None


def _recent_event_bound(as_of: str, calendar: Sequence[str] | None) -> str:
    """「新事件」下界：``as_of`` 有日历取前一交易日，否则只认当日（保守下限）。"""
    if not calendar:
        return as_of
    prior = sorted({day for day in (_canonical_date_or_none(entry) for entry in calendar) if day})
    earlier = [day for day in prior if day < as_of]
    return earlier[-1] if earlier else as_of


def check_signal_freshness(
    symbols: Sequence[str],
    *,
    snapshot_rows: dict[str, dict],
    events_by_symbol: dict[str, list[dict]],
    as_of: str,
    calendar: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """深度分析前的信号时效性校验（S2）：最新可得状态重校验，问题者移除。

    检查顺序逐字（全部命中原因都返回，不短路）：``SUSPENDED``（快照行缺失或
    ``is_suspended`` 为真——缺失视同不可交易，fail-closed）→ ``LIMIT_LOCKED``
    （``limit_up_sealed``/``limit_down_sealed``）→ ``NEW_EVENT``（任一事件日期 ≥
    ``as_of`` 的**前一交易日**；``calendar`` 缺省/为空时下界退化为 ``as_of`` 当日，
    保守下限不猜前一交易日）。事件日期缺失/不可解析 → 不计入（events 模块兜底占位
    无日期，不得一律判「新」）。

    返回 ``[{"symbol", "reason", "detail"}]``（``symbols`` 输入序 × S2 原因序）；
    干净标的**不出现**在返回列表。
    """
    normalized_as_of = _canonical_date(as_of, "as_of")
    bound = _recent_event_bound(normalized_as_of, calendar)
    findings: list[dict[str, Any]] = []
    for symbol in symbols:
        row = snapshot_rows.get(symbol) or {}
        if symbol not in snapshot_rows:
            findings.append(
                {
                    "symbol": symbol,
                    "reason": FRESHNESS_SUSPENDED,
                    "detail": "无当日快照行（视同不可交易）",
                }
            )
        elif row.get("is_suspended"):
            findings.append({"symbol": symbol, "reason": FRESHNESS_SUSPENDED, "detail": "停牌"})
        if row.get("limit_up_sealed") and row.get("limit_down_sealed"):
            locked_detail = "一字涨跌停封板"
        elif row.get("limit_up_sealed"):
            locked_detail = "一字涨停封板"
        elif row.get("limit_down_sealed"):
            locked_detail = "一字跌停封板"
        else:
            locked_detail = ""
        if locked_detail:
            findings.append(
                {"symbol": symbol, "reason": FRESHNESS_LIMIT_LOCKED, "detail": locked_detail}
            )
        fresh_events = []
        for event in events_by_symbol.get(symbol, []):
            if not isinstance(event, dict):
                continue
            event_date = _canonical_date_or_none(event.get("date"))
            if event_date is not None and event_date >= bound:
                fresh_events.append((event_date, event))
        if fresh_events:
            event_date, event = max(fresh_events, key=lambda item: item[0])
            title = str(event.get("title") or "").strip()
            detail = f"新事件 {event_date}" + (f"：{title}" if title else "")
            findings.append({"symbol": symbol, "reason": FRESHNESS_NEW_EVENT, "detail": detail})
    return findings
