"""add-hallucination-rate-metric nightly @live：真实报告 + 真实行情校验。

@live 为显式 opt-in：仅当 RUN_LIVE=1 时运行——凭据可能被全量跑批的前序用例
带入进程环境，「凭据在环境里」不构成运行判据（#158）。无 LANGFUSE key 跳过；
akshare 行情失败时数据源缺失 → claim 全部 unverifiable（如实报告，不因网络
波动硬失败）；本地 sessions.db 无深度报告属环境前置不满足 → 显式跳过。
报告落 reports/。
"""

import json
import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from evals.hallucination.measure import render_report, run_offline

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_env() -> bool:
    # @live 统一门禁（issue #605）：原 #158 内联实现收编到 _live_guard
    from tests.evals._live_guard import require_live_opt_in

    return require_live_opt_in()


def _factual_llm():
    """事实型 claim 抽取用 LLM 客户端（invoke(prompt) -> str，judges 同款约定）。

    LLM 凭据缺失返回 None → run_offline 内 extract_factual_claims 优雅回退为空，
    数值型路径不受影响（nightly 无 key 时报告内容与 v1 一致）。
    """
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return None

    from finance_agent.llm.gateway import complete_text

    class _Client:
        def invoke(self, prompt: str) -> str:
            text, _ = complete_text(
                [{"role": "user", "content": prompt}],
                purpose="main",
                temperature=0.0,
            )
            return text

    return _Client()


def _latest_deep_report(db_path: Path) -> tuple[str, str] | None:
    """取最近一条深度会话的 assistant 最终报告文本 + 股票代码。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT session_id, stock_code FROM sessions WHERE session_type='analysis' "
        "ORDER BY created_at DESC LIMIT 3"
    ).fetchall()
    for r in rows:
        events = conn.execute(
            "SELECT event_json FROM session_events WHERE session_id=?",
            (r["session_id"],),
        ).fetchall()
        # 深度分析会话的报告按 report_chunk（text 字段）流式落库；chat 会话为
        # chat_token（token 字段）。事件 schema 演进：2026-09 起分析报告走 report_chunk
        parts: list[str] = []
        for e in events:
            j = json.loads(e["event_json"])
            etype = j.get("type")
            if etype == "chat_token":
                parts.append(j.get("token") or "")
            elif etype == "report_chunk":
                parts.append(j.get("text") or "")
        text = "".join(parts)
        if len(text) > 200:
            conn.close()
            return text, str(r["stock_code"] or "")
    conn.close()
    return None


def test_hallucination_live_report(live_env: bool):
    db_path = Path(os.environ.get("SESSIONS_DB_PATH", "data/sessions.db"))
    found = _latest_deep_report(db_path)
    if not found:
        # 样本不足是环境前置不满足，不是被测系统缺陷——显式跳过并说明，不硬失败
        pytest.skip(f"{db_path} 无深度会话报告可用（sample 不足），显式跳过")
    report_text, stock_code = found

    data_map: dict[str, float] = {}
    try:
        from finance_agent.data.akshare_client import AKShareClient

        quote = AKShareClient().fetch_stock_quote(stock_code)
        if quote.get("price") is not None:
            data_map["price"] = float(quote["price"])
        if quote.get("pct_change") is not None:
            data_map["pct"] = float(quote["pct_change"])
        if quote.get("market_cap"):
            data_map["cap_billion"] = float(quote["market_cap"]) / 1e8
        pe = quote.get("PE_ttm") or quote.get("PE")
        if pe is not None:
            data_map["pe"] = float(pe)
        if quote.get("PB") is not None:
            data_map["pb"] = float(quote["PB"])
    except Exception as e:  # noqa: BLE001 - 行情失败降级为无数据源
        print(f"[HALLUCINATION] 行情拉取失败（数据源缺失，claim 归 unverifiable）: {e}")

    # measure.py 契约：data_map 必须带 source（独立快照来源与时点，与报告生成管道解耦）
    if data_map:
        data_map["source"] = f"snapshot:akshare-{date.today().isoformat()}"

    # ROE 数据源：fetch_indicators 最新期净资产收益率（财务比率校验）
    if stock_code:
        try:
            from finance_agent.data.akshare_client import AKShareClient

            ind = AKShareClient().fetch_indicators(stock_code.lstrip("sh").lstrip("sz"))
            if ind is not None and not ind.empty and "净资产收益率(%)" in ind.columns:
                latest_roe = ind.iloc[-1]["净资产收益率(%)"]
                if latest_roe is not None:
                    data_map["roe"] = float(latest_roe)
        except Exception as e:  # noqa: BLE001 - 财务源失败维持 unverifiable
            print(f"[HALLUCINATION] 财务指标拉取失败: {e}")

    # kline 兜底：spot 接口限流/失败时，由日 K 末两根收盘推导 price/pct
    if "price" not in data_map and stock_code:
        try:
            from finance_agent.data.akshare_client import AKShareClient

            kline = AKShareClient().fetch_kline(stock_code.lstrip("sh").lstrip("sz"), days=10)
            if kline is not None and len(kline) >= 2:
                closes = kline["收盘"].astype(float)
                data_map["price"] = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                if prev:
                    data_map["pct"] = round((float(closes.iloc[-1]) / prev - 1) * 100, 2)
                print(
                    f"[HALLUCINATION] spot 失败，kline 兜底：price={data_map.get('price')} pct={data_map.get('pct')}"
                )
        except Exception as e:  # noqa: BLE001 - 兜底也失败则维持无数据源
            print(f"[HALLUCINATION] kline 兜底失败: {e}")

    result = run_offline(report_text, data_map, llm=_factual_llm())
    from datetime import datetime

    out = Path("reports") / f"hallucination-report-{datetime.now():%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(result), encoding="utf-8")
    print(
        f"[HALLUCINATION] {stock_code} claims {len(result.claims)}"
        f"（可验证 {result.countable}/不可验证 {result.unverifiable}）"
        f" 幻觉率 {f'{result.rate:.2%}' if result.rate is not None else '—'}；报告: {out}"
    )
