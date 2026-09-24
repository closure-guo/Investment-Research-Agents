"""回测设施小规模试跑驱动（评估体系开机任务清单 · 任务 2）。

配置硬上限：1 个 regime（2023 震荡段）× 3 标的 × 1 次重复，模型 deepseek-chat。
全链路：data_snapshot（前视截断）→ replay（TradeDecision）→ performance（四指标）
→ baselines（Buy-and-Hold/MACD/KDJ/RSI）→ significance（block bootstrap）。

另做两件核查：
1. 截断断言：抽 1 条样本重建快照，断言 K 线/财报/宏观/新闻日期均 ≤ 决策日；
2. LLM 成本记账：包装 litellm_adapter 的 raw_completion/raw_stream，逐条记录
   调用次数与 token 用量（流式注入 stream_options.include_usage 取真值，
   取不到则标记 estimated）。

产物：reports/backtest/pilot-2023-shock-<ts>.json（报告 + 成本台账 + 截断核查）。

用法：
    uv run python tests/scripts/backtest_pilot_2023.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402
from evals.backtest.data_snapshot import build_snapshot  # noqa: E402
from evals.backtest.run_backtest import run_backtest  # noqa: E402
from evals.backtest.sampling import classify_regime  # noqa: E402

from finance_agent.data.akshare_client import (  # noqa: E402
    SETTLEMENT_ADJUST,
    AKShareClient,
)
from finance_agent.outcome.track_record.job import BENCHMARK_CODE  # noqa: E402

CODES = ["002412", "600519", "300308"]  # 汉森制药 / 贵州茅台 / 中际旭创（同冒烟三标的）
REGIME = "sideways"
REPEATS = 1
WINDOW_DAYS = 120

# ---------------------------------------------------------------- 成本记账

_usage_ledger: list[dict[str, Any]] = []
_stream_include_usage_supported = True


def _record_usage(tag: str, usage: Any, estimated: bool) -> None:
    _usage_ledger.append(
        {
            "path": tag,
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
            "estimated": estimated,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
    )


_TRANSIENT_HINTS = (
    "burst",
    "rate.?limit",
    "APIConnectionError",
    "429",
    "slow down",
    # incident 016/017 同族：半僵流（chunk 超时）是瞬时故障，adapter 层重试
    "半僵流",
    "chunk 超过",
)


def _is_transient(exc: Exception) -> bool:
    import re

    msg = str(exc)
    return any(re.search(hint, msg, re.IGNORECASE) for hint in _TRANSIENT_HINTS)


def _retry_transient(fn, *, attempts: int = 6, base_delay: float = 8.0):
    """瞬时故障（限流/连接重置，如 ark burst 保护）指数退避重试。

    试跑 harness 层兜底——litellm 的 fallback 链在此环境会误切到已失效的
    官方 key 直接崩溃，故在 adapter 层就重试原始调用。ark「System protection
    triggered by request burst」需要较长冷却（实测短退避 4×2^n 顶不住），
    默认 6 次、8/16/32/64/128/256s。
    """
    import time

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_transient(exc):
                raise
            time.sleep(base_delay * (2**attempt))
    raise last  # type: ignore[misc]


def install_usage_meter() -> None:
    """包装 adapter 的 raw_completion/raw_stream（gateway 内均为调用时惰性导入，
    模块属性补丁可拦截全部非流式/流式路径）。"""
    from finance_agent.llm.adapters import litellm_adapter

    original_completion = litellm_adapter.raw_completion
    original_stream = litellm_adapter.raw_stream

    def metered_completion(**kwargs: Any) -> Any:
        resp = _retry_transient(lambda: original_completion(**kwargs))
        _record_usage("raw_completion", getattr(resp, "usage", None), estimated=False)
        return resp

    def metered_stream(**kwargs: Any) -> Any:
        global _stream_include_usage_supported
        if _stream_include_usage_supported and "stream_options" not in kwargs:
            kwargs["stream_options"] = {"include_usage": True}

        def _fresh_gen():
            try:
                return original_stream(**kwargs)
            except Exception:
                if kwargs.get("stream_options"):
                    _stream_include_usage_supported = False
                    kwargs.pop("stream_options", None)
                    return original_stream(**kwargs)
                raise

        def _wrapper():
            import time

            usage = None
            attempt = 0
            while True:
                try:
                    for chunk in _fresh_gen():
                        chunk_usage = getattr(chunk, "usage", None)
                        if chunk_usage is not None:
                            usage = chunk_usage
                        # include_usage 的末 chunk choices=[]，下游消费方 choices[0] 会
                        # IndexError——记账后丢弃，不透传给消费方
                        if not getattr(chunk, "choices", None):
                            continue
                        yield chunk
                    _record_usage("raw_stream", usage, estimated=usage is None)
                    return
                except Exception as exc:  # noqa: BLE001
                    if not _is_transient(exc) or attempt >= 5:
                        raise
                    attempt += 1
                    time.sleep(8 * (2**attempt))

        return _wrapper()

    litellm_adapter.raw_completion = metered_completion
    litellm_adapter.raw_stream = metered_stream


# ---------------------------------------------------------------- 数据源适配（pilot 进程内补丁）
#
# 2026-09-01 实测：本机到 push2his.eastmoney.com（akshare 指数/个股历史 K 线主源）
# 连接被重置（TLS renegotiate 后 RST），财报 datacenter-web 与新浪源正常。
# 个股 K 线有客户端内置新浪回退；指数 K 线无回退 → 进程内补丁到新浪源。
# 补丁只存在于本试跑进程，不改仓库代码；数据源替换在试跑记录中披露。


def _sina_kline_to_cn(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "date": "日期",
        "open": "开盘",
        "close": "收盘",
        "high": "最高",
        "low": "最低",
        "volume": "成交量",
        "amount": "成交额",
        "turnover": "换手率",
    }
    df = df.rename(columns=rename_map)
    if "日期" in df.columns:
        df["日期"] = df["日期"].astype(str).str[:10]
    return df.sort_values("日期").reset_index(drop=True)


def install_index_kline_patch() -> None:
    """AKShareClient 三处进程内补丁（试跑 harness，不动仓库代码）：

    1. fetch_index_kline / fetch_benchmark_kline → 新浪指数日 K（东财指数源本机不可达）；
    2. fetch_kline days 默认提至 1500（issue #104：fetch_data 默认 250 日，
       历史决策日截断后 K 线为空）；
    3. 全部加轻量重试（issue #103：并发下新浪源间歇返回空/失败）。
    """
    import time

    import akshare as ak

    def _retry(fn, *args, **kwargs):
        for attempt in range(4):
            try:
                df = fn(*args, **kwargs)
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
        return pd.DataFrame()

    def _fetch_index_sina(self: Any, index_code: str, days: int = 250) -> pd.DataFrame:
        symbol = f"sh{index_code}" if str(index_code).startswith(("0", "6")) else str(index_code)
        df = _retry(ak.stock_zh_index_daily, symbol=symbol)
        if df.empty:
            return df
        return _sina_kline_to_cn(df).tail(days).reset_index(drop=True)

    AKShareClient.fetch_index_kline = _fetch_index_sina  # type: ignore[method-assign]

    def _fetch_benchmark(self: Any, days: int = 1500) -> pd.DataFrame:
        return _fetch_index_sina(self, BENCHMARK_CODE, days=days)

    AKShareClient.fetch_benchmark_kline = _fetch_benchmark  # type: ignore[method-assign]

    def _fetch_kline_sina(
        self: Any, stock_code: str, days: int = 1500, *, adjust: str = "qfq", **kwargs: Any
    ) -> pd.DataFrame:
        df = _retry(
            ak.stock_zh_a_daily,
            symbol=self._to_sina_symbol(stock_code),
            adjust=adjust,
            **kwargs,
        )
        if df.empty:
            return df
        return _sina_kline_to_cn(df).tail(days).reset_index(drop=True)

    AKShareClient.fetch_kline = _fetch_kline_sina  # type: ignore[method-assign]


# ---------------------------------------------------------------- 抽样


def find_sideways_decision_date(index_kline: pd.DataFrame, year: int = 2023) -> str:
    """在指定年份内找第一个 120 日窗口为 sideways 的窗口末日（决策日）。"""
    dates = index_kline["日期"].astype(str).str[:10]
    n = len(index_kline)
    for end in range(WINDOW_DAYS, n + 1, 5):
        decision_date = str(dates.iloc[end - 1])
        if not decision_date.startswith(str(year)):
            continue
        window = index_kline.iloc[end - WINDOW_DAYS : end]
        if classify_regime(window) == REGIME:
            return decision_date
    raise ValueError(f"{year} 年内未找到 {REGIME} 窗口（window={WINDOW_DAYS}d）")


# ---------------------------------------------------------------- 截断核查


def verify_truncation(code: str, decision_date: str) -> dict:
    """抽 1 条样本重建快照，断言所有时点数据 ≤ 决策日（前视截断实证）。"""
    snap = build_snapshot(code, decision_date)
    state = snap.state
    checks: dict[str, Any] = {"code": code, "decision_date": decision_date}

    kline = state.get("kline")
    if isinstance(kline, pd.DataFrame) and not kline.empty:
        max_date = str(kline["日期"].astype(str).str[:10].max())
        checks["kline_max_date"] = max_date
        checks["kline_truncated"] = max_date <= decision_date
    else:
        # 快照缺 K 线是硬失败（issue #103：并发 fetch 下新浪回退不稳），不得静默跳过
        checks["kline_max_date"] = None
        checks["kline_truncated"] = False
        checks["kline_missing"] = True
    for key in ("balance_sheet", "income_statement", "financial_indicators"):
        df = state.get(key)
        if isinstance(df, pd.DataFrame) and not df.empty:
            col = next((c for c in ("报告日", "日期") if c in df.columns), None)
            if col:
                from evals.backtest.data_snapshot import disclosure_deadline

                deadlines = [disclosure_deadline(str(v)) for v in df[col].dropna().tolist()]
                cutoff = decision_date.replace("-", "")
                checks[f"{key}_max_disclosure"] = max(deadlines) if deadlines else None
                checks[f"{key}_truncated"] = all(d <= cutoff for d in deadlines)
    macro = state.get("macro_indicators")
    if isinstance(macro, dict):
        from evals.backtest.data_snapshot import _record_month

        months = [
            m
            for v in macro.values()
            if isinstance(v, dict) and isinstance(v.get("records"), list)
            for r in v["records"]
            if isinstance(r, dict) and (m := _record_month(r)) is not None
        ]
        if months:
            checks["macro_max_month"] = max(months)
            checks["macro_truncated"] = max(months) <= decision_date[:7]
    news = state.get("news_list") or []
    if news:
        news_dates = [
            str(item.get("date") or item.get("发布时间") or "")[:10]
            for item in news
            if isinstance(item, dict)
        ]
        news_dates = [d for d in news_dates if d]
        checks["news_max_date"] = max(news_dates) if news_dates else None
        checks["news_truncated"] = all(d <= decision_date for d in news_dates)
    checks["excluded_fields"] = snap.metadata.get("excluded_fields")
    checks["all_passed"] = all(v for k, v in checks.items() if k.endswith("_truncated"))
    return checks


# ---------------------------------------------------------------- 主流程


def _pin_pipeline_model() -> None:
    """试跑模型钉定（用户裁决 2026-09-02）：
    - 分析管线 = glm-5.3（.env 生产默认，火山方舟）——试跑与生产同模型，数字可迁移；
    - judge = deepseek-v4-flash，端点从 opencode zen 换成 ark（zen 余额见底）。

    历史偏差记录：首两轮试跑曾误用 deepseek-v4-flash 作分析模型（deepseek-chat
    官方 key 失效后的错误替代），已弃用并按此裁决重跑。"""
    ark_base_url = os.environ.get("LLM_BASE_URL", "")
    ark_api_key = os.environ.get("LLM_API_KEY", "")
    print("[pilot] 分析模型: glm-5.3（ark）/ judge: deepseek-v4-flash（ark）", flush=True)

    os.environ["LLM_MODEL"] = "openai/glm-5.3"
    os.environ["LLM_BASE_URL"] = ark_base_url
    os.environ["LLM_API_KEY"] = ark_api_key
    # judge 切 ark（zen 凭据不可用）；judge 保持 deepseek-v4-flash，与分析模型分离
    os.environ["JUDGE_MODEL"] = "openai/deepseek-v4-flash"
    os.environ["JUDGE_BASE_URL"] = ark_base_url
    os.environ["JUDGE_API_KEY"] = ark_api_key
    os.environ.pop("LLM_QUICK_MODEL", None)
    os.environ["LLM_REASONING_EFFORT"] = os.environ.get("LLM_REASONING_EFFORT", "low")


def main() -> None:

    from dotenv import load_dotenv

    load_dotenv()
    _pin_pipeline_model()
    install_usage_meter()
    install_index_kline_patch()

    client = AKShareClient()
    print("拉取指数与标的 K 线…", flush=True)
    index_kline = client.fetch_index_kline(BENCHMARK_CODE, days=1500)
    decision_date = find_sideways_decision_date(index_kline, year=2023)
    print(f"2023 震荡段决策日: {decision_date}", flush=True)
    klines = {code: client.fetch_kline(code, days=1500, adjust=SETTLEMENT_ADJUST) for code in CODES}

    sample = [{"code": code, "regime": REGIME, "decision_date": decision_date} for code in CODES]
    print(f"样本: {sample}（repeats={REPEATS}）", flush=True)

    report = run_backtest(
        sample,
        klines,
        benchmark_kline=index_kline,
        repeats=REPEATS,
        sanity_note="pilot 通路验证，样本量 3 不具备统计意义",
    )

    print("回放完成，执行截断核查…", flush=True)
    truncation = verify_truncation(CODES[0], decision_date)

    total_prompt = sum(r["prompt_tokens"] for r in _usage_ledger)
    total_completion = sum(r["completion_tokens"] for r in _usage_ledger)
    usage_summary = {
        "llm_calls": len(_usage_ledger),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "estimated_calls": sum(1 for r in _usage_ledger if r["estimated"]),
        "per_sample_avg_calls": len(_usage_ledger) / len(sample),
        "per_sample_avg_tokens": (total_prompt + total_completion) / len(sample),
        "ledger": _usage_ledger,
    }

    out = {
        "pilot": "backtest-pilot-2023-shock",
        "config": {
            "codes": CODES,
            "regime": REGIME,
            "decision_date": decision_date,
            "repeats": REPEATS,
            "window_days": WINDOW_DAYS,
        },
        "truncation_check": truncation,
        "usage": usage_summary,
        "report": report,
    }
    out_dir = Path("reports/backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pilot-2023-shock-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"试跑产物已写入 {path}")
    print(
        json.dumps(
            {
                "truncation": truncation,
                "usage": {k: v for k, v in usage_summary.items() if k != "ledger"},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
