"""Task 4(delta add-eval-ops-console):回测批 / 探针 / 健康检查的进程内封装。

全部离线:假 client / 假 replay / 假 llm(复用 Δ4 离线驱动的夹具思路),零网络零 LLM。

断言「薄编排、零复制」的可见证据:
- formal 批门禁顺序 = 预登记 → 抽样 → 干净窗口 →(批级探针)→ 回放,门禁不过**在任何
  回放与逐标的取数之前**就抛错(CleanWindowError / MissingPreregistrationError);
- 报告由既有 ``evals.backtest.run_backtest`` 产出(五键披露齐备、md 自校通过、status 头可解析),
  本层只做「取数 → 门禁 → 跑既有编排 → 落盘 → 返回 dict」;
- 探针读数三态(measurable / downgraded / unmeasurable)如实透传,0 与 None 不可混读;
- 健康检查门禁读数缺失为 None(「无读数」),JSON 可序列化。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from evals.causal_ablation.preregister import (
    OUTCOME_REQUIRED_FIELDS,
    MissingPreregistrationError,
    parse_preregister,
)
from evals.outcome.caliber import PRIMARY_WINDOW_DAYS

from finance_agent.outcome.ops import batches

# ── 夹具(与 tests/evals/backtest/test_run_backtest.py 同款思路)──

_GRID_START = "2024-01-02"
_BATCH_CODES = ["600000", "600001", "600002"]


def _grid(n: int) -> list[str]:
    return pd.date_range(_GRID_START, periods=n, freq="B").strftime("%Y-%m-%d").tolist()


def _kline(closes: list[float], dates: list[str] | None = None) -> pd.DataFrame:
    day_list = dates or _grid(len(closes))
    return pd.DataFrame(
        {
            "日期": day_list,
            "开盘": closes,
            "收盘": closes,
            "最高": [c * 1.01 for c in closes],
            "最低": [c * 0.99 for c in closes],
        }
    )


def _closes(n: int, up: float, down: float, start: float = 10.0) -> list[float]:
    """涨跌交替(有限 Sharpe,规避 sanity invalid 分支)。"""
    out = [start]
    for i in range(1, n):
        out.append(out[-1] * (1.0 + (up if i % 2 else down)))
    return out


def _segmented_index() -> pd.DataFrame:
    """三段行情(bull 150 / bear 120 / 平坦 180)指数:供真实 ``stratified_sample`` 用。"""
    closes: list[float] = []
    value = 100.0
    for _ in range(150):
        value *= 1.0035
        closes.append(value)
    for _ in range(120):
        value *= 0.9965
        closes.append(value)
    closes.extend([value] * 180)
    return _kline(closes)


class _FakeClient:
    """AKShareClient 最小替身(签名对齐;记录逐标的取数调用)。"""

    def __init__(self, *, index: pd.DataFrame | None = None, stock: pd.DataFrame | None = None):
        self._index = index
        self._stock = stock
        self.kline_calls: list[tuple[str, str]] = []
        self.index_calls = 0

    def fetch_index_kline(self, _code: str, days: int | None = None) -> pd.DataFrame:
        self.index_calls += 1
        frame = (
            self._index if self._index is not None else _kline(_closes(days or 450, 0.014, -0.010))
        )
        return frame

    def fetch_kline(
        self, stock_code: str, days: int | None = None, *, adjust: str = "qfq"
    ) -> pd.DataFrame:
        self.kline_calls.append((stock_code, adjust))
        if self._stock is not None:
            return self._stock
        return _kline(_closes(days or 450, 0.014, -0.010))

    def fetch_news(self, _stock_code: str, limit: int = 20) -> list[dict]:
        return [{"title": "公司发布日常经营公告"}]


def _fake_replay(action: str = "buy", hold: int = 10):
    """(code, decision_date) → 与 replay_with_consistency 同形态的结果 dict(按帧取价)。"""

    def replay(
        code: str,
        decision_date: str,
        *,
        n: int = 3,
        full_kline: pd.DataFrame | None = None,
        full_benchmark: pd.DataFrame | None = None,
    ) -> dict:
        assert full_kline is not None
        dates = full_kline["日期"].astype(str).str[:10].tolist()
        closes = full_kline["收盘"].astype(float).tolist()
        idx = dates.index(str(decision_date)[:10])
        settle_idx = min(idx + hold, len(closes) - 1)
        return {
            "code": code,
            "decision_date": str(decision_date)[:10],
            "actions": [action] * n,
            "agreement": 1.0,
            "settlement": {
                "status": "expired",
                "settle_date": dates[settle_idx],
                "settle_price": closes[settle_idx],
                "hold_days": hold,
                "decision_return": None,
                "benchmark_return": None,
                "decision_excess": None,
                "decision_hit": None,
            },
            "entry_price": closes[idx],
            "action": action,
            "snapshot_metadata": {},
        }

    return replay


def _valid_preregistration():
    text = "\n".join(
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
    return parse_preregister(text, required_fields=OUTCOME_REQUIRED_FIELDS)


def _probe(*, rate: float | None, downgraded: bool = False) -> dict[str, Any]:
    return {
        "probe_n": 10,
        "questions_per_ticker": 3,
        "direction_hit_rate": rate,
        "magnitude_hit_rate": 0.4,
        "event_hit_rate": 0.5,
        "unknown_ratio": 0.1,
        "threshold": 0.60,
        "downgraded": downgraded,
        "details": [],
    }


def _raiser(exc: BaseException, recorder: list[str] | None = None, label: str = ""):
    """先记录再抛的假门禁函数(用于钉死门禁顺序)。"""

    def fn(*_a, **_k):
        if recorder is not None:
            recorder.append(label)
        raise exc

    return fn


def _md_and_json(tmp_path: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """报告落盘路径(md/json) → 绝对路径(路径为仓库相对,as_posix 分隔)。"""
    return tmp_path / report["report_paths"]["md"], tmp_path / report["report_paths"]["json"]


# ── formal 批门禁 ──


class TestFormalGates:
    def test_gate_order_and_no_replay_before_clean_window(self, monkeypatch):
        """门禁顺序:预登记 → 抽样 → 干净窗口;不过 → 抛错且**无回放、无逐标的取数**。"""
        order: list[str] = []
        monkeypatch.setattr(
            batches,
            "assert_preregistered",
            lambda *a, **k: order.append("preregister") or _valid_preregistration(),
        )
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            lambda *a, **k: (
                order.append("sample")
                or [{"code": "600000", "regime": "bull", "decision_date": "2024-03-01"}]
            ),
        )
        monkeypatch.setattr(
            batches,
            "assert_clean_window",
            lambda *a, **k: (
                order.append("clean_window")
                or {"passed": False, "reason": "决策日 2024-03-01 距 as_of 仅 3 个交易日"}
            ),
        )
        client = _FakeClient()

        def replay(*_a, **_k):  # pragma: no cover - 门禁不过不得走到这里
            order.append("replay")
            raise AssertionError("门禁未过却启动了回放")

        with pytest.raises(batches.CleanWindowError, match="3 个交易日"):
            batches.run_backtest_task(
                batch_kind="formal",
                codes=["600000"],
                per_regime=1,
                client=client,
                replay_fn=replay,
            )
        assert order == ["preregister", "sample", "clean_window"]
        assert client.kline_calls == []  # 逐标的取数也在门禁之后

    def test_missing_preregistration_propagates_before_sampling(self, monkeypatch):
        """无有效预登记:预登记异常原样上抛(端点翻 409),连抽样都不做。"""
        calls: list[str] = []
        monkeypatch.setattr(
            batches,
            "assert_preregistered",
            _raiser(MissingPreregistrationError("未找到预登记文档"), calls, "preregister"),
        )
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            lambda *a, **k: calls.append("sample") or [],
        )
        with pytest.raises(MissingPreregistrationError, match="未找到预登记文档"):
            batches.run_backtest_task(batch_kind="formal", codes=["600000"], client=_FakeClient())
        assert calls == ["preregister"]

    def test_formal_passes_gates_then_runs_probe(self, monkeypatch, tmp_path):
        """门禁通过后跑批级探针,报告为 formal 身份 + 预登记有效 + 干净窗口通过。"""
        monkeypatch.chdir(tmp_path)  # 报告落 tmp(不得污染仓库 evals/backtest/results)
        order: list[str] = []
        monkeypatch.setattr(
            batches,
            "assert_preregistered",
            lambda *a, **k: order.append("preregister") or _valid_preregistration(),
        )
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            lambda *a, **k: (
                order.append("sample")
                # 深历史执法（#172）：skill 定位断言需切点后决策日
                or [{"code": "600000", "regime": "bull", "decision_date": "2025-06-02"}]
            ),
        )
        monkeypatch.setattr(
            batches,
            "assert_clean_window",
            lambda *a, **k: order.append("clean_window") or {"passed": True, "reason": "ok"},
        )
        monkeypatch.setattr(
            batches,
            "run_batch_probe",
            lambda *a, **k: order.append("probe") or _probe(rate=0.3),
        )
        report = batches.run_backtest_task(
            batch_kind="formal",
            codes=["600000"],
            per_regime=1,
            as_of="2024-12-31",
            client=_FakeClient(),
            replay_fn=_fake_replay(),
        )
        assert order == ["preregister", "sample", "clean_window", "probe"]
        assert report["batch_kind"] == "formal"
        assert report["preregister"]["valid"] is True
        assert report["clean_window"]["passed"] is True
        assert report["leakage_probe"]["direction_hit_rate"] == 0.3
        assert report["positioning"] == "skill"

    def test_prepared_plan_is_reused_without_re_gating(self, monkeypatch, tmp_path):
        """端点已同步过门禁时透传 prepared:不重复抽样/取指数(只跑回放)。"""
        monkeypatch.chdir(tmp_path)  # 报告落 tmp(不得污染仓库 evals/backtest/results)
        sample = [{"code": "600000", "regime": "bull", "decision_date": "2024-03-01"}]
        prepared = {
            "batch_kind": "formal",
            "as_of": "2024-12-31",
            "codes": ["600000"],
            "sample": sample,
            "index_kline": _segmented_index(),
            "preregistration": _valid_preregistration(),
            "clean_window": {"passed": True, "reason": "ok"},
        }
        monkeypatch.setattr(
            batches, "stratified_sample", lambda *a, **k: pytest.fail("不得重复抽样")
        )
        monkeypatch.setattr(
            batches, "assert_preregistered", lambda *a, **k: pytest.fail("不得重复门禁")
        )
        monkeypatch.setattr(batches, "run_batch_probe", lambda *a, **k: _probe(rate=0.3))
        client = _FakeClient()
        report = batches.run_backtest_task(
            batch_kind="formal",
            codes=["600000"],
            client=client,
            replay_fn=_fake_replay(),
            prepared=prepared,
        )
        assert client.index_calls == 0
        assert report["n_sample"] == 1
        assert report["preregister"]["valid"] is True


# ── pathway 批与落盘 ──


class TestPathwayBatch:
    def _run(self, monkeypatch, tmp_path, *, pool: list[str] | None = None):
        monkeypatch.chdir(tmp_path)  # md 落 evals/backtest/results、json 落 reports/backtest
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            lambda *a, **k: [{"code": "600000", "regime": "bull", "decision_date": _grid(60)[30]}],
        )
        return batches.run_backtest_task(
            batch_kind="pathway",
            codes=pool or ["600000"],
            per_regime=1,
            client=_FakeClient(),
            replay_fn=_fake_replay(),
        )

    def test_returns_report_and_writes_md_json(self, monkeypatch, tmp_path):
        report = self._run(monkeypatch, tmp_path)
        assert report["batch_kind"] == "pathway"
        assert report["positioning"] == "pathway"
        assert report["conclusion"].startswith("通路验证定位")
        # 五键披露齐备(既有 evals 实现产出,非本层新造)
        for key in (
            "batch_kind",
            "preregister",
            "clean_window",
            "leakage_probe",
            "regime_coverage",
        ):
            assert key in report
        md_path, json_path = _md_and_json(tmp_path, report)
        assert report["report_paths"]["md"].startswith("evals/backtest/results/pathway-")
        assert md_path.exists() and json_path.exists()
        # md 自校通过(render_backtest_report_md 内部 assert_outcome_report)+ status 头可解析
        from evals.causal_ablation.report_status import parse_status

        status, target = parse_status(md_path.read_text(encoding="utf-8"))
        assert status == "active" and target is None
        # JSON 全量报告落盘(与 summary 裁剪版互补)
        assert json.loads(json_path.read_text(encoding="utf-8"))["n_sample"] == 1

    def test_report_written_for_real_stratified_sample(self, monkeypatch, tmp_path):
        """真实 stratified_sample:三段行情指数 → 三 regime 各取满样本,报告照常产出。"""
        monkeypatch.chdir(tmp_path)
        index = _segmented_index()
        frames = {code: _kline(_closes(len(index), 0.014, -0.010)) for code in _BATCH_CODES}

        class _PerCodeClient(_FakeClient):
            def fetch_kline(self, stock_code, days=None, *, adjust="qfq"):
                self.kline_calls.append((stock_code, adjust))
                return frames[stock_code]

        report = batches.run_backtest_task(
            batch_kind="pathway",
            codes=list(_BATCH_CODES),
            per_regime=3,
            client=_PerCodeClient(index=index),
            replay_fn=_fake_replay(),
        )
        assert report["n_sample"] == 9  # 3 regime × 3 标的
        assert sorted(report["regime_coverage"]["covered"]) == ["bear", "bull", "sideways"]
        assert report["positioning"] == "pathway"
        assert report["perf_table"]["system"]["Sharpe"] is not None

    def test_report_name_is_unique_per_run(self, monkeypatch, tmp_path):
        """同秒二次发起不得覆盖上一次报告(报告名消歧后两份都在)。"""
        first = self._run(monkeypatch, tmp_path)
        second = self._run(monkeypatch, tmp_path)
        assert first["report_paths"]["md"] != second["report_paths"]["md"]
        md_dir = tmp_path / "evals" / "backtest" / "results"
        assert len(list(md_dir.glob("pathway-*.md"))) == 2
        assert len(list((tmp_path / "reports" / "backtest").glob("pathway-*.json"))) == 2


# ── 探针单跑 ──


class TestProbeTask:
    def _client(self) -> _FakeClient:
        # 标的价格序列 == 指数序列 → 区间相对涨跌 = 0(方向真值 down),事件真值「无」
        frame = _kline(_closes(60, 0.014, -0.010))
        return _FakeClient(index=frame, stock=frame)

    def test_refusal_is_unmeasurable_not_zero(self):
        out = batches.run_probe_task(
            codes=["600000"],
            decision_date=_grid(60)[10],
            client=self._client(),
            llm=lambda _prompt: "我不确定",
        )
        assert out["state"] == "unmeasurable"
        assert out["direction_hit_rate"] is None
        assert out["unknown_ratio"] == 1.0
        assert out["downgraded"] is False

    def test_wrong_answer_is_measurable_zero(self):
        out = batches.run_probe_task(
            codes=["600000"],
            decision_date=_grid(60)[10],
            client=self._client(),
            llm=lambda _prompt: json.dumps({"direction": "up"}),
        )
        assert out["state"] == "measurable"
        assert out["direction_hit_rate"] == 0.0  # 0（全答错）与 None（不可测）严格可分
        assert out["threshold"] == 0.60

    def test_correct_answer_is_downgraded(self):
        out = batches.run_probe_task(
            codes=["600000"],
            decision_date=_grid(60)[10],
            client=self._client(),
            llm=lambda _prompt: json.dumps({"direction": "down"}),
        )
        assert out["state"] == "downgraded"
        assert out["direction_hit_rate"] == 1.0
        assert out["downgraded"] is True

    def test_passes_window_and_sampling_params(self):
        frame = _kline(_closes(200, 0.014, -0.010))
        out = batches.run_probe_task(
            codes=["600000", "600001"],
            decision_date=_grid(200)[10],
            window_days=5,
            n_tickers=1,
            seed=7,
            client=_FakeClient(index=frame, stock=frame),
            llm=lambda _prompt: "n/a",
        )
        assert out["probe_n"] == 1
        assert out["state"] == "unmeasurable"

    def test_synthesizes_single_window_disclosure_from_request(self):
        """``run_leakage_probe`` 不返回窗口字段:单跑按请求入参合成一条窗口披露。

        形状对齐 ``report.aggregate_probes`` 的逐窗口条目(每项带**它自己**的窗口 + 读数),
        使运行历史与批报告的窗口字段同形;单窗口的 ``window_days`` 随条目披露。
        """
        decision_date = _grid(60)[10]
        out = batches.run_probe_task(
            codes=["600000"],
            decision_date=decision_date,
            window_days=5,
            client=self._client(),
            llm=lambda _prompt: "我不确定",
        )
        assert out["probe_window"] == [decision_date]
        assert isinstance(out["per_window"], list) and len(out["per_window"]) == 1
        entry = out["per_window"][0]
        assert entry["probe_window"] == [decision_date]
        assert entry["window_days"] == 5
        assert entry["state"] == out["state"] == "unmeasurable"
        assert entry["direction_hit_rate"] is None
        assert entry["probe_n"] == out["probe_n"]
        assert "details" not in entry  # 逐题明细不进窗口条目(体积大且非披露面)


# ── 健康检查 ──


class TestHealthTask:
    def test_missing_db_reports_no_reading_and_is_serializable(self, tmp_path):
        out = batches.run_health_task(db_path=tmp_path / "x.db")
        json.dumps(out, ensure_ascii=False)  # 不得含不可序列化对象
        assert out["available"] is False
        assert out["passed"] is None
        assert out["gates"] and all(g["value"] is None for g in out["gates"])
        assert all(g["passed"] is None for g in out["gates"])
        assert "DB 不存在" in out["error"]

    def test_db_without_predictions_table_reports_no_reading(self, tmp_path):
        """DB 文件存在但缺 predictions 表(空库/半初始化库):同「无读数」形状,不抛不 500。"""
        import sqlite3

        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()  # 零字节库:文件在,表不在

        out = batches.run_health_task(db_path=db)

        json.dumps(out, ensure_ascii=False)
        assert out["available"] is False
        assert out["passed"] is None and out["readings"] is None
        assert out["gates"] and all(g["value"] is None for g in out["gates"])
        assert all(g["passed"] is None for g in out["gates"])
        assert "无读数" in out["error"]
        assert "predictions" in out["error"]

    def test_empty_db_gates_fail_without_reading(self, tmp_path):
        from finance_agent.outcome.track_record.model import init_predictions

        db = tmp_path / "h.db"
        init_predictions(db)
        out = batches.run_health_task(db_path=db)
        json.dumps(out, ensure_ascii=False)
        assert out["available"] is True
        assert {g["id"] for g in out["gates"]} == {
            "settlement_success",
            "unresolvable",
            "integrity",
            "bookkeeping",
        }
        settlement = next(g for g in out["gates"] if g["id"] == "settlement_success")
        assert settlement["value"] is None and settlement["passed"] is False
        assert "无读数" in settlement["reason"]
        assert out["passed"] is False

    def test_settled_sample_passes_gates(self, tmp_path):
        from finance_agent.outcome.track_record.model import (
            init_predictions,
            init_track_record_tables,
            insert_prediction,
            update_prediction_status,
        )

        db = tmp_path / "h.db"
        init_predictions(db)
        init_track_record_tables(db)
        pid = insert_prediction(
            {
                "source_type": "live",
                "symbol": "600519.SH",
                "symbol_name": "贵州茅台",
                "direction": "long",
                "entry_price": 100.0,
                "horizon_days": PRIMARY_WINDOW_DAYS,
                "confidence": 0.8,
                "rationale_snapshot": {"action": "buy"},
                "created_at": "2026-09-01T10:00:00",
            },
            db_path=db,
        )
        update_prediction_status(
            pid, {"status": "resolved_win", "raw_return": 0.1, "excess_return": 0.05}, db_path=db
        )
        out = batches.run_health_task(db_path=db)
        settlement = next(g for g in out["gates"] if g["id"] == "settlement_success")
        assert settlement["value"] == 1.0 and settlement["passed"] is True
        assert out["passed"] is True
        assert out["readings"]["total"] == 1
        bookkeeping = next(g for g in out["gates"] if g["id"] == "bookkeeping")
        assert bookkeeping["value"] == 1.0 and bookkeeping["passed"] is None  # 披露项不阻断


# ── 与 CLI / 端点的一致性护栏 ──


class TestConsistencyGuards:
    def test_dirs_and_gate_constants_match_cli(self):
        """目录与门禁常量取自既有 CLI 模块,不得各自另定义(防口径漂移)。"""
        import inspect

        import evals.backtest.run_backtest as rb

        assert batches.PREREGISTER_DIR == rb.PREREGISTER_DIR
        assert batches.PREREGISTER_NAME_CONTAINS == rb.PREREGISTER_NAME_CONTAINS
        assert batches.MD_REPORT_DIR == rb.MD_REPORT_DIR
        assert batches.JSON_REPORT_DIR == rb.JSON_REPORT_DIR
        assert batches.BATCH_KINDS == rb.BATCH_KINDS
        # 主评估窗口取口径常量(不另写 20)
        assert batches.PRIMARY_WINDOW_DAYS == PRIMARY_WINDOW_DAYS
        assert (
            inspect.signature(batches.run_probe_task).parameters["window_days"].default
            == PRIMARY_WINDOW_DAYS
        )

    def test_unknown_batch_kind_rejected(self):
        with pytest.raises(ValueError, match="未知批次类型"):
            batches.run_backtest_task(batch_kind="nope", codes=["600000"], client=_FakeClient())

    def test_ops_api_resolves_real_impls(self):
        """端点经 ``_resolve_batch_func`` 解析到的就是本模块实现(接线护栏)。"""
        from finance_agent import ops_api

        for name in ("prepare_backtest", "run_backtest_task", "run_probe_task", "run_health_task"):
            assert ops_api._resolve_batch_func(name) is getattr(batches, name)
        assert ops_api._is_clean_window_refusal(batches.CleanWindowError("x")) is True
        assert ops_api._is_clean_window_refusal(ValueError("x")) is False

    def test_prepare_backtest_only_reads(self, monkeypatch, tmp_path):
        """前置门禁/取数只读:不落报告、不建目录(失败路径不留半成品)。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            batches,
            "stratified_sample",
            lambda *a, **k: [{"code": "600000", "regime": "bull", "decision_date": "2024-03-01"}],
        )
        prepared = batches.prepare_backtest(
            batch_kind="pathway", codes=["600000"], per_regime=1, client=_FakeClient()
        )
        assert len(prepared["sample"]) == 1
        assert prepared["clean_window"] is None and prepared["preregistration"] is None
        assert len(prepared["index_kline"]) > 0
        assert not (tmp_path / "evals").exists()
        assert not (tmp_path / "reports").exists()
