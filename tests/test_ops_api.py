"""Task 3(delta add-eval-ops-console):``/api/v1/ops`` 端点族。

覆盖面:
1. 五任务状态与运行历史——含定时重试「每次尝试各一行」时的 ``last_run`` 取最新行、
   进程重启遗留悬挂 ``running`` 行的清扫、cohort 行与 ``cohort`` 块时刻一致;
2. 手动补跑——202 + run_id;未知任务 404;锁占用/开关关闭 409;**任务失败非 2xx**
   （失败不伪装成 202 成功,原因随响应与运行历史双通道可见）;
3. cohort 开关与时刻——PUT 持久化 + 即时重排 + config-change 审计行（旧值→新值）
   + 越界 422;
4. 异步长任务（回测/探针/健康检查）——立即 202 返回 run_id,结果经
   ``GET /runs/{run_id}`` 读取（裁剪 summary:完整报告在 md/json）;同类并发 409;
5. 报告注册表——只读 status 头 + 定位标签 + 探针方向命中率;
6. 事件循环不被阻塞（状态接口与长任务派发均不得阻塞 asyncio 循环）+ 启动引导。

全部用例零网络零 LLM:batch 层任务函数经 ``ops_api._resolve_batch_func`` 注入假实现
（Task 4 落地的真实实现由 ``tests/outcome/test_ops_batches.py`` 的接线用例兜住）;
调度器句柄逐用例复位——不依赖其它测试模块（如 test_track_record_metrics 的
``TestSchedulerJobs``）可能泄漏的 Mock 句柄。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from finance_agent import ops_api
from finance_agent.api import app
from finance_agent.outcome.cohort.model import init_cohort_runs, insert_cohort_run
from finance_agent.outcome.ops.jobs import (
    JOB_FUNCS,
    JOB_IDS,
    SCHEDULES,
    CohortDisabled,
    JobAlreadyRunning,
    active_run_ids,
    mark_run_active,
    mark_run_finished,
    run_job,
)
from finance_agent.outcome.ops.model import (
    COHORT_HOUR_KEY,
    COHORT_MINUTE_KEY,
    finish_job_run,
    get_config,
    insert_job_run,
    last_job_run,
    list_job_runs,
    set_config,
)
from finance_agent.outcome.ops.prereg import KNOB_KEYS

BASE = "/api/v1/ops"

# 有效 outcome 预登记正文（七个门禁字段齐备 + 决策阈值带换算依据）
_PREREG_BODY = "\n".join(
    [
        "- 主指标: 逐决策 T+20 相对沪深300 超额收益均值与胜率",
        "- MDE: n=30 → 5.1pp（换算依据见 §4）",
        "- 决策阈值: 均值超额 95% CI 下限 > 0；依据：标的簇 bootstrap CI",
        "- 样本量依据: forward ≥10 / ≥30 / ≥100（MDE 反算）",
        "- 停止规则: 健康检查不过作废；探针 >0.60 降级",
        "- 成本分型: forward 每标的 1 次 deep；回测 回放 ×3 + 探针",
        "- 泄漏控制: 干净窗口 + 探针披露（阈值 0.60）",
    ]
)


# ── 夹具 ──


@pytest.fixture(autouse=True)
def _clean_scheduler_handle():
    """逐用例复位 scheduler 句柄(防其它测试模块的 Mock 泄漏影响「是否运行」判定)。"""
    import finance_agent.outcome.scheduler as scheduler_mod

    old = scheduler_mod._scheduler
    scheduler_mod._scheduler = None
    try:
        yield
    finally:
        scheduler_mod._scheduler = old


@pytest.fixture(autouse=True)
def _restore_cohort_schedule_snapshot():
    """SCHEDULES 是调度器启动期刷新的模块级快照,用例改过后复原(测试隔离)。"""
    spec = dict(SCHEDULES["cohort_batch"])
    try:
        yield
    finally:
        SCHEDULES["cohort_batch"].update(spec)


@pytest.fixture(autouse=True)
def _testing_env(monkeypatch):
    """TESTING=1(调度器不启动)+ cohort 开关回到「未配置」(默认关)。"""
    monkeypatch.setenv("TESTING", "1")
    for name in ("COHORT_ENABLED", "COHORT_HOUR", "COHORT_MINUTE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def patch_batch_funcs(monkeypatch):
    """按名注入 batch 层假实现;端点解析到未注入的名字 → AssertionError(强断言无外溢调用)。"""

    def _install(mapping: dict):
        def _resolve(name: str):
            assert name in mapping, f"端点解析了未注入的 batch 函数: {name}"
            return mapping[name]

        monkeypatch.setattr(ops_api, "_resolve_batch_func", _resolve)

    return _install


@pytest.fixture
def prereg_dir(tmp_path) -> Path:
    """有效预登记目录(真实过 assert_preregistered 门禁)。"""
    directory = tmp_path / "preregister"
    directory.mkdir()
    (directory / "2026-01-01-outcome-prereg.md").write_text(_PREREG_BODY, encoding="utf-8")
    return directory


def _db() -> Path:
    return Path(os.environ["SESSIONS_DB_PATH"])


def _seed_run(
    job_id: str,
    *,
    kind: str = "manual",
    status: str = "ok",
    summary: dict | None = None,
    error: str | None = None,
    source: str | None = None,
) -> int:
    """落一条终态运行历史(真实走 insert + finish 两段)。"""
    run_id = insert_job_run(
        job_id,
        kind,
        "running",
        source=source or ("manual" if kind == "manual" else "scheduled"),
        db_path=_db(),
    )
    finish_job_run(run_id, status=status, summary=summary, error=error, db_path=_db())
    return run_id


def _job(body: dict, job_id: str) -> dict:
    return next(j for j in body["jobs"] if j["job_id"] == job_id)


def _backdate_started_at(run_id: int, when: str = "2020-01-01T00:00:00") -> None:
    """把某行 started_at 回拨到过去（模拟时钟不可信：在跑的行也可能带旧时刻）。"""
    conn = sqlite3.connect(_db())
    try:
        conn.execute("UPDATE job_runs SET started_at=? WHERE run_id=?", (when, run_id))
        conn.commit()
    finally:
        conn.close()


def _wait_running_row(job_id: str, timeout: float = 5.0) -> int:
    """等某 job 落 running 行（真实执行路径开出的行），返回 run_id。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = last_job_run(job_id, _db())
        if row is not None and row["status"] == "running":
            return int(row["run_id"])
        time.sleep(0.02)
    raise AssertionError(f"{job_id} 未在 {timeout}s 内落 running 行")


def _wait_run(client: TestClient, run_id: int, timeout: float = 5.0) -> dict:
    """轮询 GET /runs/{id} 直到离开 running(异步任务完成)。"""
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        resp = client.get(f"{BASE}/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 超时未完成: {body}")


class _FakeTask:
    """可调用假任务:记录入参,返回固定结果或抛异常。"""

    def __init__(self, result: dict | None = None, exc: BaseException | None = None) -> None:
        self.result = result or {}
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


def _raising(exc: BaseException):
    """总是抛该异常的假实现(钉死错误映射)。"""

    def fn(*_args, **_kwargs):
        raise exc

    return fn


# ── 离线取数替身(与 tests/outcome/test_ops_batches.py 同款思路,零网络零 LLM)──


def _offline_kline(n: int = 60, start: float = 10.0) -> Any:
    import pandas as pd

    dates = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d").tolist()
    closes = [start]
    for i in range(1, n):
        closes.append(closes[-1] * (1.014 if i % 2 else 0.99))
    return pd.DataFrame(
        {"日期": dates, "开盘": closes, "收盘": closes, "最高": closes, "最低": closes}
    )


class _OfflineClient:
    """AKShareClient 签名对齐的零网络替身。"""

    def __init__(self, frame: Any = None) -> None:
        self._frame = frame

    def _kline(self, days: int | None) -> Any:
        return self._frame if self._frame is not None else _offline_kline(days or 60)

    def fetch_index_kline(self, _code: str, days: int | None = None) -> Any:
        return self._kline(days)

    def fetch_kline(self, _code: str, days: int | None = None, *, adjust: str = "qfq") -> Any:
        return self._kline(days)

    def fetch_news(self, _code: str, limit: int = 20) -> list[dict]:
        return [{"title": "公司发布日常经营公告"}]


def _offline_replay(code: str, decision_date: str, *, n: int = 3, full_kline: Any = None, **_kw):
    """与 replay_with_consistency 同形态的假回放(按帧取价,零 LLM)。"""
    dates = full_kline["日期"].astype(str).str[:10].tolist()
    closes = full_kline["收盘"].astype(float).tolist()
    idx = dates.index(str(decision_date)[:10])
    settle_idx = min(idx + 10, len(closes) - 1)
    return {
        "code": code,
        "decision_date": str(decision_date)[:10],
        "actions": ["buy"] * n,
        "agreement": 1.0,
        "settlement": {
            "status": "expired",
            "settle_date": dates[settle_idx],
            "settle_price": closes[settle_idx],
            "hold_days": 10,
            "decision_return": None,
            "benchmark_return": None,
            "decision_excess": None,
            "decision_hit": None,
        },
        "entry_price": closes[idx],
        "action": "buy",
        "snapshot_metadata": {},
    }


# ── 1. 状态与运行历史 ──


class TestJobsEndpoint:
    def test_reports_not_running_under_testing(self, client):
        body = client.get(f"{BASE}/jobs").json()
        assert body["scheduler_running"] is False
        assert [j["job_id"] for j in body["jobs"]] == list(JOB_IDS)
        assert all(j["next_fire_time"] is None for j in body["jobs"])
        assert all(j["label"] for j in body["jobs"])
        for job in body["jobs"]:
            # 排程粒度两形态（add-monthly-batch-scheduling）：日/周粒度 = day_of_week，
            # 月粒度 = day_of_month（互斥；时刻与时区恒有）
            assert set(job["schedule"]) in (
                {"day_of_week", "hour", "minute", "timezone"},
                {"day_of_month", "hour", "minute", "timezone"},
            ), job["job_id"]
            assert job["last_run"] is None and job["history"] == []
        # error 只在降级载荷出现（正常载荷不得带它）
        assert "error" not in body

    def test_last_run_and_history_shape(self, client):
        run_id = _seed_run("integrity_check", summary={"issues": 0})
        job = _job(client.get(f"{BASE}/jobs").json(), "integrity_check")
        assert job["last_run"]["run_id"] == run_id
        assert job["last_run"]["status"] == "ok"
        assert job["last_run"]["summary"] == {"issues": 0}
        assert job["last_run"]["finished_at"] is not None
        assert [h["run_id"] for h in job["history"]] == [run_id]

    def test_last_run_takes_newest_of_retry_attempts(self, client):
        """定时失败每次尝试各留一行(3 行)——last_run 取最新行,history 保留全部。"""
        ids = [
            _seed_run(
                "decision_settle_daily", kind="scheduled", status="failed", error=f"attempt {i}"
            )
            for i in range(3)
        ]
        # 最终行标注重试次数(scheduler._mark_retries 语义)
        finish_job_run(
            ids[-1], status="failed", summary={"attempts": 3, "retries": 2}, db_path=_db()
        )
        job = _job(client.get(f"{BASE}/jobs").json(), "decision_settle_daily")
        assert job["last_run"]["run_id"] == ids[-1]
        assert job["last_run"]["summary"] == {"attempts": 3, "retries": 2}
        assert [h["run_id"] for h in job["history"]] == list(reversed(ids))
        assert all(h["status"] == "failed" for h in job["history"])

    def test_history_is_bounded_and_newest_first(self, client):
        ids = [
            _seed_run("metrics_snapshot", summary={"i": i})
            for i in range(ops_api.HISTORY_LIMIT + 3)
        ]
        job = _job(client.get(f"{BASE}/jobs").json(), "metrics_snapshot")
        assert [h["run_id"] for h in job["history"]] == list(
            reversed(ids[-ops_api.HISTORY_LIMIT :])
        )

    def test_stale_running_swept_on_read(self, client, monkeypatch):
        """进程重启遗留的 running 行:读到状态时收尾为 failed(stale_process_restart)。"""
        stale = insert_job_run("daily_marking", "scheduled", "running", source="scheduled")
        # 模拟「本进程启动晚于该行」——只有更早启动的进程遗留行才会满足
        monkeypatch.setattr(
            ops_api, "_PROCESS_STARTED_AT", (datetime.now() + timedelta(minutes=5)).isoformat()
        )
        job = _job(client.get(f"{BASE}/jobs").json(), "daily_marking")
        assert job["last_run"]["run_id"] == stale
        row = last_job_run("daily_marking")
        assert job["last_run"]["status"] == "failed", (
            f"悬挂行未被清扫（issue #334 失败现场）：active_run_ids={sorted(active_run_ids())} "
            f"row.started_at={row['started_at']!r} _PROCESS_STARTED_AT={ops_api._PROCESS_STARTED_AT!r}"
        )
        assert job["last_run"]["finished_at"] is not None
        assert "stale_process_restart" in (job["last_run"]["error"] or "")
        assert job["last_run"]["summary"]["reason"] == "stale_process_restart"

    def test_live_running_row_not_swept(self, client):
        """本进程正在跑的行不得被清扫(否则并发轮询会把补跑误标为失败)。"""
        live = insert_job_run("daily_marking", "manual", "running", source="manual")
        job = _job(client.get(f"{BASE}/jobs").json(), "daily_marking")
        assert job["last_run"]["run_id"] == live
        assert job["last_run"]["status"] == "running"

    def test_foreign_db_leaked_active_does_not_block_sweep(self, client, monkeypatch, tmp_path):
        """issue #334 回归：他库（跨用例线程遗留）同号 active 不得挡住本库悬挂行清扫。

        机制：`_ACTIVE_RUN_IDS` 进程级、run_id 每库独立自增——所有权须按库作用域，
        否则前一测试的漏注销 id 会把后一测试的同号悬挂行永远挡在清扫之外（CI flake）。
        """
        stale = insert_job_run("daily_marking", "scheduled", "running", source="scheduled")
        monkeypatch.setattr(
            ops_api, "_PROCESS_STARTED_AT", (datetime.now() + timedelta(minutes=5)).isoformat()
        )
        foreign_db = tmp_path / "other-process.db"
        mark_run_active(stale, db_path=foreign_db)  # 模拟他库遗留的所有权
        try:
            job = _job(client.get(f"{BASE}/jobs").json(), "daily_marking")
            assert job["last_run"]["run_id"] == stale
            assert job["last_run"]["status"] == "failed", (
                f"他库同号 active 挡住了本库清扫：active(all)={sorted(active_run_ids())}"
            )
        finally:
            mark_run_finished(stale, db_path=foreign_db)

    def test_backdated_inflight_run_not_swept_but_foreign_stale_row_is(self, client, monkeypatch):
        """清扫以所有权为准:在跑的行即使 started_at 被回拨也不得清扫,无主的陈旧行仍被清扫。"""
        gate = threading.Event()
        monkeypatch.setitem(JOB_FUNCS, "daily_marking", lambda: gate.wait(5) or {"ok": True})
        # 时钟条件对两条行都成立(进程启动时刻被推到未来)——只有所有权能把它们分开
        monkeypatch.setattr(
            ops_api, "_PROCESS_STARTED_AT", (datetime.now() + timedelta(minutes=5)).isoformat()
        )
        foreign = insert_job_run("metrics_snapshot", "scheduled", "running", source="scheduled")
        _backdate_started_at(foreign)
        worker = threading.Thread(
            target=run_job, args=("daily_marking",), kwargs={"source": "manual"}, daemon=True
        )
        worker.start()
        try:
            live_id = _wait_running_row("daily_marking")
            _backdate_started_at(live_id)
            body = client.get(f"{BASE}/jobs").json()
            live = _job(body, "daily_marking")["last_run"]
            assert live["run_id"] == live_id and live["status"] == "running"
            swept = _job(body, "metrics_snapshot")["last_run"]
            assert swept["run_id"] == foreign and swept["status"] == "failed"
            assert "stale_process_restart" in (swept["error"] or "")
        finally:
            gate.set()
            worker.join(5)

    def test_run_detail_reachable_beyond_listing_window(self, client, monkeypatch):
        """单条运行按 run_id 直读:不依赖「最新 1000 行」窗口,旧行不会因窗口 404。"""
        run_id = _seed_run("integrity_check", summary={"issues": 0})
        monkeypatch.setattr(ops_api, "list_job_runs", lambda *a, **k: [])
        resp = client.get(f"{BASE}/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == run_id
        assert resp.json()["summary"] == {"issues": 0}

    def test_status_read_prunes_history_per_job(self, client, monkeypatch):
        """状态读取路径顺带裁剪运行历史(每 job 保最新 N 行),防表无限膨胀。"""
        assert ops_api.PRUNE_KEEP_PER_JOB == 500  # 裁剪线 500(实施约定)
        monkeypatch.setattr(ops_api, "PRUNE_KEEP_PER_JOB", 2)
        ids = [_seed_run("metrics_snapshot", summary={"i": i}) for i in range(5)]
        client.get(f"{BASE}/jobs")
        rows = list_job_runs("metrics_snapshot", db_path=_db(), limit=1000)
        assert [r["run_id"] for r in rows] == list(reversed(ids[-2:]))

    def test_degraded_payload_on_unexpected_db_error(self, client, monkeypatch):
        """意外 DB 故障不得把状态接口打成 500:降级但合法的载荷 + 顶层 error 字段。"""
        monkeypatch.setattr(ops_api, "last_job_run", _raising(RuntimeError("db locked")))
        resp = client.get(f"{BASE}/jobs")
        assert resp.status_code == 200
        body = resp.json()
        assert "db locked" in body["error"]
        assert body["scheduler_running"] is False
        assert [j["job_id"] for j in body["jobs"]] == list(JOB_IDS)
        assert all(j["last_run"] is None and j["history"] == [] for j in body["jobs"])
        assert set(body["cohort"]) == {
            "enabled",
            "hour",
            "minute",
            "budget_tokens",
            "today_spend",
            "today_success",
            "today_failure",
        }

    def test_cohort_job_row_agrees_with_cohort_block(self, client):
        """cohort 行不得渲染调度器启动期的陈旧快照,须与 cohort 块同源(不可能互相矛盾)。"""
        SCHEDULES["cohort_batch"].update({"hour": 7, "minute": 5})  # 陈旧快照
        set_config(COHORT_HOUR_KEY, "19", _db())
        set_config(COHORT_MINUTE_KEY, "30", _db())
        body = client.get(f"{BASE}/jobs").json()
        job = _job(body, "cohort_batch")
        assert (job["schedule"]["hour"], job["schedule"]["minute"]) == (19, 30)
        assert (job["schedule"]["hour"], job["schedule"]["minute"]) == (
            body["cohort"]["hour"],
            body["cohort"]["minute"],
        )
        # 结算链四任务时刻不受影响
        assert _job(body, "decision_settle_daily")["schedule"]["hour"] == 16

    def test_cohort_block_fields(self, client):
        body = client.get(f"{BASE}/jobs").json()
        assert set(body["cohort"]) == {
            "enabled",
            "hour",
            "minute",
            "budget_tokens",
            "today_spend",
            "today_success",
            "today_failure",
        }
        assert body["cohort"]["enabled"] is False
        assert body["cohort"]["hour"] == 18 and body["cohort"]["minute"] == 0
        assert body["cohort"]["budget_tokens"] > 0

    def test_cohort_block_counts_today_spend(self, client):
        init_cohort_runs(_db())
        day = datetime.now().strftime("%Y-%m-%d")
        for ticker, status, tokens in (
            ("600519", "success", 1200),
            ("000858", "failure", None),
            ("300308", "skipped", None),
            ("600000", "success", 300),
        ):
            insert_cohort_run(
                {
                    "universe_version": "v1",
                    "ticker": ticker,
                    "trade_date": day,
                    "status": status,
                    "tokens_total": tokens,
                    "trigger_time": datetime.now().isoformat(),
                },
                _db(),
            )
        # 昨日行不计入今日花费
        insert_cohort_run(
            {
                "universe_version": "v1",
                "ticker": "002412",
                "trade_date": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                "status": "success",
                "tokens_total": 9999,
                "trigger_time": datetime.now().isoformat(),
            },
            _db(),
        )
        block = client.get(f"{BASE}/cohort").json()
        assert block["today_spend"] == 1500
        assert block["today_success"] == 2 and block["today_failure"] == 1


# ── 2. 手动补跑 ──


class TestManualRun:
    def test_ok_returns_202_and_lands_history(self, client, monkeypatch):
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: {"issues": 0})
        resp = client.post(f"{BASE}/jobs/integrity_check/run")
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        assert isinstance(run_id, int)
        job = _job(client.get(f"{BASE}/jobs").json(), "integrity_check")
        assert job["last_run"]["run_id"] == run_id
        assert job["last_run"]["status"] == "ok"
        assert job["last_run"]["summary"] == {"issues": 0}
        assert job["last_run"]["kind"] == "manual"
        assert job["last_run"]["source"] == "manual"
        # run 详情可读(单条运行接口)
        detail = client.get(f"{BASE}/runs/{run_id}").json()
        assert detail["run_id"] == run_id and detail["summary"] == {"issues": 0}

    def test_failed_run_is_non_2xx_with_reason(self, client, monkeypatch):
        """任务失败返回 dict(status=failed)不抛——端点必须翻成非 2xx,不得伪装成功。"""

        def boom():
            raise RuntimeError("db locked")

        monkeypatch.setitem(JOB_FUNCS, "daily_marking", boom)
        resp = client.post(f"{BASE}/jobs/daily_marking/run")
        assert resp.status_code == 500
        assert "db locked" in resp.json()["detail"]
        job = _job(client.get(f"{BASE}/jobs").json(), "daily_marking")
        assert job["last_run"]["status"] == "failed"
        assert "db locked" in job["last_run"]["error"]

    def test_already_running_409(self, client, monkeypatch):
        def busy(job_id, **_kwargs):
            raise JobAlreadyRunning(f"{job_id} 已在运行")

        monkeypatch.setattr(ops_api, "run_job", busy)
        resp = client.post(f"{BASE}/jobs/integrity_check/run")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_running"

    def test_cohort_disabled_409_and_zero_llm(self, client):
        """开关关:手动跑批拒绝(零 LLM 调用),历史留 skipped-disabled 行。"""
        resp = client.post(f"{BASE}/jobs/cohort_batch/run")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "cohort_disabled"
        row = last_job_run("cohort_batch", _db())
        assert row["status"] == "skipped-disabled"

    def test_unknown_job_404(self, client):
        assert client.post(f"{BASE}/jobs/nope/run").status_code == 404

    def test_serialization_valueerror_is_500_not_404(self, client, monkeypatch):
        """合法 job 的真故障(summary 循环引用 → ValueError)不得被误报成「未知任务」404。"""
        circular: dict[str, Any] = {}
        circular["self"] = circular
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: circular)
        resp = client.post(f"{BASE}/jobs/integrity_check/run")
        assert resp.status_code == 500
        assert "Circular reference" in resp.json()["detail"]

    def test_unknown_run_404(self, client):
        assert client.get(f"{BASE}/runs/999999").status_code == 404


# ── 3. cohort 开关与时刻 ──


class TestCohortControl:
    def test_put_persists_reschedules_and_audits(self, client, monkeypatch):
        calls: list[tuple[int, int]] = []
        monkeypatch.setattr(ops_api, "reschedule_cohort", lambda h, m: calls.append((h, m)) or True)
        resp = client.put(f"{BASE}/cohort", json={"enabled": True, "hour": 19, "minute": 30})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True and resp.json()["rescheduled"] is True
        assert calls == [(19, 30)]
        # 持久化(重启保持语义:表值是运行期唯一真源)
        assert get_config(COHORT_HOUR_KEY, _db()) == "19"
        assert get_config(COHORT_MINUTE_KEY, _db()) == "30"
        assert client.get(f"{BASE}/cohort").json()["minute"] == 30
        # 审计行:旧值→新值
        audits = [
            r for r in list_job_runs("cohort_batch", db_path=_db()) if r["kind"] == "config-change"
        ]
        assert len(audits) == 1
        assert audits[0]["status"] == "ok" and audits[0]["finished_at"] is not None
        assert audits[0]["summary"]["from"] == {"enabled": False, "hour": 18, "minute": 0}
        assert audits[0]["summary"]["to"] == {"enabled": True, "hour": 19, "minute": 30}
        assert resp.json()["audit_run_id"] == audits[0]["run_id"]

    def test_put_partial_update_keeps_other_keys(self, client, monkeypatch):
        monkeypatch.setattr(ops_api, "reschedule_cohort", lambda h, m: True)
        client.put(f"{BASE}/cohort", json={"hour": 20})
        body = client.get(f"{BASE}/cohort").json()
        assert (body["hour"], body["minute"], body["enabled"]) == (20, 0, False)

    def test_put_rejects_out_of_range(self, client):
        assert client.put(f"{BASE}/cohort", json={"hour": 24}).status_code == 422
        assert client.put(f"{BASE}/cohort", json={"minute": 60}).status_code == 422
        assert client.put(f"{BASE}/cohort", json={"enabled": "maybe"}).status_code == 422
        assert get_config(COHORT_HOUR_KEY, _db()) is None

    def test_put_records_audit_even_without_scheduler(self, client):
        """TESTING(无调度器)下配置仍须落库 + 留审计,rescheduled=False 如实返回。"""
        resp = client.put(f"{BASE}/cohort", json={"hour": 9, "minute": 5})
        assert resp.status_code == 200
        assert resp.json()["rescheduled"] is False
        assert resp.json()["hour"] == 9


# ── 4. 异步长任务(回测/探针/健康检查) ──


class TestAsyncTasks:
    def test_backtest_pathway_returns_202_and_trimmed_summary(self, client, patch_batch_funcs):
        fat = {
            "conclusion": "通路验证定位（batch_kind=pathway）：本批不产出 skill / 赚钱能力结论句",
            "positioning": "pathway",
            "batch_kind": "pathway",
            "n_sample": 3,
            "n_consistent": 2,
            "clean_window": None,
            "leakage_probe": {
                "state": "measurable",
                "direction_hit_rate": 0.3,
                "unknown_ratio": 0.1,
                "downgraded": False,
                "threshold": 0.6,
                "probe_n": 10,
                "details": [{"huge": "x" * 50}],
            },
            "perf_table": {
                "system": {"CR": 0.1, "ARR": 0.2, "Sharpe": 1.5, "MDD": 0.2},
                "buy_hold": {"Sharpe": 0.7},
            },
            "consistency": {"per_symbol": [{"code": "600519"}]},
            "methodology": {"entry": "..."},
            "report_paths": {
                "md": "evals/backtest/results/pathway-1.md",
                "json": "reports/backtest/x.json",
            },
        }
        fake = _FakeTask(result=fat)
        patch_batch_funcs({"run_backtest_task": fake})

        resp = client.post(
            f"{BASE}/backtest",
            json={"batch_kind": "pathway", "codes": ["600519"], "per_regime": 1},
        )
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["job_id"] == "backtest" and run["status"] == "ok"
        summary = run["summary"]
        # 裁剪版字段路径齐备
        assert summary["conclusion"] == fat["conclusion"]
        assert summary["positioning"] == "pathway"
        assert summary["n_sample"] == 3 and summary["n_consistent"] == 2
        assert summary["leakage_probe"]["direction_hit_rate"] == 0.3
        assert summary["perf_table"]["system"]["Sharpe"] == 1.5
        assert summary["report_paths"]["md"].endswith(".md")
        # 体积大的明细不进 summary(完整报告在 md/json)
        assert "details" not in summary["leakage_probe"]
        assert "consistency" not in summary
        assert "buy_hold" not in summary["perf_table"]
        assert len(json.dumps(summary, ensure_ascii=False)) < 2000
        # 端点把请求透传给任务
        assert fake.calls[0]["batch_kind"] == "pathway"
        assert fake.calls[0]["codes"] == ["600519"]
        assert fake.calls[0]["per_regime"] == 1

    def test_backtest_formal_refused_without_preregistration(self, client, monkeypatch, tmp_path):
        """空预登记目录:同步门禁拒绝(409 + 原因),不解析/派发任何 batch 函数。"""
        monkeypatch.setattr(ops_api, "PREREGISTER_DIR", tmp_path / "preregister")
        resolved: list[str] = []

        def _resolve(name: str):
            resolved.append(name)
            return _FakeTask(result={"conclusion": "不应跑到这里"})

        monkeypatch.setattr(ops_api, "_resolve_batch_func", _resolve)
        resp = client.post(f"{BASE}/backtest", json={"batch_kind": "formal", "codes": ["600519"]})
        assert resp.status_code == 409
        assert "预登记" in resp.json()["detail"]
        assert resolved == []
        assert list_job_runs("backtest", db_path=_db()) == []

    def test_backtest_formal_clean_window_refusal_409(
        self, client, monkeypatch, prereg_dir, patch_batch_funcs
    ):
        """预登记有效但干净窗口拒绝 → 409 + 原因(而非 202 后静默失败),且不派发任务。"""

        class _CleanWindowRefusalError(RuntimeError):
            pass

        def _preflight(**_kwargs):
            raise _CleanWindowRefusalError("决策日 2024-06-03 距 as_of 仅 3 个交易日")

        monkeypatch.setattr(ops_api, "PREREGISTER_DIR", prereg_dir)
        patch_batch_funcs({"prepare_backtest": _preflight})
        monkeypatch.setattr(
            ops_api,
            "_is_clean_window_refusal",
            lambda exc: isinstance(exc, _CleanWindowRefusalError),
        )
        resp = client.post(
            f"{BASE}/backtest", json={"batch_kind": "formal", "codes": ["600519"], "per_regime": 1}
        )
        assert resp.status_code == 409
        assert "3 个交易日" in resp.json()["detail"]
        # 门禁未通过:不派发任务(无运行历史行、无回放)
        assert list_job_runs("backtest", db_path=_db()) == []

    def test_backtest_formal_dispatches_with_prepared_gate_result(
        self, client, monkeypatch, prereg_dir, patch_batch_funcs
    ):
        """门禁通过:prepare_backtest 的结果随任务透传(不重复取数/不重复判定)。"""
        prepared = {"sample": [{"code": "600519", "regime": "bull", "decision_date": "x"}]}
        prepare = _FakeTask(result=prepared)
        task = _FakeTask(result={"conclusion": "上界证据", "positioning": "skill"})
        monkeypatch.setattr(ops_api, "PREREGISTER_DIR", prereg_dir)
        patch_batch_funcs({"prepare_backtest": prepare, "run_backtest_task": task})
        resp = client.post(
            f"{BASE}/backtest", json={"batch_kind": "formal", "codes": ["600519"], "per_regime": 1}
        )
        assert resp.status_code == 202
        assert _wait_run(client, resp.json()["run_id"])["status"] == "ok"
        assert prepare.calls[0]["batch_kind"] == "formal"
        assert task.calls[0]["prepared"] is prepared

    def test_backtest_task_failure_recorded_not_202_success(self, client, patch_batch_funcs):
        """异步任务内部失败:运行记录如实 failed(端点已 202,失败经 /runs/{id} 可见)。"""
        patch_batch_funcs({"run_backtest_task": _FakeTask(exc=RuntimeError("采样失败"))})
        resp = client.post(f"{BASE}/backtest", json={"batch_kind": "pathway", "codes": ["600519"]})
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["status"] == "failed"
        assert "采样失败" in run["error"]

    def test_probe_returns_readings_without_details(self, client, patch_batch_funcs):
        reading = {
            "state": "unmeasurable",
            "probe_n": 3,
            "questions_per_ticker": 3,
            "direction_hit_rate": None,
            "magnitude_hit_rate": None,
            "event_hit_rate": None,
            "unknown_ratio": 1.0,
            "threshold": 0.6,
            "downgraded": False,
            "probe_window": ["2024-06-03"],
            "per_window": [{"probe_window": ["2024-06-03"], "state": "unmeasurable"}],
            "details": [{"ticker": "600519", "prompt": "x" * 5000}],
        }
        fake = _FakeTask(result=reading)
        patch_batch_funcs({"run_probe_task": fake})
        resp = client.post(
            f"{BASE}/probe", json={"codes": ["600519"], "decision_date": "2024-06-03"}
        )
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["status"] == "ok" and run["job_id"] == "probe"
        summary = run["summary"]
        # 不可测态如实为 null(不折算 0),逐窗口明细保留,逐题 details 不进 summary
        assert summary["state"] == "unmeasurable"
        assert summary["direction_hit_rate"] is None
        assert summary["unknown_ratio"] == 1.0
        assert summary["per_window"]
        assert "details" not in summary
        assert fake.calls[0]["decision_date"] == "2024-06-03"

    def test_probe_requires_codes_and_decision_date(self, client):
        assert (
            client.post(
                f"{BASE}/probe", json={"codes": [], "decision_date": "2024-06-03"}
            ).status_code
            == 422
        )
        assert client.post(f"{BASE}/probe", json={"codes": ["600519"]}).status_code == 422
        assert (
            client.post(
                f"{BASE}/probe",
                json={"codes": ["600519"], "decision_date": "2024-06-03", "window_days": 0},
            ).status_code
            == 422
        )

    def test_health_dispatch_passes_gates_summary(self, client, patch_batch_funcs):
        """健康检查结果(门禁读数)原样入 summary:缺失读数如实为 None,不冒充 0/100%。"""
        reading = {
            "db": "x.db",
            "available": True,
            "passed": False,
            "gates": [
                {
                    "id": "settlement_success",
                    "label": "结算成功率",
                    "value": None,
                    "threshold": ">= 0.9",
                    "passed": False,
                    "reason": "无已结算样本（无读数）",
                },
                {
                    "id": "integrity",
                    "label": "快照完整性",
                    "value": 0,
                    "threshold": "== 0",
                    "passed": True,
                    "reason": "无不一致",
                },
            ],
        }
        patch_batch_funcs({"run_health_task": _FakeTask(result=reading)})
        resp = client.post(f"{BASE}/health")
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["status"] == "ok" and run["job_id"] == "health"
        assert run["summary"] == reading
        settlement = next(g for g in run["summary"]["gates"] if g["id"] == "settlement_success")
        assert settlement["value"] is None and settlement["passed"] is False

    def test_same_kind_concurrent_refused(self, client, monkeypatch):
        gate = threading.Event()

        def slow(**_kwargs):
            gate.wait(3)
            return {"state": "measurable", "direction_hit_rate": 0.5}

        monkeypatch.setattr(ops_api, "_resolve_batch_func", lambda _name: slow)
        first = client.post(
            f"{BASE}/probe", json={"codes": ["600519"], "decision_date": "2024-06-03"}
        )
        assert first.status_code == 202
        try:
            second = client.post(
                f"{BASE}/probe", json={"codes": ["600519"], "decision_date": "2024-06-03"}
            )
            assert second.status_code == 409
            assert second.json()["detail"] == "already_running"
        finally:
            gate.set()
        assert _wait_run(client, first.json()["run_id"])["status"] == "ok"
        # 锁释放后同类任务可再次派发
        third = client.post(
            f"{BASE}/probe", json={"codes": ["600519"], "decision_date": "2024-06-03"}
        )
        assert third.status_code == 202
        _wait_run(client, third.json()["run_id"])


# ── 4b. 端点 × 真实 batch 层(接线 + 门禁语义,离线) ──


class TestRealBatchIntegration:
    """Task 4 落地后补的接线用例:真实 ``outcome.ops.batches`` 走 HTTP 端点(零网络零 LLM)。"""

    def _patch_offline(self, monkeypatch, *, frame: Any = None):
        from finance_agent.outcome.ops import batches

        monkeypatch.setattr(batches, "_default_client", lambda: _OfflineClient(frame))
        monkeypatch.setattr(batches, "replay_with_consistency", _offline_replay)
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            lambda *a, **k: [
                {
                    "code": "600519",
                    "regime": "bull",
                    "decision_date": _offline_kline(60)["日期"][30],
                }
            ],
        )

    def test_formal_clean_window_refusal_via_real_batches(self, client, monkeypatch, prereg_dir):
        """真实 prepare_backtest + 真实 CleanWindowError → 409(门禁拒绝,不派发回放)。"""
        from finance_agent.outcome.ops import batches

        monkeypatch.setattr(ops_api, "PREREGISTER_DIR", prereg_dir)
        self._patch_offline(monkeypatch)
        monkeypatch.setattr(
            batches,
            "assert_clean_window",
            lambda *a, **k: {"passed": False, "reason": "决策日 2024-06-03 距 as_of 仅 3 个交易日"},
        )
        resp = client.post(
            f"{BASE}/backtest", json={"batch_kind": "formal", "codes": ["600519"], "per_regime": 1}
        )
        assert resp.status_code == 409
        assert "3 个交易日" in resp.json()["detail"]
        assert list_job_runs("backtest", db_path=_db()) == []

    def test_formal_bad_pool_is_422_with_reason(self, client, monkeypatch, prereg_dir):
        """标的池不足 per_regime → 422 + 原因原文(不裸 500、不派发)。"""
        from finance_agent.outcome.ops import batches

        monkeypatch.setattr(ops_api, "PREREGISTER_DIR", prereg_dir)
        self._patch_offline(monkeypatch)
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            _raising(ValueError("regime bull 标的池不足 10")),
        )
        resp = client.post(f"{BASE}/backtest", json={"batch_kind": "formal", "codes": ["600519"]})
        assert resp.status_code == 422
        assert "标的池不足" in resp.json()["detail"]
        assert list_job_runs("backtest", db_path=_db()) == []

    def test_pathway_offline_end_to_end_writes_report(self, client, monkeypatch, tmp_path):
        """通路批全链路(真实 batches + 真实 run_backtest):报告落 md/json,summary 裁剪。"""
        monkeypatch.chdir(tmp_path)
        self._patch_offline(monkeypatch)
        resp = client.post(
            f"{BASE}/backtest",
            json={"batch_kind": "pathway", "codes": ["600519"], "per_regime": 1},
        )
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["status"] == "ok"
        summary = run["summary"]
        assert summary["positioning"] == "pathway"
        assert summary["conclusion"].startswith("通路验证定位")
        assert summary["leakage_probe"]["direction_hit_rate"] is None  # pathway 不跑探针
        md_rel = summary["report_paths"]["md"]
        assert md_rel.startswith("evals/backtest/results/pathway-")
        md_path = tmp_path / md_rel
        assert md_path.exists() and (tmp_path / summary["report_paths"]["json"]).exists()
        from evals.causal_ablation.report_status import parse_status

        assert parse_status(md_path.read_text(encoding="utf-8"))[0] == "active"
        # 报告注册表(指向同一目录时)能看到刚生成的报告
        monkeypatch.setattr(ops_api, "BACKTEST_RESULTS_DIR", tmp_path / "evals/backtest/results")
        names = [item["name"] for item in client.get(f"{BASE}/reports").json()]
        assert md_path.stem in names

    def test_health_run_uses_real_collector(self, client):
        """真实健康检查(隔离空库):读数缺失如实为 None(「无读数」),不冒充 0/100%。"""
        resp = client.post(f"{BASE}/health")
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["status"] == "ok" and run["job_id"] == "health"
        summary = run["summary"]
        assert summary["available"] is True
        gates = {g["id"]: g for g in summary["gates"]}
        assert set(gates) == {"settlement_success", "unresolvable", "integrity", "bookkeeping"}
        assert gates["settlement_success"]["value"] is None
        assert gates["settlement_success"]["passed"] is False
        assert "无读数" in gates["settlement_success"]["reason"]
        assert gates["integrity"]["passed"] is True
        assert summary["passed"] is False
        assert summary["readings"]["total"] == 0

    def test_probe_offline_end_to_end(self, client, monkeypatch):
        """探针单跑全链路(真实 run_leakage_probe + 假 LLM):三态读数经 /runs 可读。"""
        from finance_agent.outcome.ops import batches

        frame = _offline_kline(60)
        decision_date = str(frame["日期"][10])[:10]
        monkeypatch.setattr(batches, "_default_client", lambda: _OfflineClient(frame))
        monkeypatch.setattr("evals.backtest.leakage_probe._default_llm", lambda _prompt: "我不确定")
        resp = client.post(
            f"{BASE}/probe",
            json={
                "codes": ["600519"],
                "decision_date": decision_date,
                "window_days": 20,
                "n_tickers": 1,
            },
        )
        assert resp.status_code == 202
        run = _wait_run(client, resp.json()["run_id"])
        assert run["status"] == "ok"
        assert run["summary"]["state"] == "unmeasurable"
        assert run["summary"]["direction_hit_rate"] is None
        assert "details" not in run["summary"]
        # 窗口披露字段按请求入参合成(不为 null):单窗口 + 逐窗口读数一条
        assert run["summary"]["probe_window"] == [decision_date]
        per_window = run["summary"]["per_window"]
        assert isinstance(per_window, list) and len(per_window) == 1
        assert per_window[0]["probe_window"] == [decision_date]
        assert per_window[0]["window_days"] == 20
        assert per_window[0]["state"] == "unmeasurable"
        assert per_window[0]["direction_hit_rate"] is None


# ── 5. 报告注册表 ──


class TestReportsRegistry:
    def test_registry_reads_status_header(self, client):
        body = client.get(f"{BASE}/reports").json()
        assert isinstance(body, list) and body
        assert any(item["name"].startswith("pilot-2023-shock") for item in body)
        assert all(item["status"] in ("active", "superseded-by") for item in body)
        pilot = next(i for i in body if i["name"].startswith("pilot-2023-shock"))
        assert set(pilot) == {
            "name",
            "path",
            "status",
            "target",
            "positioning",
            "probe_direction_hit_rate",
        }
        assert pilot["positioning"] == "pathway"  # 正文标注「通路验证」
        assert pilot["path"].endswith("evals/backtest/results/pilot-2023-shock.md")

    def test_registry_reads_positioning_and_probe_rate(self, client, monkeypatch, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        (results / "formal-1.md").write_text(
            "\n".join(
                [
                    "# 回测报告：formal-1",
                    "**status**: superseded-by: pathway-2.md",
                    "**批次类型**: formal",
                    "- 方向命中率 direction_hit_rate：12.5%（阈值 threshold：60%）",
                    "泄漏污染下的上界证据（真实 skill ≤ 读数）：无显著差异",
                ]
            ),
            encoding="utf-8",
        )
        (results / "pathway-2.md").write_text(
            "\n".join(
                [
                    "# 回测报告：pathway-2",
                    "**status**: active",
                    "**批次类型**: pathway",
                    "- 方向命中率 direction_hit_rate：80%（阈值 threshold：60%）",
                    "通路验证定位（batch_kind=pathway）：本批不产出 skill 结论句",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ops_api, "BACKTEST_RESULTS_DIR", results)
        body = client.get(f"{BASE}/reports").json()
        by_name = {i["name"]: i for i in body}
        assert by_name["formal-1"]["status"] == "superseded-by"
        assert by_name["formal-1"]["target"] == "pathway-2.md"
        assert by_name["formal-1"]["positioning"] == "upper_bound"
        assert by_name["formal-1"]["probe_direction_hit_rate"] == 0.125
        assert by_name["pathway-2"]["positioning"] == "pathway"
        assert by_name["pathway-2"]["probe_direction_hit_rate"] == 0.8

    def test_registry_missing_dir_is_empty_not_error(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(ops_api, "BACKTEST_RESULTS_DIR", tmp_path / "nope")
        assert client.get(f"{BASE}/reports").json() == []

    def test_registry_flags_defective_report_loudly(self, client, monkeypatch, tmp_path):
        """superseded-by 缺目标是有缺陷的报告,不得静默跳过(整表显式报错)。"""
        results = tmp_path / "results"
        results.mkdir()
        (results / "broken.md").write_text(
            "# 回测报告：broken\n**status**: superseded-by\n", encoding="utf-8"
        )
        monkeypatch.setattr(ops_api, "BACKTEST_RESULTS_DIR", results)
        resp = client.get(f"{BASE}/reports")
        assert resp.status_code == 500
        assert "报告注册表" in resp.json()["detail"]


# ── 6. 事件循环不被阻塞 + 启动引导 ──


class TestEventLoopAndLifespan:
    async def test_status_endpoint_does_not_block_event_loop(self, monkeypatch):
        def slow_payload():
            time.sleep(0.5)
            return {"scheduler_running": False, "jobs": [], "cohort": {}}

        monkeypatch.setattr(ops_api, "_jobs_payload", slow_payload)
        wakeups = 0

        async def ticker():
            nonlocal wakeups
            while True:
                await asyncio.sleep(0.05)
                wakeups += 1

        task = asyncio.create_task(ticker())
        body = await ops_api.get_jobs()
        await asyncio.sleep(0.1)
        task.cancel()
        assert wakeups >= 5, f"事件循环被阻塞(唤醒 {wakeups} 次)"
        assert body["jobs"] == []

    async def test_long_task_dispatch_returns_without_blocking(self, monkeypatch):
        def slow_task(**_kwargs):
            time.sleep(0.5)
            return {"conclusion": "ok", "positioning": "pathway"}

        monkeypatch.setattr(ops_api, "_resolve_batch_func", lambda _name: slow_task)
        wakeups = 0

        async def ticker():
            nonlocal wakeups
            while True:
                await asyncio.sleep(0.05)
                wakeups += 1

        task = asyncio.create_task(ticker())
        started = time.time()
        body = await ops_api.trigger_backtest(
            ops_api.BacktestRequest(batch_kind="pathway", codes=["600519"])
        )
        elapsed = time.time() - started
        assert body["run_id"]
        assert elapsed < 0.3, f"派发阻塞了 {elapsed:.2f}s"
        # 长任务仍在后台线程运行(0.5s)期间,事件循环必须照常唤醒
        await asyncio.sleep(0.4)
        task.cancel()
        assert wakeups >= 4, f"事件循环被阻塞(唤醒 {wakeups} 次)"

    def test_lifespan_bootstraps_cohort_config_from_env(self, monkeypatch):
        """启动引导:env 只在表内缺失时落表(重启保持),端点随即读到。"""
        monkeypatch.setenv("TESTING", "1")  # 防真调度器被拉起
        monkeypatch.setenv("COHORT_ENABLED", "1")
        monkeypatch.setenv("COHORT_HOUR", "20")
        monkeypatch.delenv("COHORT_MINUTE", raising=False)
        with TestClient(app) as c:
            body = c.get(f"{BASE}/cohort").json()
        assert body["enabled"] is True and body["hour"] == 20 and body["minute"] == 0
        # 已有表值不被引导覆盖
        set_config(COHORT_MINUTE_KEY, "45", _db())
        with TestClient(app) as c:
            assert c.get(f"{BASE}/cohort").json()["minute"] == 45


def test_job_already_running_and_cohort_disabled_are_runtime_errors():
    """端点映射依赖的异常类型(回归护栏)。"""
    assert issubclass(JobAlreadyRunning, RuntimeError)
    assert issubclass(CohortDisabled, RuntimeError)


# ── 7. 调度器句柄健壮性(跨测试模块泄漏的 Mock / 真实运行态)──


class TestSchedulerHandleRobustness:
    def test_mock_handle_does_not_claim_running(self, client):
        """句柄被其它测试模块泄漏成 Mock 时不得冒充「已运行」:非真实调度器 → False/None。"""
        from unittest.mock import MagicMock

        import finance_agent.outcome.scheduler as scheduler_mod

        scheduler_mod._scheduler = MagicMock()  # autouse 夹具负责复原
        body = client.get(f"{BASE}/jobs").json()
        assert body["scheduler_running"] is False
        assert all(j["next_fire_time"] is None for j in body["jobs"])

    def test_real_running_scheduler_reports_next_fire_time(self, client):
        """真实在跑的 BackgroundScheduler → scheduler_running=True + 该任务 next_fire_time 有值。"""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        import finance_agent.outcome.scheduler as scheduler_mod

        real = BackgroundScheduler()
        real.add_job(
            lambda: None,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=40, timezone="Asia/Shanghai"),
            id="integrity_check",
        )
        real.start()
        try:
            scheduler_mod._scheduler = real
            body = client.get(f"{BASE}/jobs").json()
        finally:
            real.shutdown(wait=False)
        assert body["scheduler_running"] is True
        job = _job(body, "integrity_check")
        assert job["next_fire_time"] is not None
        assert job["next_fire_time"].startswith("20")  # ISO 时间戳
        assert datetime.fromisoformat(job["next_fire_time"]).tzinfo is not None


# ── 8. 预登记版本化与口径草稿（Task 5,spec R6）──

# 红线两文件(本文件在 tests/ 下,仓库根 = parents[1]):口径操作前后逐字节不变
_LEDGER = Path(__file__).resolve().parents[1] / "docs" / "evals" / "metrics.md"
_CALIBER_CONST = Path(__file__).resolve().parents[1] / "evals" / "outcome" / "caliber.py"


def _prereg_fields() -> dict[str, str]:
    """既有 ``_PREREG_BODY`` 的 `- 字段: 值` 行 → PUT body（单一真源，防两处漂移）。"""
    return dict(line[2:].split(": ", 1) for line in _PREREG_BODY.splitlines())


def _patch_prereg_dir(monkeypatch, prereg_dir: Path, backtests: Path) -> None:
    monkeypatch.setattr(ops_api, "PREREGISTER_DIR", prereg_dir)
    monkeypatch.setattr(ops_api, "BACKTEST_RESULTS_DIR", backtests)


class TestPreregEndpoints:
    """GET/PUT /api/v1/ops/prereg:版本列表（有效/问题/锁定）+ 新建版本（校验前置）。"""

    def test_list_reports_valid_issues_and_locked(self, client, prereg_dir, tmp_path, monkeypatch):
        backtests = tmp_path / "bt"
        backtests.mkdir()
        _patch_prereg_dir(monkeypatch, prereg_dir, backtests)
        (prereg_dir / "2026-02-01-outcome-bad.md").write_text("- 主指标: x\n", encoding="utf-8")

        body = client.get(f"{BASE}/prereg").json()

        assert [Path(row["path"]).name for row in body] == [
            "2026-01-01-outcome-prereg.md",
            "2026-02-01-outcome-bad.md",
        ]
        row = body[0]
        assert set(row) == {"path", "fields", "valid", "issues", "locked"}
        assert row["valid"] is True and row["issues"] == [] and row["locked"] is False
        assert row["fields"]["MDE"].startswith("n=30")
        assert body[1]["valid"] is False and any("MDE" in i for i in body[1]["issues"])

    def test_lock_flips_when_backtest_report_references_version(
        self, client, prereg_dir, tmp_path, monkeypatch
    ):
        backtests = tmp_path / "bt"
        backtests.mkdir()
        _patch_prereg_dir(monkeypatch, prereg_dir, backtests)
        path = client.get(f"{BASE}/prereg").json()[0]["path"]
        assert client.get(f"{BASE}/prereg").json()[0]["locked"] is False

        (backtests / "formal-1.md").write_text(
            f"**status**: active\n**预登记**: {path}（valid）\n", encoding="utf-8"
        )
        assert client.get(f"{BASE}/prereg").json()[0]["locked"] is True

    def test_put_saves_new_version_and_audits(self, client, prereg_dir, tmp_path, monkeypatch):
        _patch_prereg_dir(monkeypatch, prereg_dir, tmp_path / "bt")
        previous = (prereg_dir / "2026-01-01-outcome-prereg.md").as_posix()
        history_before = sorted(p.name for p in prereg_dir.glob("*.md"))

        resp = client.put(f"{BASE}/prereg", json=_prereg_fields())

        assert resp.status_code == 201
        assert set(resp.json()) == {"path"}
        saved = Path(resp.json()["path"])
        assert saved.parent == prereg_dir and saved.exists()
        rows = client.get(f"{BASE}/prereg").json()
        assert rows[-1]["path"] == saved.as_posix()
        assert rows[-1]["valid"] is True and rows[-1]["locked"] is False
        # 历史版本一个字节不动
        assert sorted(p.name for p in prereg_dir.glob("*.md")) == sorted(
            [*history_before, saved.name]
        )
        assert (prereg_dir / "2026-01-01-outcome-prereg.md").as_posix() == previous
        # R6「编辑留审计」:config-change 行记旧版本 → 新版本
        audit = list_job_runs(ops_api.PREREG_AUDIT_JOB)
        assert audit and audit[0]["kind"] == "config-change"
        assert audit[0]["summary"]["from"] == previous
        assert audit[0]["summary"]["to"] == saved.as_posix()

    def test_put_rejects_missing_fields_without_writing(
        self, client, prereg_dir, tmp_path, monkeypatch
    ):
        _patch_prereg_dir(monkeypatch, prereg_dir, tmp_path / "bt")
        before = sorted(p.name for p in prereg_dir.glob("*.md"))

        resp = client.put(f"{BASE}/prereg", json={"主指标": "x"})

        assert resp.status_code == 422
        assert "缺字段" in resp.json()["detail"] and "MDE" in resp.json()["detail"]
        assert sorted(p.name for p in prereg_dir.glob("*.md")) == before
        assert list_job_runs(ops_api.PREREG_AUDIT_JOB) == []

    def test_put_rejects_bare_threshold_without_rationale(
        self, client, prereg_dir, tmp_path, monkeypatch
    ):
        _patch_prereg_dir(monkeypatch, prereg_dir, tmp_path / "bt")
        resp = client.put(f"{BASE}/prereg", json={**_prereg_fields(), "决策阈值": "0.5"})
        assert resp.status_code == 422
        assert "换算依据" in resp.json()["detail"]

    def test_put_refuses_locked_base_version_and_writes_nothing(
        self, client, prereg_dir, tmp_path, monkeypatch
    ):
        """spec「已有读数的预登记锁定」:基准版本已有读数 → 409 locked,磁盘零改动。"""
        backtests = tmp_path / "bt"
        backtests.mkdir()
        _patch_prereg_dir(monkeypatch, prereg_dir, backtests)
        base = (prereg_dir / "2026-01-01-outcome-prereg.md").as_posix()
        (backtests / "formal-1.md").write_text(
            f"**status**: active\n**预登记**: {base}（valid）\n", encoding="utf-8"
        )
        before = sorted(p.name for p in prereg_dir.glob("*.md"))

        resp = client.put(f"{BASE}/prereg", json={**_prereg_fields(), "base_path": base})

        assert resp.status_code == 409
        assert resp.json()["detail"] == "locked"
        assert sorted(p.name for p in prereg_dir.glob("*.md")) == before
        assert list_job_runs(ops_api.PREREG_AUDIT_JOB) == []

    def test_put_allows_unlocked_base_version(self, client, prereg_dir, tmp_path, monkeypatch):
        """基准版本无读数(未锁定)→ 照常新建版本(201),锁定只拦已产生读数的版本。"""
        backtests = tmp_path / "bt"
        backtests.mkdir()
        _patch_prereg_dir(monkeypatch, prereg_dir, backtests)
        base = (prereg_dir / "2026-01-01-outcome-prereg.md").as_posix()

        resp = client.put(f"{BASE}/prereg", json={**_prereg_fields(), "base_path": base})

        assert resp.status_code == 201
        saved = Path(resp.json()["path"])
        assert saved.parent == prereg_dir and saved.exists()
        assert saved.name != Path(base).name

    def test_put_without_base_path_keeps_create_new_version_semantics(
        self, client, prereg_dir, tmp_path, monkeypatch
    ):
        """缺省 base_path:不读任何历史版本(即便最新版本已锁定),保持「新建版本」语义。"""
        backtests = tmp_path / "bt"
        backtests.mkdir()
        _patch_prereg_dir(monkeypatch, prereg_dir, backtests)
        base = (prereg_dir / "2026-01-01-outcome-prereg.md").as_posix()
        (backtests / "formal-1.md").write_text(
            f"**status**: active\n**预登记**: {base}（valid）\n", encoding="utf-8"
        )

        resp = client.put(f"{BASE}/prereg", json=_prereg_fields())

        assert resp.status_code == 201
        assert Path(resp.json()["path"]).exists()


class TestCaliberEndpoints:
    """GET /caliber（只读当前旋钮）+ POST /caliber-draft（只出 delta 草稿）。"""

    def test_get_caliber_reads_constants_readonly(self, client):
        import evals.outcome.caliber as caliber

        before = (_LEDGER.read_bytes(), _CALIBER_CONST.read_bytes())
        body = client.get(f"{BASE}/caliber").json()
        assert set(body) == {"knobs", "source"}
        assert body["source"] == "evals/outcome/caliber.py"
        assert tuple(body["knobs"]) == KNOB_KEYS
        assert body["knobs"] == {key: getattr(caliber, key) for key in KNOB_KEYS}
        assert (_LEDGER.read_bytes(), _CALIBER_CONST.read_bytes()) == before

    def test_draft_created_then_refused_for_same_knob(self, client, tmp_path, monkeypatch):
        changes = tmp_path / "changes"
        monkeypatch.setattr(ops_api, "CALIBER_CHANGES_DIR", changes)
        ledger_before = _LEDGER.read_bytes()
        caliber_before = _CALIBER_CONST.read_bytes()

        resp = client.post(f"{BASE}/caliber-draft", json={"LEAKAGE_PROBE_THRESHOLD": 0.55})

        assert resp.status_code == 201
        assert set(resp.json()) == {"draft_dir"}
        draft = Path(resp.json()["draft_dir"])
        assert draft.is_dir() and draft.parent == changes
        assert (draft / "proposal.md").exists()
        assert (draft / "specs" / "evaluation" / "spec.md").exists()
        assert (draft / "metrics-timeline-line.md").exists()
        assert "0.55" in (draft / "proposal.md").read_text(encoding="utf-8")
        # 红线:台账与代码常量零改动
        assert _LEDGER.read_bytes() == ledger_before
        assert _CALIBER_CONST.read_bytes() == caliber_before
        # R6「编辑留审计」:旧值→新值
        audit = list_job_runs(ops_api.CALIBER_AUDIT_JOB)
        assert audit and audit[0]["kind"] == "config-change"
        assert audit[0]["summary"]["from"] == {"LEAKAGE_PROBE_THRESHOLD": 0.6}
        assert audit[0]["summary"]["to"] == {"LEAKAGE_PROBE_THRESHOLD": 0.55}

        again = client.post(f"{BASE}/caliber-draft", json={"LEAKAGE_PROBE_THRESHOLD": 0.5})
        assert again.status_code == 409
        assert again.json()["detail"] == "draft_exists"
        assert len(list(changes.iterdir())) == 1  # 第二次不落任何新目录

        other = client.post(f"{BASE}/caliber-draft", json={"NEUTRAL_BAND": 0.03})
        assert other.status_code == 201
        assert len(list(changes.iterdir())) == 2

    def test_draft_unknown_knob_refused(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(ops_api, "CALIBER_CHANGES_DIR", tmp_path / "changes")
        resp = client.post(f"{BASE}/caliber-draft", json={"NOT_A_KNOB": 1})
        assert resp.status_code == 422
        assert "NOT_A_KNOB" in resp.json()["detail"]
        assert list_job_runs(ops_api.CALIBER_AUDIT_JOB) == []


class TestDailyBrief:
    """add-auto-pool-analysis-daily-brief：每日汇报只读接口。"""

    def test_latest_returns_row(self, client):
        from finance_agent.outcome.daily_brief import upsert_daily_brief

        upsert_daily_brief(
            "2026-10-20", "ok", "# 日报\n## 今日结论", model="openai/deepseek-v4.1-flash"
        )
        resp = client.get(f"{BASE}/daily-brief/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["brief_date"] == "2026-10-20"
        assert body["markdown"].startswith("# 日报")
        assert body["model"] == "openai/deepseek-v4.1-flash"

    def test_by_date_returns_row(self, client):
        from finance_agent.outcome.daily_brief import upsert_daily_brief

        upsert_daily_brief("2026-10-19", "ok", "旧日报")
        resp = client.get(f"{BASE}/daily-brief", params={"date": "2026-10-19"})
        assert resp.status_code == 200
        assert resp.json()["markdown"] == "旧日报"

    def test_missing_returns_404(self, client):
        resp = client.get(f"{BASE}/daily-brief", params={"date": "1999-01-01"})
        assert resp.status_code == 404
        assert resp.json()["error"] == "daily_brief_not_found"

    def test_latest_missing_returns_404(self, client):
        resp = client.get(f"{BASE}/daily-brief/latest")
        assert resp.status_code == 404
        assert resp.json()["error"] == "daily_brief_not_found"


class TestAnalystScorecardEndpoint:
    """add-analyst-ex-post-verification：只读记分卡端点。"""

    def _db(self) -> Path:
        return Path(os.environ["RESEARCH_REPORTS_DB_PATH"])

    def test_empty_returns_empty_status(self, client):
        resp = client.get(f"{BASE}/research/analyst-scorecard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "empty"
        assert body["rows"] == []
        assert body["snapshot_date"] is None

    def test_filter_by_snapshot_and_bucket_type(self, client):
        from finance_agent.data import analyst_claims_store as store

        store.replace_scorecard(
            "2026-10-20",
            [
                {
                    "bucket_type": "org",
                    "bucket_key": "中信证券",
                    "n": 12,
                    "wins": 8,
                    "win_rate": 8 / 12,
                    "mean_excess": 0.03,
                    "median_excess": 0.02,
                    "reliable": False,
                    "weight": 0.8,
                    "tier": "1",
                },
                {
                    "bucket_type": "rating",
                    "bucket_key": "买入",
                    "n": 12,
                    "wins": 8,
                    "win_rate": 8 / 12,
                    "mean_excess": 0.03,
                    "median_excess": 0.02,
                    "reliable": False,
                    "weight": None,
                    "tier": None,
                },
            ],
            db_path=self._db(),
        )
        resp = client.get(
            f"{BASE}/research/analyst-scorecard",
            params={"snapshot_date": "2026-10-20", "bucket_type": "org"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["snapshot_date"] == "2026-10-20"
        assert [row["bucket_key"] for row in body["rows"]] == ["中信证券"]
        assert body["rows"][0]["reliable"] == 0


class TestForeignSentimentEndpoint:
    """add-foreign-broker-sentiment：只读情绪序列端点。"""

    def _db(self) -> Path:
        return Path(os.environ["RESEARCH_REPORTS_DB_PATH"])

    def test_empty(self, client):
        resp = client.get(f"{BASE}/research/foreign-sentiment")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "empty"
        assert body["rows"] == []

    def test_rows_and_counts(self, client):
        from finance_agent.data.foreign_broker_news import upsert_event

        upsert_event(
            {
                "event_key": "e1",
                "title": "UBS 上调中国银行股评级",
                "url": "https://example.invalid/a",
                "source": "example",
                "published_at": "2026-10-20",
                "broker": "UBS",
                "action": "rating_up",
                "target_type": "sector",
                "target": "银行",
                "direction": "positive",
                "summary": "瑞银上调中国银行股评级",
                "confidence": "high",
                "extraction_status": "ok",
            },
            db_path=self._db(),
        )
        resp = client.get(f"{BASE}/research/foreign-sentiment", params={"broker": "UBS"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["counts"]["total"] == 1
        assert body["counts"]["by_action"]["rating_up"] == 1
        assert body["counts"]["by_direction"]["positive"] == 1


class TestScorecardsUnified:
    """add-region-split-broker-scoring：内外资两块独立返回。"""

    def _db(self) -> Path:
        return Path(os.environ["RESEARCH_REPORTS_DB_PATH"])

    def test_empty_both_blocks(self, client):
        resp = client.get(f"{BASE}/research/scorecards")
        assert resp.status_code == 200
        body = resp.json()
        assert body["domestic"]["status"] == "empty"
        assert body["foreign"]["status"] == "empty"
        assert body["domestic"]["score_family"] == "event_study"
        assert body["foreign"]["score_family"] == "sentiment"

    def test_blocks_keep_separate_metrics(self, client):
        from finance_agent.data import analyst_claims_store as store
        from finance_agent.outcome.foreign_broker_scoring import persist_foreign_scorecard

        store.replace_scorecard(
            "2026-10-20",
            [
                {
                    "bucket_type": "org",
                    "bucket_key": "中信证券",
                    "n": 12,
                    "wins": 8,
                    "win_rate": 8 / 12,
                    "mean_excess": 0.03,
                    "median_excess": 0.02,
                    "reliable": False,
                    "weight": 0.8,
                    "tier": "1",
                    "region": "domestic",
                    "score_family": "event_study",
                    "weighted_mean_excess": 0.04,
                    "deep_n": 5,
                }
            ],
            db_path=self._db(),
        )
        persist_foreign_scorecard(
            "2026-10-20",
            [
                {
                    "bucket_type": "broker",
                    "bucket_key": "UBS",
                    "n": 6,
                    "hits": 4,
                    "hit_rate": 4 / 6,
                    "mean_return": 0.01,
                    "net_score": 0.5,
                    "reliable": False,
                }
            ],
            db_path=self._db(),
        )
        resp = client.get(f"{BASE}/research/scorecards")
        assert resp.status_code == 200
        body = resp.json()
        assert body["domestic"]["status"] == "ok"
        assert body["foreign"]["status"] == "ok"
        domestic = body["domestic"]["rows"][0]
        foreign = body["foreign"]["rows"][0]
        assert domestic["win_rate"] is not None
        assert domestic["weighted_mean_excess"] == 0.04
        assert "hit_rate" not in domestic
        assert foreign["hit_rate"] is not None
        assert foreign["net_score"] == 0.5
        assert "win_rate" not in foreign
