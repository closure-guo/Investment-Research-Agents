"""拥挤度日批 job 单测（delta add-crowding-regime-positioning T2；先红后绿）。

契约（计划 Task 4 Interfaces + design「市值兜底源（腾讯批量）」修订）：

- ``run_regime_daily(as_of=None, *, db_path=None, snapshot_db_path=None, client=None)``；
- 顺序：``latest_snapshot_date`` → ``read_universe(as_of)``（校验新鲜度）→ 拉 spot；
- 快照不可用（库为空/当日无行）→ ``no_snapshot_data``；``as_of > latest`` →
  ``as_of_not_fresh``（独立原因），均不产行（MUST NOT 以 0 值冒充）；
- 市值链：spot ``流通市值`` 列优先 → 缺失时腾讯批量兜底（``cap_source=tencent``）
  → 两源皆无 / caps 对参与集覆盖不全 → ``cap_data_missing``，不产行
  （MUST NOT 以成交额等替代口径）；
- 正常：compute_indicators → percentile_rank（窗口 = 历史行 + 当日记 250 交易日）→
  upsert_regime_daily → ``{"produced": True, "trade_date": ...}``；
- 同日重跑幂等（仍单行，值取最新一次）；分位样本 < 60 → null 如实落库。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.outcome.regime.job import run_regime_daily
from finance_agent.outcome.regime.model import (
    latest_regime,
    list_regime_history,
    upsert_regime_daily,
)

_AS_OF = "2026-10-16"

_INDICATORS = {
    "small_cap_share": 0.1,
    "up_count": 1,
    "down_count": 1,
    "flat_count": 0,
    "limit_up_count": 0,
    "limit_down_count": 0,
    "total_amount": 300.0,
}


class _FakeClient:
    """job 消费的取数面：``fetch_market_spot``（同源 spot）+ ``fetch_market_caps_bulk``
    （腾讯批量兜底）。"""

    def __init__(self, spot: pd.DataFrame | None, bulk: dict[str, float] | None = None) -> None:
        self._spot = spot
        self._bulk = bulk
        self.calls = 0
        self.bulk_calls = 0
        self.bulk_codes: list[str] = []

    def fetch_market_spot(self) -> pd.DataFrame | None:
        self.calls += 1
        return self._spot

    def fetch_market_caps_bulk(self, codes) -> dict[str, float] | None:
        self.bulk_calls += 1
        self.bulk_codes = list(codes)
        return self._bulk


def _spot(*, caps: dict[str, float], include_cap_column: bool = True) -> pd.DataFrame:
    payload: dict[str, list] = {"代码": list(caps)}
    if include_cap_column:
        payload["流通市值"] = list(caps.values())
    return pd.DataFrame(payload)


def _snapshot_rows(*, amounts: dict[str, float] | None = None) -> list[dict]:
    amounts = amounts or {"600001": 10.0, "600002": 20.0, "600003": 30.0}
    return [
        {
            "trade_date": _AS_OF,
            "code": code,
            "amount": amount,
            "pct_chg": 1.0,
            "is_suspended": 0,
            "limit_up_sealed": 0,
            "limit_down_sealed": 0,
        }
        for code, amount in amounts.items()
    ]


def _dbs(tmp_path):
    return tmp_path / "regime.db", tmp_path / "snapshot.db"


class TestHappyPath:
    def test_produces_row_with_indicators(self, tmp_path):
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        summary = run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        assert summary == {"produced": True, "trade_date": _AS_OF}
        assert client.calls == 1
        row = latest_regime(db_path=regime_db)
        assert row is not None
        assert row["trade_date"] == _AS_OF
        assert row["total_amount"] == pytest.approx(60.0)
        # 组 = ceil(3 × 0.3) = 1 → cap 最小的 600001：10 / 60
        assert row["small_cap_share"] == pytest.approx(10.0 / 60.0)
        assert (row["up_count"], row["down_count"], row["flat_count"]) == (3, 0, 0)
        assert row["computed_at"]  # 审计时间戳非空
        # 同源 spot 生效 → cap_source 留痕 spot；不触发兜底源
        assert row["cap_source"] == "spot"
        assert client.bulk_calls == 0

    def test_tencent_fallback_records_cap_source(self, tmp_path):
        """spot 无流通市值列（新浪回退列形）+ 腾讯批量可用 → 产行 + ``cap_source=tencent``。"""
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(
            _spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}, include_cap_column=False),
            bulk={"600001": 1.0, "600002": 2.0, "600003": 3.0},
        )

        summary = run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        assert summary == {"produced": True, "trade_date": _AS_OF}
        row = latest_regime(db_path=regime_db)
        assert row is not None
        assert row["cap_source"] == "tencent"
        # 兜底源市值口径与 spot 同（真实市值字段）→ 指标与 spot 路径一致
        assert row["small_cap_share"] == pytest.approx(10.0 / 60.0)
        assert row["total_amount"] == pytest.approx(60.0)
        assert client.bulk_calls == 1
        assert client.bulk_codes == ["600001", "600002", "600003"]  # 兜底按当日在册代码批量取

    def test_as_of_explicit_selects_that_date(self, tmp_path):
        regime_db, snap_db = _dbs(tmp_path)
        earlier = (date.fromisoformat(_AS_OF) - timedelta(days=1)).isoformat()
        write_snapshot_rows(earlier, _snapshot_rows(), db_path=snap_db)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        summary = run_regime_daily(
            earlier, db_path=regime_db, snapshot_db_path=snap_db, client=client
        )

        assert summary == {"produced": True, "trade_date": earlier}
        assert latest_regime(db_path=regime_db)["trade_date"] == earlier

    def test_rerun_same_day_is_idempotent(self, tmp_path):
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)
        run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        rows = list_regime_history(db_path=regime_db)
        assert len(rows) == 1
        assert rows[0]["trade_date"] == _AS_OF


class TestSkipPaths:
    def test_empty_snapshot_db_returns_no_snapshot_data(self, tmp_path):
        regime_db, snap_db = _dbs(tmp_path)
        client = _FakeClient(_spot(caps={"600001": 1.0}))

        summary = run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        assert summary == {"produced": False, "skipped_reason": "no_snapshot_data"}
        assert latest_regime(db_path=regime_db) is None
        assert client.calls == 0  # 快照都没有，不拉行情

    def test_missing_rows_for_date_before_latest_returns_no_snapshot_data(self, tmp_path):
        """as_of ≤ 最新快照日但当日无行 → ``no_snapshot_data``（与不新鲜区分）。"""
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows("2026-10-15", _snapshot_rows(), db_path=snap_db)
        write_snapshot_rows("2026-10-17", _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(_spot(caps={"600001": 1.0}))

        summary = run_regime_daily(
            _AS_OF, db_path=regime_db, snapshot_db_path=snap_db, client=client
        )

        assert summary == {"produced": False, "skipped_reason": "no_snapshot_data"}
        assert latest_regime(db_path=regime_db) is None
        assert client.calls == 0  # 当日无快照行，不拉行情

    def test_as_of_after_latest_returns_as_of_not_fresh(self, tmp_path):
        """``as_of > latest`` → 独立原因 ``as_of_not_fresh``（不冒充 no_snapshot_data）。"""
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows("2026-10-15", _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(_spot(caps={"600001": 1.0}))

        summary = run_regime_daily(
            _AS_OF, db_path=regime_db, snapshot_db_path=snap_db, client=client
        )

        assert summary == {
            "produced": False,
            "skipped_reason": "as_of_not_fresh",
            "trade_date": _AS_OF,
        }
        assert latest_regime(db_path=regime_db) is None
        # 校验顺序：快照新鲜度不过 → 不拉 spot（兜底/行情零请求）
        assert client.calls == 0
        assert client.bulk_calls == 0

    def test_spot_fetch_failure_returns_cap_data_missing(self, tmp_path):
        """spot 与腾讯批量双源皆不可用 → ``cap_data_missing``（不产行）。"""
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(None)

        summary = run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        assert summary == {
            "produced": False,
            "skipped_reason": "cap_data_missing",
            "trade_date": _AS_OF,
        }
        assert latest_regime(db_path=regime_db) is None
        assert client.bulk_calls == 1  # spot 缺失 → 兜底源试过一次

    def test_spot_without_circulating_cap_column_returns_cap_data_missing(self, tmp_path):
        """新浪回退列形（无 流通市值）且腾讯批量不可用 → 不产行（不以成交额替代市值分组）。"""
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(
            _spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}, include_cap_column=False)
        )

        summary = run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        assert summary["produced"] is False
        assert summary["skipped_reason"] == "cap_data_missing"
        assert latest_regime(db_path=regime_db) is None
        assert client.bulk_calls == 1

    def test_incomplete_caps_returns_cap_data_missing(self, tmp_path):
        """caps 对参与集覆盖不全 → 不产行（不缩样本冒充；spot 在列即不触发兜底）。"""
        regime_db, snap_db = _dbs(tmp_path)
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0}))

        summary = run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

        assert summary["skipped_reason"] == "cap_data_missing"
        assert latest_regime(db_path=regime_db) is None
        assert client.bulk_calls == 0  # 兜底链触发条件 = spot caps 缺失，不含覆盖不全


class TestPercentiles:
    def _seed_history(self, regime_db, count: int) -> None:
        """以 as_of 之前 count 个自然日造历史行（值固定便于手算）。"""
        base = date.fromisoformat(_AS_OF)
        for offset in range(count, 0, -1):
            upsert_regime_daily(
                (base - timedelta(days=offset)).isoformat(),
                {**_INDICATORS, "small_cap_share": 0.1, "total_amount": 100.0},
                {"small_cap_share_pctile": None, "total_amount_pctile": None},
                computed_at="2026-01-01T00:00:00",
                db_path=regime_db,
            )

    def _run(self, regime_db, snap_db, client):
        write_snapshot_rows(_AS_OF, _snapshot_rows(), db_path=snap_db)
        return run_regime_daily(db_path=regime_db, snapshot_db_path=snap_db, client=client)

    def test_window_includes_today_and_default_min_history_is_60(self, tmp_path):
        """59 条历史 + 当日 = 60 样本 → 分位非 null（手算 ``#{x≤v}/n``）。

        历史 small_cap_share=0.1（59 条）、当日 10/60=0.1667 → 60/60 = 1.0；
        历史 total_amount=100（59 条）、当日 60 → 1/60（仅当日 ≤ 60）。
        """
        regime_db, snap_db = _dbs(tmp_path)
        self._seed_history(regime_db, 59)
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        summary = self._run(regime_db, snap_db, client)

        assert summary["produced"] is True
        row = latest_regime(db_path=regime_db)
        assert row["small_cap_share_pctile"] == pytest.approx(1.0)
        assert row["total_amount_pctile"] == pytest.approx(1.0 / 60.0)

    def test_same_day_row_not_double_counted_in_window(self, tmp_path):
        """同日重跑：当日已有行不计入历史段（窗口大小不因重跑变大）。

        造 58 条历史 + 1 条当日行（模拟上一轮同日产行）→ 重跑后窗口 = 58 + 当日 = 59
        < 60 → 分位 null；若实现把当日已有行当历史（59 + 当日 = 60）则为非 null。
        """
        regime_db, snap_db = _dbs(tmp_path)
        self._seed_history(regime_db, 58)
        upsert_regime_daily(
            _AS_OF,
            {**_INDICATORS, "small_cap_share": 0.1, "total_amount": 100.0},
            {"small_cap_share_pctile": None, "total_amount_pctile": None},
            computed_at="2026-01-01T00:00:00",
            db_path=regime_db,
        )
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        self._run(regime_db, snap_db, client)

        row = latest_regime(db_path=regime_db)
        assert row is not None
        assert row["small_cap_share_pctile"] is None
        assert row["total_amount_pctile"] is None
        assert len(list_regime_history(db_path=regime_db)) == 59  # 仍单日单行

    def test_58_history_rows_below_threshold_yield_null_pctiles(self, tmp_path):
        """58 条历史 + 当日 = 59 样本 < 60 → 分位 null（insufficient_history）。"""
        regime_db, snap_db = _dbs(tmp_path)
        self._seed_history(regime_db, 58)
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        self._run(regime_db, snap_db, client)

        row = latest_regime(db_path=regime_db)
        assert row["small_cap_share_pctile"] is None
        assert row["total_amount_pctile"] is None

    def test_custom_quantile_from_config_is_honored(self, tmp_path, monkeypatch):
        """``config/regime.json`` 的 small_cap_quantile 生效：1.0 → 全体入组 → 占比 1.0。"""
        import json

        regime_db, snap_db = _dbs(tmp_path)
        config_path = tmp_path / "regime-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "thresholds": {"elevated": 0.8, "danger": 0.9},
                    "multipliers": {"normal": 1.0, "elevated": 0.5, "danger": 0.0},
                    "small_cap_quantile": 1.0,
                    "history_window": 250,
                    "min_history": 60,
                    "stale_days": 7,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("REGIME_CONFIG_PATH", str(config_path))
        client = _FakeClient(_spot(caps={"600001": 1.0, "600002": 2.0, "600003": 3.0}))

        self._run(regime_db, snap_db, client)

        assert latest_regime(db_path=regime_db)["small_cap_share"] == pytest.approx(1.0)
