"""知识泄漏探针：模型对候选回测窗口的记忆命中率（实证披露，非净化假设）。

背景与定位
----------
回测腿快（样本可批量构造，不必等日历），但它有一个 forward 腿没有的威胁：
**LLM 参数化记忆泄漏**——as-of 快照能截断输入数据，截不断模型对历史走势的
记忆。本模块对候选窗口的抽样标的执行记忆探测，产出「模型记得多少」的实证
读数，随批次报告披露（spec `decision-backtest`「知识泄漏探针」）。

裸问条件（关键设计）
--------------------
探针**不提供任何 as-of 快照**：prompt 里只有标的代码与日期区间，没有行情 /
财报 / 新闻输入。这正是要测的东西——不是「给足数据能否推理」，而是「不给
数据，模型自己记得多少」。命中率显著高于随机（方向二分类 50%）即说明模型对
该窗口有可用记忆。因此本模块是**实证披露**装置：它无法根除参数化记忆，只能
把「记忆污染程度」变成可披露、可降级的量（超阈 → 结论降级句式）。

三层题（每题独立三态：答对 / 答错 / 未知）
------------------------------------------
① 方向题（主指标）：区间内标的相对沪深300涨跌方向；
② 幅度桶题：区间相对涨跌幅落在哪个桶（±2% 带同源分桶，`caliber.NEUTRAL_BAND`）；
③ 事件题（辅证）：区间内是否有重大事件；真值取自既有新闻源标题关键词 +
   区间相对涨跌幅代理。

真值口径与结算一致：K 线走 `SETTLEMENT_ADJUST`（后复权 hfq，Δ4 Task 1 统一），
区间相对涨跌 = 标的后复权区间收益 − 沪深300 区间收益。

三态语义（spec：探针失败不静默）
--------------------------------
- 答对 / 答错：真值可得且答案可解析；
- 未知（unknown）：拒答、JSON 不可解析、枚举值非法，以及**真值不可得**
  （K 线 / 基准缺失、决策日后未走满 `window_days`、新闻源空）。未知**不折算**
  为答对或答错，只计入 `unknown_ratio` 随报告披露。

阈值与降级
----------
`direction_hit_rate > LEAKAGE_PROBE_THRESHOLD`（默认 0.60，预登记 owner 可调）
→ `downgraded=True`，该批结论降级为「泄漏污染下的上界证据（真实 skill ≤ 读数）」。
命中率分母**只含可解析答题数**；分母为 0 → `None`（**不报 0**——0 是「全答错」
的强主张，无样本时不得冒充）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial
from typing import Any

import numpy as np
import pandas as pd

from evals.outcome.caliber import LEAKAGE_PROBE_THRESHOLD, NEUTRAL_BAND
from finance_agent.data.akshare_client import SETTLEMENT_ADJUST

BENCHMARK_INDEX_CODE = "000300"  # 沪深300（基准，区间相对涨跌）
QUESTIONS_PER_TICKER = 3  # 方向 / 幅度桶 / 事件
LARGE_MOVE = 0.05  # 大涨 / 大跌门槛（±2% 中性带之外的「大幅」口径）
EVENT_MOVE_PROXY = 0.15  # 无新闻关键词时，区间大幅波动作为「有事件」的价格代理
NEWS_LIMIT = 20
KLINE_DAYS = 250  # 单次取数天数（候选窗口为近端，250 交易日足以覆盖 T+20）

DIRECTION_PROMPT = (
    "回答一个事实性问题，不要解释：{ticker} 在 {start} 至 {end} 区间内，"
    '收盘价相对沪深300指数是上涨还是下跌？只回答 JSON：{{"direction": "up"|"down"}}'
)
MAGNITUDE_PROMPT = (
    "回答一个事实性问题，不要解释：{ticker} 在 {start} 至 {end} 区间内，"
    "相对沪深300指数的涨跌幅落在哪个区间？"
    '只回答 JSON：{{"bucket": "大涨"|"小涨"|"持平"|"小跌"|"大跌"}}'
)
EVENT_PROMPT = (
    "回答一个事实性问题，不要解释：{ticker} 在 {start} 至 {end} 区间内，"
    "是否发生重大事件（并购/重组/暴雷/政策等）？"
    '只回答 JSON：{{"event": "有"|"无"|"不确定"}}'
)

_DIRECTIONS = {"up", "down"}
_BUCKETS = {"大涨", "小涨", "持平", "小跌", "大跌"}
_EVENTS = {"有", "无"}
# 重大事件关键词（真值代理：标题命中即视为有事件；口径与「辅证题」定位匹配）
_EVENT_KEYWORDS = (
    "并购",
    "重组",
    "暴雷",
    "立案",
    "处罚",
    "退市",
    "商誉减值",
    "违约",
    "重大合同",
    "中标",
    "减持",
    "增持",
    "回购",
    "业绩预告",
    "涨停",
    "跌停",
    "政策",
)


def _default_llm(prompt: str) -> str:
    """默认 LLM 通道：`complete_text(purpose="judge", temperature=0.0)`。

    最小封装先例：`evals/claim_benchmark/llm_label.py::_call_llm`。测试一律
    注入 `llm=` 回调，零网络零 LLM。
    """
    from finance_agent.llm.gateway import complete_text

    model = os.getenv("JUDGE_MODEL", os.getenv("LLM_MODEL", "openai/deepseek-v4-flash"))
    base_url = os.getenv("JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL", "") or ""
    api_key = os.getenv("JUDGE_API_KEY") or os.getenv("LLM_API_KEY", "") or ""
    text, _meta = complete_text(
        [{"role": "user", "content": prompt}],
        purpose="judge",
        temperature=0.0,
        llm_config={"model": model, "baseUrl": base_url, "apiKey": api_key},
        trace={"name": "leakage_probe", "metadata": {"environment": "backtest-leakage-probe"}},
    )
    return text


def _sample_tickers(codes: list[str], n_tickers: int, seed: int) -> list[str]:
    """确定性抽样（同 seed 同结果）：去重排序后不放回抽 n_tickers 只。"""
    pool = sorted({str(c).strip() for c in codes if str(c).strip()})
    k = min(int(n_tickers), len(pool))
    if k <= 0:
        return []
    rng = np.random.default_rng(seed)
    return [str(c) for c in rng.choice(pool, size=k, replace=False)]


def _dates_of(df: pd.DataFrame) -> pd.Series:
    return df["日期"].astype(str).str[:10]


def _window(
    kline: pd.DataFrame | None, decision_date: str, window_days: int
) -> pd.DataFrame | None:
    """决策日后的前 window_days 个交易日；不足（含空表/缺列）→ None（真值不可得）。"""
    if kline is None or not isinstance(kline, pd.DataFrame) or kline.empty:
        return None
    if "日期" not in kline.columns or "收盘" not in kline.columns:
        return None
    after = kline[_dates_of(kline) > decision_date].reset_index(drop=True)
    if len(after) < window_days:
        return None
    return after.iloc[:window_days]


def _window_return(window: pd.DataFrame) -> float:
    closes = window["收盘"].astype(float)
    return float(closes.iloc[-1] / closes.iloc[0] - 1.0)


def _aligned_return(index_kline: pd.DataFrame | None, start: str, end: str) -> float | None:
    """基准指数在 [start, end] 上的区间收益；覆盖不足 → None。"""
    if index_kline is None or not isinstance(index_kline, pd.DataFrame) or index_kline.empty:
        return None
    if "日期" not in index_kline.columns or "收盘" not in index_kline.columns:
        return None
    dates = _dates_of(index_kline)
    seg = index_kline[(dates >= start) & (dates <= end)]
    if len(seg) < 2:
        return None
    closes = seg["收盘"].astype(float)
    return float(closes.iloc[-1] / closes.iloc[0] - 1.0)


def _direction_of(rel: float) -> str:
    return "up" if rel > 0 else "down"


def _bucket_of(rel: float) -> str:
    """±2% 中性带同源分桶（caliber.NEUTRAL_BAND）；>5% 为大幅。"""
    if rel > LARGE_MOVE:
        return "大涨"
    if rel > NEUTRAL_BAND:
        return "小涨"
    if rel >= -NEUTRAL_BAND:
        return "持平"
    if rel >= -LARGE_MOVE:
        return "小跌"
    return "大跌"


def _truth_event(news: list[dict] | None, rel: float | None) -> str | None:
    """事件真值：新闻源空 → None（不可得）；标题关键词或大幅波动代理 → 有。"""
    if not news:
        return None
    titles = " ".join(str(item.get("title") or "") for item in news)
    if any(keyword in titles for keyword in _EVENT_KEYWORDS):
        return "有"
    if rel is not None and abs(rel) >= EVENT_MOVE_PROXY:
        return "有"
    return "无"


def _answer_value(text: str, key: str, allowed: set[str]) -> str | None:
    """解析 LLM 回答 → 合法枚举值；拒答 / 不可解析 / 枚举非法 → None（未知）。"""
    from finance_agent.nodes._llm_utils import parse_json_response

    try:
        data = parse_json_response(text)
    except Exception:  # noqa: BLE001 - 任何解析失败一律计未知（不折算答错）
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value in allowed else None


def _safe(call: Callable[[], Any], default: Any) -> Any:
    """取数异常（网络/接口变更）不炸整批：真值不可得 → 未知。"""
    try:
        return call()
    except Exception:  # noqa: BLE001
        return default


def _grade(
    *,
    ticker: str,
    kind: str,
    prompt: str,
    raw: str,
    truth: str | None,
    key: str,
    allowed: set[str],
) -> dict[str, Any]:
    if truth is None:
        return {
            "ticker": ticker,
            "kind": kind,
            "prompt": prompt,
            "answer": None,
            "truth": None,
            "hit": None,
            "unknown": True,
            "reason": "truth_unavailable",
        }
    answer = _answer_value(raw, key, allowed)
    if answer is None:
        return {
            "ticker": ticker,
            "kind": kind,
            "prompt": prompt,
            "answer": raw[:80],
            "truth": truth,
            "hit": None,
            "unknown": True,
            "reason": "refused_or_unparseable",
        }
    hit = answer == truth
    return {
        "ticker": ticker,
        "kind": kind,
        "prompt": prompt,
        "answer": answer,
        "truth": truth,
        "hit": hit,
        "unknown": False,
        "reason": "ok",
    }


def run_leakage_probe(
    codes: list[str],
    decision_date: str,
    *,
    window_days: int = 20,
    n_tickers: int = 10,
    seed: int = 42,
    client: Any = None,
    llm: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """对候选窗口抽样标的执行三层题裸问记忆探测，返回命中率与未知占比。

    Args:
        codes: 候选标的池（去重后确定性抽样）。
        decision_date: 回测决策日（窗口起点，ISO `YYYY-MM-DD`）。
        window_days: 主评估窗口交易日数（默认 20，与结算主窗口同源）。
        n_tickers: 抽样标的数（spec：每批 ≥10 只 × 3 问）。
        seed: 抽样种子（同 seed 完全可复现）。
        client: AKShare 客户端（需 fetch_kline/fetch_index_kline/fetch_news）；
            None → 延迟构造 `AKShareClient()`。
        llm: 答题回调 `prompt -> text`；None → `_default_llm`（真实网关）。

    Returns:
        含 probe_n / questions_per_ticker / direction_hit_rate / magnitude_hit_rate /
        event_hit_rate / unknown_ratio / threshold / downgraded / details 的字典。
        命中率分母只含可解析答题数，无样本 → None。
    """
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    ask = llm if llm is not None else _default_llm

    tickers = _sample_tickers(codes, n_tickers, seed)
    index_kline = _safe(
        partial(client.fetch_index_kline, BENCHMARK_INDEX_CODE, days=KLINE_DAYS), None
    )

    details: list[dict[str, Any]] = []
    for ticker in tickers:
        kline = _safe(
            partial(client.fetch_kline, ticker, days=KLINE_DAYS, adjust=SETTLEMENT_ADJUST),
            None,
        )
        window = _window(kline, decision_date, window_days)
        if window is None:
            for kind, key, allowed in (
                ("direction", "direction", _DIRECTIONS),
                ("magnitude", "bucket", _BUCKETS),
                ("event", "event", _EVENTS),
            ):
                details.append(
                    _grade(
                        ticker=ticker,
                        kind=kind,
                        prompt="",
                        raw="",
                        truth=None,
                        key=key,
                        allowed=allowed,
                    )
                )
            continue

        dates = _dates_of(window)
        start, end = str(dates.iloc[0]), str(dates.iloc[-1])
        rel: float | None = None
        if index_kline is not None:
            bench = _aligned_return(index_kline, start, end)
            if bench is not None:
                rel = _window_return(window) - bench

        news = _safe(partial(client.fetch_news, ticker, limit=NEWS_LIMIT), None)

        specs = (
            (
                "direction",
                DIRECTION_PROMPT,
                "direction",
                _DIRECTIONS,
                None if rel is None else _direction_of(rel),
            ),
            (
                "magnitude",
                MAGNITUDE_PROMPT,
                "bucket",
                _BUCKETS,
                None if rel is None else _bucket_of(rel),
            ),
            ("event", EVENT_PROMPT, "event", _EVENTS, _truth_event(news, rel)),
        )
        for kind, template, key, allowed, truth in specs:
            prompt = template.format(ticker=ticker, start=start, end=end)
            raw = "" if truth is None else str(_safe(partial(ask, prompt), ""))
            details.append(
                _grade(
                    ticker=ticker,
                    kind=kind,
                    prompt=prompt,
                    raw=raw,
                    truth=truth,
                    key=key,
                    allowed=allowed,
                )
            )

    total = len(tickers) * QUESTIONS_PER_TICKER
    unknown = sum(1 for d in details if d["unknown"])

    def _rate(kind: str) -> float | None:
        graded = [d for d in details if d["kind"] == kind and not d["unknown"]]
        if not graded:
            return None
        return sum(1 for d in graded if d["hit"]) / len(graded)

    direction_rate = _rate("direction")
    return {
        "probe_n": len(tickers),
        "questions_per_ticker": QUESTIONS_PER_TICKER,
        "direction_hit_rate": direction_rate,
        "magnitude_hit_rate": _rate("magnitude"),
        "event_hit_rate": _rate("event"),
        "unknown_ratio": (unknown / total) if total else 0.0,
        "threshold": LEAKAGE_PROBE_THRESHOLD,
        "downgraded": direction_rate is not None and direction_rate > LEAKAGE_PROBE_THRESHOLD,
        "details": details,
    }
