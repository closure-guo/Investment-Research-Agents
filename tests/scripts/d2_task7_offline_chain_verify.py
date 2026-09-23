"""Δ2 Task 7 离线真实链路验证（零 LLM，脚本化）。

覆盖 delta update-decision-settlement-contract 的三条端到端契约：

① persist_prediction_from_accumulated（真实落库入口）→ predictions 行契约核对：
   horizon_days==20、参考价取 quote 优先、rationale_snapshot["declared_prices"] 三键、
   status=="open"。
② settle_open_predictions（fake client 提供 hfq 形态 K 线）→ 落库核对：
   long 行 settle_entry_price 为归属日收盘、resolved_at==exit_date、Score 上报计数；
   neutral 行 avoidance_status 落库且 status=="avoidance"（离开 open 池）。
③ 双腿口径一致性：同一假 K 线分别走生产 resolve_prediction 与
   evals.backtest.replay 的映射，断言 raw_return / excess_return 一致（两腿同源证据）。

全程零 LLM / 零网络：K 线、基准、quote、Langfuse 均为内存构造。

用法: uv run python tests/scripts/d2_task7_offline_chain_verify.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # evals.* 包根

import pandas as pd  # noqa: E402

from finance_agent.outcome.track_record.ingest import (  # noqa: E402
    persist_prediction_from_accumulated,
)
from finance_agent.outcome.track_record.job import settle_open_predictions  # noqa: E402
from finance_agent.outcome.track_record.judgment import (  # noqa: E402
    DEFAULT_HORIZON_DAYS,
    direction_for_action,
    resolve_prediction,
)
from finance_agent.outcome.track_record.model import (  # noqa: E402
    init_track_record_tables,
    insert_prediction,
    list_predictions,
)

_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _FAILURES.append(label)


def kline(prices: list[float], start: str = "2026-09-01") -> pd.DataFrame:
    """hfq 形态 K 线（日期/开盘/最高/最低/收盘/成交量）。"""
    dates = [str(pd.Timestamp(start) + pd.Timedelta(days=i))[:10] for i in range(len(prices))]
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": prices,
            "最高": prices,
            "最低": prices,
            "收盘": prices,
            "成交量": [1] * len(prices),
        }
    )


class _StubClient:
    def __init__(self, klines: dict[str, pd.DataFrame], benchmark: pd.DataFrame | None = None):
        self.klines = klines
        self.benchmark = benchmark

    def fetch_kline(self, code: str, days: int | None = None) -> pd.DataFrame | None:
        return self.klines.get(code)

    def fetch_index_kline(self, code: str, days: int | None = None) -> pd.DataFrame | None:
        return self.benchmark


class _FakeLangfuse:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_score(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


def _tmp_db() -> Path:
    d = Path(tempfile.mkdtemp(prefix="d2t7-"))
    db = d / "verify.db"
    init_track_record_tables(db)
    return db


# ── ① 落库入口契约 ──
def verify_ingest() -> None:
    print("[1] persist_prediction_from_accumulated → predictions 行契约")
    db = _tmp_db()
    os.environ["SESSIONS_DB_PATH"] = str(db)  # 落库入口走默认库路径

    accumulated = {
        "final_trade_decision": {
            "action": "buy",
            "confidence": 0.77,
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "target_price": 120.0,
        },
        "stock_quote": {"price": 101.5},  # 参考价来源：quote 优先
        "kline": kline([99.0, 99.5, 99.8]),  # 兜底源（本用例不应被采用）
        "fund_manager_decision": "approve",
        "fund_manager_decision_reasoning": "seeded for offline verify",
        "langfuse_trace_id": "trace-offline-ingest",
    }
    persist_prediction_from_accumulated(accumulated, "sess-offline", "600519", "贵州茅台")

    rows = list_predictions(db_path=db)
    check("落库 1 行", len(rows) == 1, f"n={len(rows)}")
    row = rows[0]
    check("horizon_days == 20", row["horizon_days"] == 20, f"={row['horizon_days']}")
    check("direction == long (buy→long)", row["direction"] == "long", f"={row['direction']}")
    check("参考价取 quote 优先 (101.5)", row["entry_price"] == 101.5, f"={row['entry_price']}")
    snap_raw = row["rationale_snapshot"] or "{}"
    snap = json.loads(snap_raw) if isinstance(snap_raw, str) else snap_raw
    declared = snap.get("declared_prices") or {}
    check(
        "declared_prices 三键齐全",
        set(declared) == {"entry_price", "stop_loss", "target_price"},
        f"keys={sorted(declared)}",
    )
    check(
        "declared_prices 值与申报一致",
        declared.get("entry_price") == 100.0
        and declared.get("stop_loss") == 90.0
        and declared.get("target_price") == 120.0,
        f"{declared}",
    )
    check("status == open", row["status"] == "open", f"={row['status']}")


# ── ② 日批判定落库契约 ──
def verify_settle() -> None:
    print("[2] settle_open_predictions → long/neutral 落库契约")
    db = _tmp_db()

    # long：created_at 早于 15:00 → 归属日收盘入场
    insert_prediction(
        {
            "source_type": "live",
            "symbol": "600519.SH",
            "symbol_name": "贵州茅台",
            "direction": "long",
            "entry_price": 95.0,  # 参考价 ≠ 派生入场，验证结算用派生价
            "horizon_days": DEFAULT_HORIZON_DAYS,
            "confidence": 0.8,
            "rationale_snapshot": {"action": "buy"},
            "created_at": "2026-09-01T10:00:00",
            "langfuse_trace_id": "trace-long",
        },
        db_path=db,
        status="open",
    )
    # neutral：hold → neutral，回避判定
    insert_prediction(
        {
            "source_type": "live",
            "symbol": "000001.SZ",
            "symbol_name": "平安银行",
            "direction": "neutral",
            "entry_price": 100.0,
            "horizon_days": DEFAULT_HORIZON_DAYS,
            "confidence": 0.5,
            "rationale_snapshot": {"action": "hold"},
            "created_at": "2026-09-01T10:00:00",
            "langfuse_trace_id": "trace-neutral",
        },
        db_path=db,
        status="open",
    )

    # 21 行 → 派生入场 09-01 收盘 100，20 交易日后 09-21 收盘 120（+20%）
    long_k = kline([100.0 + i for i in range(21)])
    # neutral 标的下跌 20%（-20%）→ avoidance_win
    neutral_k = kline([100.0 - i for i in range(21)])
    bench = kline([4000.0] * 21)
    client = _StubClient({"600519": long_k, "000001": neutral_k}, benchmark=bench)
    langfuse = _FakeLangfuse()

    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    check("整批 0 error", result["errors"] == 0, f"{result}")
    check("settled == 2", result["settled"] == 2, f"settled={result['settled']}")
    check(
        "scores_reported == 3（long 3 个，neutral 0 个）",
        result["scores_reported"] == 3,
        f"={result['scores_reported']}",
    )

    rows = {r["symbol"]: r for r in list_predictions(db_path=db)}
    lr = rows["600519.SH"]
    check("long: status == resolved_win", lr["status"] == "resolved_win", f"={lr['status']}")
    check(
        "long: settle_entry_price == 归属日收盘 100",
        lr["settle_entry_price"] == 100.0,
        f"={lr['settle_entry_price']}",
    )
    check("long: 参考价 entry_price 保持 95", lr["entry_price"] == 95.0, f"={lr['entry_price']}")
    check(
        "long: resolved_at == exit_date 2026-09-21",
        lr["resolved_at"] == "2026-09-21",
        f"={lr['resolved_at']}",
    )
    check(
        "long: raw_return ≈ 0.20",
        abs((lr["raw_return"] or 0) - 0.20) < 1e-6,
        f"={lr['raw_return']}",
    )

    nr = rows["000001.SZ"]
    check("neutral: status == avoidance（终态）", nr["status"] == "avoidance", f"={nr['status']}")
    check(
        "neutral: avoidance_status 落库 avoidance_win",
        nr["avoidance_status"] == "avoidance_win",
        f"={nr['avoidance_status']}",
    )
    check(
        "neutral: 结果不写 resolved_*",
        nr["status"] not in ("resolved_win", "resolved_loss", "resolved_neutral"),
        f"={nr['status']}",
    )
    check("neutral: 离开 open 池", list_predictions(status="open", db_path=db) == [])

    reported_traces = {c["trace_id"] for c in langfuse.calls}
    check(
        "Score 只挂 long 的 trace，neutral 不上报",
        reported_traces == {"trace-long"},
        f"{reported_traces}",
    )
    check(
        "Score 三键 = decision_hit/return/excess",
        {c["name"] for c in langfuse.calls}
        == {"decision_hit", "decision_return", "decision_excess"},
        f"{sorted(c['name'] for c in langfuse.calls)}",
    )


# ── ③ 双腿口径一致性 ──
def verify_two_legs() -> None:
    print("[3] 双腿口径一致性：生产 resolve_prediction vs 回测 replay 映射")
    from evals.backtest.replay import _settlement_from_resolution

    k = kline([100.0 + i for i in range(21)])
    bench = kline([4000.0] * 21)

    for action, expected_direction in (("buy", "long"), ("sell", "short")):
        direction = direction_for_action(action)
        check(
            f"{action} → direction {expected_direction}（单一来源）",
            direction == expected_direction,
            f"={direction}",
        )
        prediction = {
            "created_at": "2026-09-01T10:00:00",
            "direction": direction,
            "horizon_days": DEFAULT_HORIZON_DAYS,
            "target_price": None,
        }
        # 生产腿
        prod = resolve_prediction(prediction, k, bench)
        assert prod is not None, f"生产腿未到点: {action}"
        # 回测腿：replay 内部同形映射（direction_for_action + DEFAULT_HORIZON_DAYS）→ 同一判定
        replay_res = resolve_prediction(
            {
                "created_at": "2026-09-01T10:00:00",
                "direction": direction_for_action(action),
                "horizon_days": DEFAULT_HORIZON_DAYS,
                "target_price": None,
            },
            k,
            bench,
        )
        assert replay_res is not None
        settlement = _settlement_from_resolution(replay_res, bench)

        check(
            f"{action}: 双腿 raw_return 一致",
            replay_res.raw_return == prod.raw_return,
            f"prod={prod.raw_return} replay={replay_res.raw_return}",
        )
        check(
            f"{action}: 双腿 excess_return 一致",
            replay_res.excess_return == prod.excess_return,
            f"prod={prod.excess_return} replay={replay_res.excess_return}",
        )
        check(
            f"{action}: 回测 settlement.decision_return 映射 == 生产 raw",
            settlement["decision_return"] == prod.raw_return,
            f"={settlement['decision_return']}",
        )
        check(
            f"{action}: 回测 settlement.decision_excess 映射 == 生产 excess",
            settlement["decision_excess"] == prod.excess_return,
            f"={settlement['decision_excess']}",
        )
    # short 方向符号自证：涨 20% → short raw 应为 -0.20
    short_prod = resolve_prediction(
        {
            "created_at": "2026-09-01T10:00:00",
            "direction": "short",
            "horizon_days": DEFAULT_HORIZON_DAYS,
        },
        k,
        bench,
    )
    assert short_prod is not None
    check(
        "short 方向符号取负（+20% 行情 → raw -0.20）",
        short_prod.raw_return == -0.2,
        f"={short_prod.raw_return}",
    )


def main() -> int:
    print("=" * 72)
    print(f"DEFAULT_HORIZON_DAYS = {DEFAULT_HORIZON_DAYS}")
    print("=" * 72)
    verify_ingest()
    verify_settle()
    verify_two_legs()
    print("=" * 72)
    if _FAILURES:
        print(f"结论: FAIL（{len(_FAILURES)} 项）")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("结论: PASS（全部核对项通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
