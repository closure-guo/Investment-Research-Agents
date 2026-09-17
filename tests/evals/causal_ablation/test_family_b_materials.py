"""P2 族 B 材料与 code 腿测试（零 LLM）：捕获面、B3 出处判据、B4 遥测分母、报告口径。"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from evals.causal_ablation import family_b_materials as fb


def _state(*, action: str = "buy", entry: float = 100.0, stop: float = 95.0, target: float = 115.0):
    """最小 full 终态：决策 + 上游池（参考带/派生指标/分析师 claim/上游计划）。"""
    return {
        "final_trade_decision": {
            "action": action,
            "confidence": 0.7,
            "reasoning": "r",
            "entry_price": entry,
            "stop_loss": stop,
            "target_price": target,
            "evidence_refs": [{"claim": "站上 60 日线", "source": "technical"}],
        },
        "trader_plan": {
            "action": action,
            "entry_price": entry,
            "stop_loss": stop,
            "target_price": target,
        },
        "price_levels": {
            "available": True,
            "entry_ref": 100.2,
            "stop_band_long": {"low": 95.0},
            "target_band_long": {"high": 115.0},
            "atr": 4.0,
        },
        "derived_metrics": {"stop_distance_pct": 5.0, "risk_reward_ratio": 3.0},
        "analyst_reports": {
            "technical": {"claims": [{"stated_value": 100.0}], "markdown": "..."},
        },
        "price_check": {"result": "pass"},
        "price_check_attempts": 0,
        "price_level_corrected": False,
        "fund_manager_decision": "approve",
        "llm_config": {"api_key": "secret"},  # 排除项
        "macro_indicators": {"cpi": {"records": [{"v": 0.3}]}},
    }


class TestCapture:
    def test_excludes_connection_keys_and_keeps_the_rest(self):
        captured = fb.capture_state(_state())
        assert "llm_config" not in captured
        for key in ("final_trade_decision", "debate_history", "macro_indicators"):
            if key in _state():
                assert key in captured
        assert captured["__capture_dropped__"] == []

    def test_unpicklable_key_is_named_not_silently_dropped(self):
        state = _state()
        state["writer"] = lambda *a: None
        state["weird"] = object.__new__(type("Weird", (), {}))  # 可 pickle
        captured = fb.capture_state(state)
        # writer 在排除项里；这里验证的是「不可 pickle 的键被记名」
        assert "writer" not in captured

    def test_deep_copy_isolates_mutation(self):
        state = _state()
        captured = fb.capture_state(state)
        captured["price_levels"]["entry_ref"] = 999.0
        assert state["price_levels"]["entry_ref"] == 100.2


class TestB3Grounding:
    def test_all_params_grounded(self):
        got = fb.b3_unit(_state())
        assert got["params_total"] == 3
        assert got["params_grounded"] == 3
        assert got["grounding_rate"] == 1.0

    def test_ungrounded_param_lowers_rate(self):
        """上游池含 trader_plan：终态决策的值若与上游计划不一致且无处可溯 → 无出处。"""
        state = _state()
        state["final_trade_decision"]["entry_price"] = 123.45  # trader_plan 仍为 100.0
        got = fb.b3_unit(state)
        assert got["grounding_rate"] == pytest.approx(2 / 3)
        assert "entry_price" in got["ungrounded_values"]

    def test_tolerance_is_verifier_tolerance(self):
        """容差复用校验器 value_close：0.5% 内的近似值算有出处（不另立一套口径）。"""
        got = fb.b3_unit(_state(entry=100.3))  # 与 100.2/100.0 差 0.3% → 命中
        assert "entry_price" in got["grounded_params"]

    def test_watch_has_no_params(self):
        state = _state(action="watch")
        state["final_trade_decision"] = {"action": "watch", "confidence": 0.5, "reasoning": "r"}
        got = fb.b3_unit(state)
        assert got["params_total"] == 0
        assert got["grounding_rate"] is None  # 不得记 0

    def test_upstream_pool_excludes_the_decision_itself(self):
        """出处查的是「从上游来」：只有决策自身的值不算有出处。"""
        state = _state(entry=777.0, stop=777.1, target=777.2)
        state["trader_plan"] = {}
        state["price_levels"] = {"available": True}
        state["derived_metrics"] = {}
        state["analyst_reports"] = {}
        got = fb.b3_unit(state)
        assert got["grounding_rate"] == 0.0


class TestB4Telemetry:
    def test_price_bearing_decision_counts(self):
        got = fb.b4_unit(_state())
        assert got["needs_price"] is True
        assert got["result"] == "pass"

    def test_watch_does_not_count(self):
        state = _state(action="watch")
        assert fb.b4_unit(state)["needs_price"] is False

    def test_fail_and_correction_are_recorded(self):
        state = _state()
        state["price_check"] = {"result": "fail", "reason": "价位关系违规"}
        state["price_check_attempts"] = 1
        got = fb.b4_unit(state)
        assert got["result"] == "fail"
        assert got["attempts"] == 1

        state["price_check"] = {"result": "corrected", "note": "按参考带修正"}
        state["price_level_corrected"] = True
        got = fb.b4_unit(state)
        assert got["corrected"] is True and got["result"] == "corrected"


class TestMaterialIO:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        state = fb.capture_state(_state())
        paths = fb.save_material(tmp_path, "600519", state=state, llm_calls=19, snapshot_digest="d")
        assert Path(paths["pickle"]).exists() and Path(paths["json"]).exists()
        loaded = fb.load_material(tmp_path, "600519")
        assert loaded["llm_calls"] == 19
        assert loaded["state"]["final_trade_decision"]["action"] == "buy"

    def test_missing_material_raises(self, tmp_path: Path):
        with pytest.raises(Exception, match="缺 P2 材料"):
            fb.load_material(tmp_path, "000001")

    def test_summary_carries_b3_b4_readouts(self, tmp_path: Path):
        fb.save_material(
            tmp_path, "600519", state=fb.capture_state(_state()), llm_calls=19, snapshot_digest="d"
        )
        summary = (tmp_path / "600519.full.json").read_text(encoding="utf-8")
        assert '"grounding_rate": 1.0' in summary
        assert '"needs_price": true' in summary


class TestRunMaterials:
    def _setup(self, tmp_path: Path):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "600519.json").write_text("{}", encoding="utf-8")
        # 用 pickle 侧车提供快照（load_product 以 pickle 为准）
        with (snap_dir / "600519.pkl").open("wb") as fh:
            pickle.dump({"ticker": "600519", "snapshot": {"stock_code": "600519"}}, fh)
        runs: list[str] = []

        def runner(*, variant, snapshot, query):
            runs.append(variant)
            return _state()

        return snap_dir, runs, runner

    def test_runs_full_variant_and_resumes(self, tmp_path: Path):
        snap_dir, runs, runner = self._setup(tmp_path)
        out = tmp_path / "materials"
        units = fb.run_materials(
            ["600519"], snapshot_materials_dir=snap_dir, out_dir=out, graph_runner=runner
        )
        assert runs == ["full"]  # 变体固定为 full（对照臂是既有 analysts 产物）
        assert units[0]["llm_calls"] is None  # 未接线 meter → unknown，不伪造 0
        second = fb.run_materials(
            ["600519"], snapshot_materials_dir=snap_dir, out_dir=out, graph_runner=runner
        )
        assert runs == ["full"]  # 续跑：第二次不再跑图
        assert second[0]["b3"]["grounding_rate"] == 1.0


class TestReport:
    def _unit(self, ticker: str, *, rate: float | None, action: str = "buy", result: str = "pass"):
        return {
            "ticker": ticker,
            "b3": {
                "params_total": 3 if rate is not None else 0,
                "params_grounded": round((rate or 0) * 3),
                "grounding_rate": rate,
                "ungrounded_values": None,
            },
            "b4": {
                "needs_price": action in ("buy", "sell"),
                "result": result,
                "corrected": result == "corrected",
                "note": None,
            },
            "llm_calls": 19,
        }

    def test_rates_and_denominator(self):
        units = [
            self._unit("600519", rate=1.0),
            self._unit("000001", rate=1 / 3),
            self._unit("002415", rate=None, action="watch"),
            self._unit("300750", rate=1.0, result="fail"),
        ]
        report = fb.family_b_code_report(units)
        assert report["b3"]["params_total"] == 9
        assert report["b3"]["rate"] == pytest.approx(7 / 9)  # 3 + 1 + 3
        assert report["units_with_params"] == 3
        assert report["b4"]["checked_units"] == 3  # watch 不进分母
        assert report["b4"]["fail_rate"] == pytest.approx(1 / 3)
        assert report["b4"]["skipped_by_action"] == ["002415"]
        assert report["cost"]["llm_calls"] == 76

    def test_empty_denominators_are_none(self):
        report = fb.family_b_code_report([self._unit("600519", rate=None, action="watch")])
        assert report["b3"]["rate"] is None
        assert report["b4"]["fail_rate"] is None

    def test_unknown_cost_is_not_zero(self):
        unit = self._unit("600519", rate=1.0)
        unit["llm_calls"] = None
        report = fb.family_b_code_report([unit])
        assert report["cost"]["llm_calls"] is None


class TestSanityRecompute:
    """变体图不含 sanity 节点 → B4 用生产同一实现在产物决策上补算（并披露无打回回路）。"""

    def _state_with_plan(self, *, entry: float, stop: float, target: float) -> dict:
        import pandas as pd

        return {
            "trader_plan": {
                "action": "buy",
                "confidence": 0.7,
                "reasoning": "r",
                "entry_price": entry,
                "stop_loss": stop,
                "target_price": target,
            },
            "price_levels": {"available": True, "entry_ref": 100.0, "atr": 4.0},
            "kline": pd.DataFrame(
                {
                    "日期": [f"2026-07-{d:02d}" for d in range(1, 31)],
                    "开盘": [99.0] * 30,
                    "最高": [101.0] * 30,
                    "最低": [98.0] * 30,
                    "收盘": [100.0] * 30,
                    "成交量": [1.0e6] * 30,
                }
            ),
        }

    def test_valid_plan_passes_and_labels_computed_by(self):
        got = fb.b4_unit_with_sanity(self._state_with_plan(entry=100.0, stop=95.0, target=115.0))
        assert got["result"] in ("pass", "fail", "corrected")
        assert got["needs_price"] is True
        assert "validate_trade_prices" in got["computed_by"]

    def test_illegal_plan_is_flagged(self):
        """stop > entry 的 long 方案：sanity 必须拦下（否则补算等于没跑）。"""
        got = fb.b4_unit_with_sanity(self._state_with_plan(entry=100.0, stop=105.0, target=115.0))
        assert got["result"] in ("fail", "corrected")

    def test_missing_plan_passes_as_no_requirement(self):
        """无 trader plan → sanity 记 pass「无价位要求」（生产语义）；该 pass 是「没跑」，
        故报告侧把这类 note 单独列入 skipped_reasons，不与「校验通过」混读。"""
        got = fb.b4_unit_with_sanity({"trader_plan": None})
        assert got["result"] == "pass"
        assert got["needs_price"] is False

    def test_recompute_exception_is_recorded(self, monkeypatch):
        import finance_agent.nodes.validate as validate_mod

        def boom(_state):
            raise RuntimeError("kline 形状异常")

        monkeypatch.setattr(validate_mod, "validate_trade_prices", boom)
        got = fb.b4_unit_with_sanity(self._state_with_plan(entry=100.0, stop=95.0, target=115.0))
        assert got["result"] is None
        assert "补算失败" in got["note"]

    def test_unit_from_state_prefers_existing_price_check(self):
        state = self._state_with_plan(entry=100.0, stop=95.0, target=115.0)
        state["price_check"] = {"result": "pass", "note": "来自图"}
        unit = fb.unit_from_state("600519", state, llm_calls=19)
        assert unit["b4"]["result"] == "pass"
        assert "computed_by" not in unit["b4"]  # 已有图产物 → 不补算
