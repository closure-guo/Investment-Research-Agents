"""事件线日批薄封装测试（delta add-event-driven-lane T6）。

三个 job：``run_earnings_fetch_daily``（抓取，失败留痕不冒充成功）/
``run_earnings_triage_daily``（分诊 + 清单）/ ``run_event_settle_daily``（记账 + 结算）。
零网络：AKShare 与 LLM 网关均以可控制桩替换。
"""

from __future__ import annotations

import pandas as pd
import pytest

from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.outcome.events import job as events_job
from finance_agent.outcome.events.model import (
    list_settled_journal,
    list_watchlist,
)

_CODE = "600519"


@pytest.fixture(autouse=True)
def _events_env(tmp_path, monkeypatch):
    """事件库 + 快照库路径指向 tmp（调用期读 env）。"""
    monkeypatch.setenv("EVENTS_DB_PATH", str(tmp_path / "events.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snap.db"))
    monkeypatch.delenv("EVENTS_FETCH_PERIODS", raising=False)
    monkeypatch.delenv("EVENTS_HORIZON_DAYS", raising=False)
    return tmp_path


def _forecast_row(announce: str = "2025-04-15", *, code: str = _CODE) -> dict:
    return {
        "code": code,
        "name": "贵州茅台",
        "report_period": "2025-03-31",
        "source_record_id": f"归属于上市公司股东的净利润|{announce}",
        "forecast_type": "预增",
        "change_low": 60.0,
        "change_high": 90.0,
        "announce_date": announce,
        "available_at": announce,
        "source": "eastmoney",
    }


class _StubFetchClient:
    """抓取桩：按报告期返回行；``fail_periods`` 内的期记 ``none`` 源（失败留痕口径）。"""

    def __init__(self, rows_by_period: dict[str, list[dict]], fail_periods: set[str] | None = None):
        self.rows_by_period = rows_by_period
        self.fail_periods = fail_periods or set()
        self.sources_seen = {"earnings_forecast": set()}
        self.periods_requested: list[str | None] = []

    def fetch_earnings_forecasts(self, report_period=None):
        self.periods_requested.append(report_period)
        if report_period in self.fail_periods:
            self.sources_seen["earnings_forecast"].add("none")
            return []
        self.sources_seen["earnings_forecast"].add("eastmoney")
        return list(self.rows_by_period.get(report_period or "", []))


class TestFetchDaily:
    def test_fetches_recent_periods_and_upserts(self, monkeypatch) -> None:
        stub = _StubFetchClient({"2026-06-30": [_forecast_row("2026-07-10")], "2026-09-30": []})
        monkeypatch.setattr(events_job, "AKShareClient", lambda: stub)
        monkeypatch.setattr(events_job, "_default_report_period", lambda today=None: "2026-09-30")
        monkeypatch.setenv("EVENTS_FETCH_PERIODS", "2")

        summary = events_job.run_earnings_fetch_daily()

        assert stub.periods_requested == ["2026-06-30", "2026-09-30"]
        assert summary["fetched"] == 1 and summary["upserted"] == 1
        assert summary["periods"] == ["2026-06-30", "2026-09-30"]

    def test_failure_raises_and_does_not_masquerade_success(self, monkeypatch) -> None:
        """数据源失败 → 上抛（ops 运行历史记 failed + 原因），MUST NOT 以零条新增冒充成功。"""
        stub = _StubFetchClient({}, fail_periods={"2026-09-30"})
        monkeypatch.setattr(events_job, "AKShareClient", lambda: stub)
        monkeypatch.setattr(events_job, "_default_report_period", lambda today=None: "2026-09-30")
        monkeypatch.setenv("EVENTS_FETCH_PERIODS", "1")

        with pytest.raises(RuntimeError) as excinfo:
            events_job.run_earnings_fetch_daily()
        assert "业绩预告抓取失败" in str(excinfo.value)

    def test_partial_failure_persists_successes_then_raises(self, monkeypatch, _events_env) -> None:
        """部分期失败：成功期先落库（不丢已成功数据），再上抛并带 upserted/失败期列表。"""
        from finance_agent.outcome.events.model import list_untriaged

        stub = _StubFetchClient(
            {
                "2025-12-31": [_forecast_row("2026-01-10", code="600519")],
                "2026-06-30": [_forecast_row("2026-07-10", code="000001")],
                "2026-09-30": [_forecast_row("2026-10-10", code="600000")],
            },
            fail_periods={"2026-03-31"},
        )
        monkeypatch.setattr(events_job, "AKShareClient", lambda: stub)
        monkeypatch.setattr(events_job, "_default_report_period", lambda today=None: "2026-09-30")
        monkeypatch.setenv("EVENTS_FETCH_PERIODS", "4")

        with pytest.raises(RuntimeError) as excinfo:
            events_job.run_earnings_fetch_daily()

        message = str(excinfo.value)
        assert "2026-03-31" in message  # 失败期列表
        assert "upserted=3" in message  # 已成功落库行数
        stored = list_untriaged(db_path=_events_env / "events.db")
        assert {row["code"] for row in stored} == {"600519", "000001", "600000"}


class _StubTriageClient:
    model_label = "job-stub"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt, *, purpose="quick", metadata=None):
        self.calls += 1
        return {
            "verdict": "outperform",
            "reason": "预增 60%~90%",
            "evidence": [{"field": "change_low", "value": 60.0}],
        }


class _PromptCapturingTriageClient:
    """分诊桩：记录 ``(code, prompt)``，恒返回 outperform（快照注入断言用）。"""

    model_label = "job-stub"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete_json(self, prompt, *, purpose="quick", metadata=None):
        self.calls.append({"prompt": prompt, "metadata": metadata or {}})
        return {
            "verdict": "outperform",
            "reason": "预增 60%~90%",
            "evidence": [{"field": "change_low", "value": 60.0}],
        }


class TestTriageDaily:
    def test_triages_untriaged_and_builds_watchlist(self, monkeypatch, _events_env) -> None:
        from finance_agent.outcome.events.model import upsert_forecasts

        upsert_forecasts([_forecast_row("2025-04-15")], db_path=str(_events_env / "events.db"))
        stub = _StubTriageClient()
        monkeypatch.setattr(events_job, "GatewayTriageClient", lambda: stub)

        summary = events_job.run_earnings_triage_daily()

        assert summary["triaged"] == 1 and summary["outperform"] == 1
        assert summary["watchlist_added"] == 1
        assert len(list_watchlist(db_path=_events_env / "events.db")) == 1
        assert stub.calls == 1

    def test_empty_queue_is_zero_work(self, monkeypatch) -> None:
        stub = _StubTriageClient()
        monkeypatch.setattr(events_job, "GatewayTriageClient", lambda: stub)
        summary = events_job.run_earnings_triage_daily()
        assert summary["triaged"] == 0 and summary["watchlist_added"] == 0
        assert summary["snapshot_ctx_missing"] == 0
        assert stub.calls == 0  # 无未分诊记录 → 零 LLM 调用

    def test_snapshot_context_is_point_in_time_per_row(self, monkeypatch, _events_env) -> None:
        """逐行时点化：每行取 ≤ available_at 的最近快照日该股字段（消除回填前视）。

        快照库仅覆盖近 N 日：旧公告行 prompt 不含快照字段（该日无覆盖），
        ``available_at == 快照日`` 的行含；更晚的快照日 MUST NOT 被前视取用。
        """
        from finance_agent.outcome.events.model import upsert_forecasts

        db = str(_events_env / "events.db")
        old = _forecast_row("2024-01-05", code="600519")
        fresh = _forecast_row("2025-04-15", code="000001")
        upsert_forecasts([old, fresh], db_path=db)
        write_snapshot_rows(
            "2025-04-15",
            [{"code": "000001", "close_raw": 10.5, "pct_chg": 2.0, "amount": 123456.0}],
            db_path=str(_events_env / "snap.db"),
        )
        write_snapshot_rows(
            "2025-04-20",
            [{"code": "600519", "close_raw": 999.0, "pct_chg": 9.9, "amount": 888.0}],
            db_path=str(_events_env / "snap.db"),
        )
        stub = _PromptCapturingTriageClient()
        monkeypatch.setattr(events_job, "GatewayTriageClient", lambda: stub)

        summary = events_job.run_earnings_triage_daily(as_of="2025-04-21")

        prompts = {call["metadata"]["code"]: call["prompt"] for call in stub.calls}
        assert set(prompts) == {"600519", "000001"}
        assert "- close: 10.5" in prompts["000001"]  # available_at == 快照日 → 注入该日字段
        assert "- close:" not in prompts["600519"]  # 无 ≤ available_at 的快照覆盖 → 不注入
        assert "999" not in prompts["600519"]  # MUST NOT 前视取未来快照
        assert summary["snapshot_ctx_missing"] == 1

    def test_snapshot_absent_degrades_to_forecast_only(self, monkeypatch, _events_env) -> None:
        """无快照覆盖退化路径：不注入快照字段，照常分诊并在批次摘要计数留痕。"""
        from finance_agent.outcome.events.model import upsert_forecasts

        upsert_forecasts([_forecast_row("2025-04-15")], db_path=str(_events_env / "events.db"))
        stub = _PromptCapturingTriageClient()
        monkeypatch.setattr(events_job, "GatewayTriageClient", lambda: stub)

        summary = events_job.run_earnings_triage_daily(as_of="2025-04-16")

        assert summary["triaged"] == 1 and summary["watchlist_added"] == 1
        assert summary["snapshot_ctx_missing"] == 1
        assert "- close:" not in stub.calls[0]["prompt"]


class TestSettleDaily:
    def _kline(self, days: list[tuple[str, float, float]]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"日期": day, "开盘": open_, "收盘": close, "最高": close + 1, "最低": open_ - 1}
                for day, open_, close in days
            ]
        )

    def test_entries_and_settles_with_snapshot_calendar(self, monkeypatch, _events_env) -> None:
        from finance_agent.outcome.events.model import forecast_key, record_triage, upsert_forecasts

        db = str(_events_env / "events.db")
        row = _forecast_row("2025-04-15")
        upsert_forecasts([row], db_path=db)
        record_triage(
            forecast_key(row),
            verdict="outperform",
            reason="预增",
            evidence=[{"field": "forecast_type", "value": "预增"}],
            model="m",
            prompt_fingerprint="event_triage@1",
            db_path=db,
        )
        calendar = [
            "2025-04-16",
            "2025-04-17",
            "2025-04-18",
            "2025-04-21",
            "2025-04-22",
            "2025-04-23",
        ]
        write_snapshot_rows(
            "2025-04-16",
            [{"code": _CODE, "is_suspended": 0, "limit_up_sealed": 0, "limit_down_sealed": 0}],
            db_path=str(_events_env / "snap.db"),
        )
        closes = {day: 100.0 + index for index, day in enumerate(calendar)}

        class _Client:
            def __init__(self) -> None:
                self.sources_seen: dict[str, set[str]] = {}

            def fetch_kline(self, code, days=250, *, adjust="qfq"):
                return self._frame

            _frame = self._kline([(day, 99.0 + i, closes[day]) for i, day in enumerate(calendar)])

        monkeypatch.setattr(events_job, "AKShareClient", _Client)
        monkeypatch.setattr(events_job, "read_snapshot_calendar", lambda *a, **k: calendar)

        summary = events_job.run_event_settle_daily()

        assert summary["entry"]["entered"] == 1
        assert summary["settle"]["settled"] == 1
        settled = list_settled_journal(db_path=db)
        assert settled[0]["settle_date"] == "2025-04-23"
        assert settled[0]["raw_return"] == pytest.approx(round(closes["2025-04-23"] / 99.0 - 1, 6))

    def test_horizon_env_configurable(self, monkeypatch, _events_env) -> None:
        from finance_agent.outcome.events.model import forecast_key, record_triage, upsert_forecasts

        db = str(_events_env / "events.db")
        row = _forecast_row("2025-04-15")
        upsert_forecasts([row], db_path=db)
        record_triage(
            forecast_key(row),
            verdict="outperform",
            reason="预增",
            evidence=[{"field": "forecast_type", "value": "预增"}],
            model="m",
            prompt_fingerprint="event_triage@1",
            db_path=db,
        )
        write_snapshot_rows(
            "2025-04-16",
            [{"code": _CODE, "is_suspended": 0, "limit_up_sealed": 0, "limit_down_sealed": 0}],
            db_path=str(_events_env / "snap.db"),
        )
        calendar = [
            "2025-04-16",
            "2025-04-17",
            "2025-04-18",
            "2025-04-21",
            "2025-04-22",
            "2025-04-23",
        ]

        class _Client:
            def __init__(self) -> None:
                self.sources_seen: dict[str, set[str]] = {}

            def fetch_kline(self, code, days=250, *, adjust="qfq"):
                return pd.DataFrame(
                    [
                        {"日期": day, "开盘": 100.0, "收盘": 101.0, "最高": 102.0, "最低": 99.0}
                        for day in calendar
                    ]
                )

        monkeypatch.setattr(events_job, "AKShareClient", _Client)
        monkeypatch.setattr(events_job, "read_snapshot_calendar", lambda *a, **k: calendar)
        monkeypatch.setenv("EVENTS_HORIZON_DAYS", "3")

        events_job.run_event_settle_daily()

        # horizon 3 → 当轮即到期结算（04-16 入场后第 3 个交易日 = 04-21）；记账行记 horizon=3
        settled = list_settled_journal(db_path=db)
        assert settled[0]["horizon_days"] == 3
        assert settled[0]["settle_date"] == "2025-04-21"
