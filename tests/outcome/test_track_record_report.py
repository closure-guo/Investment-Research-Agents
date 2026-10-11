"""结算报告消费端测试（issue #250：collect/build/render，口径 §1.9-v2）。"""

import pytest

from finance_agent.outcome.track_record.model import (
    init_predictions,
    insert_prediction,
    update_prediction_status,
)
from finance_agent.outcome.track_record.report import (
    build_report_data,
    collect_settled_day_masters,
    render_marketing_report,
)

BASE = {
    "source_type": "live",
    "symbol": "600519.SH",
    "direction": "long",
    "rationale_snapshot": {"markdown": "x"},
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "report.db"
    init_predictions(path)
    return path


def _insert(db, **overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return insert_prediction(rec, db_path=db)


def _settle(db, pid, **overrides):
    """把观点置为已结算（resolution_rule 非空）；overrides 覆盖默认 resolved 字段。"""
    resolved = {
        "status": "resolved_win",
        "resolved_at": "2026-11-03T15:00:00",
        "excess_return": 0.05,
        "resolution_rule": "expiry",
    }
    resolved.update(overrides)
    update_prediction_status(pid, resolved, db_path=db)


class TestCollectSettledDayMasters:
    def test_filters_duplicate_of_day_rows(self, db):
        """duplicate_of_day 行（同日重复关闭行）不进结算报告——§1.9-v2 防御过滤。"""
        keep = _insert(db)
        dup = _insert(db, symbol="000001.SZ")
        _settle(db, keep)
        _settle(
            db,
            dup,
            status="duplicate_of_day",
            excess_return=None,
            resolved_at=None,
            resolution_rule="duplicate_of_day",
        )
        settled = collect_settled_day_masters(db_path=db)
        assert [r["prediction_id"] for r in settled] == [keep]

    def test_excludes_unsettled_open_rows(self, db):
        """未结算 open 行（resolution_rule 为空）不进结算报告。"""
        settled_id = _insert(db)
        open_id = _insert(db, symbol="000001.SZ")
        _settle(db, settled_id)
        settled = collect_settled_day_masters(db_path=db)
        assert [r["prediction_id"] for r in settled] == [settled_id]
        assert open_id not in {r["prediction_id"] for r in settled}

    def test_keeps_unresolvable_rows_for_downstream_filtering(self, db):
        """unresolvable 行（resolution_rule='stale_no_market'）保留——状态级过滤属下游职责。"""
        pid = _insert(db)
        _settle(
            db,
            pid,
            status="unresolvable",
            resolved_at=None,
            excess_return=None,
            resolution_rule="stale_no_market",
        )
        settled = collect_settled_day_masters(db_path=db)
        assert [r["prediction_id"] for r in settled] == [pid]

    def test_reads_beyond_default_list_limit(self, db):
        """全量读取：行数超过 list_predictions 默认 limit=50 时不截断。"""
        for i in range(60):
            pid = _insert(db, symbol=f"60051{i % 10}.SH")
            _settle(db, pid)
        assert len(collect_settled_day_masters(db_path=db)) == 60

    def test_empty_db_returns_empty_list(self, db):
        """空库返回空列表，不抛异常。"""
        assert collect_settled_day_masters(db_path=db) == []


def _row(
    i: int,
    direction: str = "long",
    excess: float | None = 0.02,
    day: str = "2026-10-09",
    status: str = "resolved_win",
    symbol: str | None = None,
):
    """构造已结算日主行（build_report_data 纯函数输入，不经 DB）。"""
    return {
        "prediction_id": f"p{i}",
        "symbol": symbol or f"6{i:05d}.SH",
        "direction": direction,
        "status": status,
        "resolved_at": None if status == "unresolvable" else "2026-11-06T15:00:00",
        "excess_return": excess,
        "created_at": f"{day}T10:00:00",
        "resolution_rule": "expiry",
    }


BENCH = {"2026-10-09": 100.0, "2026-10-10": 100.0, "2026-11-06": 100.0}
CLOSES = {
    "600519": {"2026-10-09": 10.0, "2026-10-10": 10.0, "2026-11-06": 11.0},
    "000001": {"2026-10-09": 10.0, "2026-10-10": 10.0, "2026-11-06": 11.0},
}
UNIVERSE_BY_DAY = {
    "2026-10-09": ["600519", "000001"],
    "2026-10-10": ["600519", "000001"],
}
# 六标的池：⚠1/⚠2 用例需要 ≥6 槽/日 且避免有放回回退噪声
POOL6 = ["600519", "000001", "600030", "601818", "300750", "600036"]
CLOSES6 = {s: {"2026-10-09": 10.0, "2026-10-10": 10.0, "2026-11-06": 11.0} for s in POOL6}
UBD6 = {"2026-10-09": list(POOL6), "2026-10-10": list(POOL6)}


class TestBuildReportData:
    def test_red_line_nine_samples_blocks_monte_carlo(self):
        """红线：可判定样本 9<10 → 零模型不产出，reason 引用 §1.9-v2 与样本数。"""
        settled = [_row(i, direction="long" if i % 2 == 0 else "short") for i in range(9)]
        data = build_report_data(
            settled, UNIVERSE_BY_DAY, CLOSES, BENCH, "2026-11-06", n_sims=200, seed=42
        )
        assert data["caliber"] == "§1.9-v2"
        assert data["sample"] == 9
        mc = data["monte_carlo"]
        assert mc["available"] is False
        assert "样本 9<10" in mc["reason"] and "§1.9-v2" in mc["reason"]
        assert "quantile" not in mc  # 降级分支不产出任何分位/点估计读数
        assert "ic_series" in data and "icir" in data and "avoidance_series" in data

    def test_sample_counts_only_rows_with_excess_reading(self):
        """可判定样本口径：excess_return 为空的 long/short 行（unresolvable 等）不计入。

        10 行 long/short 中 2 行无超额读数 → sample=8 → 红线触发（8<10）。
        """
        settled = [_row(i, direction="long" if i % 2 == 0 else "short") for i in range(8)] + [
            _row(8, direction="long", excess=None, status="unresolvable"),
            _row(9, direction="short", excess=None, status="unresolvable"),
        ]
        data = build_report_data(
            settled, UNIVERSE_BY_DAY, CLOSES, BENCH, "2026-11-06", n_sims=200, seed=42
        )
        assert data["sample"] == 8
        assert data["monte_carlo"]["available"] is False
        assert "样本 8<10" in data["monte_carlo"]["reason"]

    def test_empty_market_panel_degrades_mc_without_point_estimate(self):
        """M1：sample≥10 但行情面板整体不可得（closes 空）→ available=False + reason。

        无分位不出现点估计：mc 段不含 real_excess/quantile 键；render 不抛异常、
        不渲染「跑赢」与槽位超额点估计。
        """
        settled = [_row(i, direction="long" if i % 2 == 0 else "short") for i in range(12)]
        data = build_report_data(
            settled,
            {"2026-10-09": ["600519", "000001"]},  # 池键齐全，但 closes 全空 = 面板整体失败
            {},
            BENCH,
            "2026-11-06",
            n_sims=200,
            seed=42,
        )
        assert data["sample"] == 12
        mc = data["monte_carlo"]
        assert mc["available"] is False
        assert "无有效模拟轮次" in mc["reason"]
        assert "quantile" not in mc and "real_excess" not in mc
        text = render_marketing_report({**data, "as_of": "2026-11-03"})
        assert "跑赢" not in text and "平均槽位超额" not in text
        assert "无有效模拟轮次" in text

    def test_superseded_non_master_row_excluded_from_sample_and_real_excess(self):
        """⚠1 第三道防御：同 (symbol, 归属日) 非日主行（superseded 旧行）不入任何统计。

        旧行（superseded 已结算，excess=0.99 毒值）与 open 新行同 (symbol, 归属日)
        ——日主 = created_at 更晚的新行；sample 只计日主可判定行，real_excess
        不被毒值污染（§1.9-v2：全部读数仅消费日主观点）。
        """
        settled = (
            [
                _row(
                    i,
                    direction="long" if i % 2 == 0 else "short",
                    day="2026-10-09",
                    symbol=f"{POOL6[i]}.SH",
                )
                for i in range(5)
            ]
            + [
                {
                    "prediction_id": "p_old",
                    "symbol": "600099.SH",
                    "direction": "long",
                    "status": "resolved_win",
                    "resolved_at": "2026-11-06T15:00:00",
                    "excess_return": 0.99,
                    "created_at": "2026-10-09T09:00:00",
                    "resolution_rule": "superseded",
                },
                {
                    "prediction_id": "p_new",
                    "symbol": "600099.SH",
                    "direction": "long",
                    "status": "open",
                    "resolved_at": None,
                    "excess_return": None,
                    "created_at": "2026-10-09T14:00:00",
                    "resolution_rule": None,
                },
            ]
            + [
                _row(
                    i + 5,
                    direction="short" if i % 2 == 0 else "long",
                    day="2026-10-10",
                    symbol=f"{POOL6[i]}.SH",
                )
                for i in range(6)
            ]
        )
        data = build_report_data(settled, UBD6, CLOSES6, BENCH, "2026-11-06", n_sims=200, seed=42)
        # 13 行输入 → 日主 12 行（p_old 非日主剔除）→ 可判定 11 行（p_new open 无读数）
        assert data["sample"] == 11
        mc = data["monte_carlo"]
        assert mc["available"] is True
        assert abs(mc["real_excess"] - 0.02) < 1e-9  # 毒值 0.99 未入均值

    def test_slot_symbol_outside_day_pool_logs_warning(self, caplog):
        """⚠2 观测：真实槽 symbol（归一化去后缀后）不在归属日池内 → warning。

        池外选股使零模型不同框——首批可忽略但要有观测；在池内行不告警。
        """
        import logging

        settled = (
            [
                _row(
                    i,
                    direction="long" if i % 2 == 0 else "short",
                    day="2026-10-09",
                    symbol=f"{POOL6[i]}.SH",
                )
                for i in range(5)
            ]
            + [
                _row(
                    i + 5,
                    direction="short" if i % 2 == 0 else "long",
                    day="2026-10-10",
                    symbol=f"{POOL6[i]}.SH",
                )
                for i in range(4)
            ]
            + [
                _row(9, direction="long", day="2026-10-10", symbol="688888.SH")  # 池外
            ]
        )
        with caplog.at_level(logging.WARNING):
            data = build_report_data(
                settled, UBD6, CLOSES6, BENCH, "2026-11-06", n_sims=200, seed=42
            )
        assert data["sample"] == 10
        assert data["monte_carlo"]["available"] is True  # 观测不阻断
        outside = [r for r in caplog.records if "不同框" in r.message]
        assert len(outside) == 1  # 仅 688888.SH（归一化后不在日池）

    def test_ten_samples_produce_real_excess_quantile_and_caliber(self):
        """正常分支：10 条可判定行 → 真实超额等权均值 + 分位/p 值 + caliber 字段。

        构造：真实超额 9×0.02 + 1×0.06 → 均值 0.024；零模型每槽 raw=10%、基准 0%
        → 模拟值恒 0.1 > 0.024 → quantile=0.0、p=(200+1)/(200+1)=1.0（确定性）。
        """
        settled = (
            [
                _row(i, direction="long" if i % 2 == 0 else "short", day="2026-10-09")
                for i in range(5)
            ]
            + [
                _row(i + 5, direction="short" if i % 2 == 0 else "long", day="2026-10-10")
                for i in range(4)
            ]
            + [_row(9, direction="long", excess=0.06, day="2026-10-10")]
        )
        data = build_report_data(
            settled, UNIVERSE_BY_DAY, CLOSES, BENCH, "2026-11-06", n_sims=200, seed=42
        )
        assert data["caliber"] == "§1.9-v2"
        assert data["sample"] == 10
        mc = data["monte_carlo"]
        assert mc["available"] is True
        assert abs(mc["real_excess"] - 0.024) < 1e-9
        assert 0.0 <= mc["quantile"] <= 1.0
        assert 0.0 <= mc["p_value"] <= 1.0
        assert mc["n_sims"] == 200 and mc["seed"] == 42
        assert mc["months_covered"] == 1
        # 复现性：同种子零模型分布确定
        again = build_report_data(
            settled, UNIVERSE_BY_DAY, CLOSES, BENCH, "2026-11-06", n_sims=200, seed=42
        )
        assert again["monte_carlo"] == mc


def _render_data(**overrides):
    """渲染测试夹具：红线降级形态为默认，overrides 覆盖顶层键。"""
    data = {
        "caliber": "§1.9-v2",
        "as_of": "2026-11-03",
        "ic_series": [
            {
                "month": "2026-11",
                "wins": 3,
                "losses": 1,
                "sample": 4,
                "ic": 0.75,
                "insufficient": True,
            }
        ],
        "avoidance_series": [],
        "icir": None,
        "sample": 4,
        "monte_carlo": {
            "available": False,
            "reason": "可判定日主样本 4<10（§1.9-v2 settled<10 红线），不产出零模型读数",
        },
    }
    data.update(overrides)
    return data


MC_OK = {
    "available": True,
    "real_excess": 0.024,
    "quantile": 0.973,
    "p_value": 0.027,
    "n_sims": 10000,
    "seed": 20261006,
    "months_covered": 1,
}


class TestRenderMarketingReport:
    def test_title_carries_date_and_caliber(self):
        """标题带报告日期与口径版本（§1.9-v2 预登记版本引用）。"""
        text = render_marketing_report(_render_data())
        first = text.splitlines()[0]
        assert "2026-11-03" in first and "§1.9-v2" in first

    def test_point_estimate_and_quantile_in_same_line(self):
        """点估计与分位同段：含「跑赢」的行必须同行含分位与 p 值（§1.9-v2 口径违规防线）。"""
        text = render_marketing_report(_render_data(monte_carlo=MC_OK, sample=10, icir=0.421))
        beat_lines = [ln for ln in text.splitlines() if "跑赢" in ln]
        assert len(beat_lines) == 1
        line = beat_lines[0]
        assert "0.0240" in line  # real_excess 点估计
        assert "分位" in line and "0.9730" in line  # 零模型分位
        assert "p 值" in line and "0.0270" in line  # 保守 p

    def test_degraded_mc_renders_reason_without_point_estimate(self):
        """红线触发：渲染降级说明（reason），不出现「跑赢」与单独点估计。"""
        text = render_marketing_report(_render_data())
        assert "不产出零模型读数" in text
        assert "样本 4<10" in text and "§1.9-v2" in text
        assert "跑赢" not in text
        assert "平均槽位超额" not in text

    def test_icir_none_shows_insufficient_note_and_month_mark(self):
        """ICIR 为 None → 「期数不足不展示」；样本不足月份在 IC 表逐月标注。"""
        text = render_marketing_report(_render_data())
        assert "期数不足不展示" in text
        assert "样本不足" in text
        assert "2026-11" in text

    def test_honesty_boundary_lists_triggered_gates(self):
        """诚实边界段逐条列出触发的门槛（样本不足期 / ICIR 不展示 / 零模型红线）。"""
        text = render_marketing_report(_render_data())
        boundary = text.split("诚实边界")[1]
        assert "2026-11 月样本 4（<10）" in boundary
        assert "ICIR 不展示" in boundary
        assert "零模型未产出" in boundary and "§1.9-v2" in boundary
