# tests/outcome/test_outcome_live.py
"""@live 用例:真实 AKShare 行情跑结算逻辑(不调 LLM),nightly 防 AKShare 漂移。"""

import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("DEEPSEEK_API_KEY"), reason="需 DEEPSEEK_API_KEY(与 nightly 同开关)"
    ),
]


def test_live_settle_with_real_kline():
    """真实 600519 日 K + 合成决策,结算逻辑产出合法结果(防 AKShare 列名/接口漂移)。"""
    from finance_agent.outcome.settle import evaluate_decision

    from finance_agent.data.akshare_client import AKShareClient

    client = AKShareClient()
    kline = client.fetch_kline("600519", days=60)
    benchmark = client.fetch_index_kline("000300", days=60)
    assert not kline.empty and not benchmark.empty
    for col in ("日期", "开盘", "收盘", "最高", "最低"):
        assert col in kline.columns, f"AKShare 列名漂移: 缺 {col}"

    # 决策日 = 倒数第 10 个交易日的上一日;stop/target 设在远离现价处必走 expired
    decision_date = str(kline.iloc[-11]["日期"])
    last_close = float(kline.iloc[-11]["收盘"])
    decision = {
        "decision_id": "live",
        "action": "buy",
        "entry_price": last_close,
        "stop_loss": last_close * 0.5,
        "target_price": last_close * 2.0,
        "timestamp": f"{decision_date}T15:00:00",
    }
    result = evaluate_decision(decision, kline, benchmark, max_hold_days=20)
    # 10 行 < 20:可能 None(行不足)或 expired(不会 hit);两类都合法,但类型必须正确
    if result is not None:
        assert result.status in ("hit_stop", "hit_target", "expired")
        assert isinstance(result.hold_days, int) and result.hold_days >= 1
        assert result.benchmark_return is not None  # 基准给了就不应为 None


def test_live_fetch_index_kline_columns():
    """真实指数 K 线列名(防 index_zh_a_hist 漂移)。"""
    from finance_agent.data.akshare_client import AKShareClient

    df = AKShareClient().fetch_index_kline("000300", days=10)
    assert not df.empty
    assert "日期" in df.columns and "收盘" in df.columns
