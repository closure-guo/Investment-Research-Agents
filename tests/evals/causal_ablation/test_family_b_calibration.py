"""P2 校准回读测试（零 LLM）：人工列归一、一致率与门控判词、B1 新增阈值标定。"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "tests" / "scripts" / "p2_calibration_apply.py"


def _load():
    spec = importlib.util.spec_from_file_location("p2_calibration_apply_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


apply_mod = _load()


class TestNorm:
    @pytest.mark.parametrize("raw", ["是", " 是 ", "Y", "YES", "true", "一致", "1"])
    def test_true_forms(self, raw):
        assert apply_mod._norm(raw) == "是"

    @pytest.mark.parametrize("raw", ["否", " n ", "NO", "false", "不一致", "0"])
    def test_false_forms(self, raw):
        assert apply_mod._norm(raw) == "否"

    @pytest.mark.parametrize("raw", ["", "   ", None, "待查", "?"])
    def test_unknown_is_none_not_false(self, raw):
        """未标注/无法识别 → None：不得当「不一致」计（那会把没判读过的东西算成错误）。"""
        assert apply_mod._norm(raw) is None


class TestGate:
    def _rows(self, pairs):
        return [
            {"unit_id": f"u{i}", "ticker": "600519", "machine": m, "human": h}
            for i, (m, h) in enumerate(pairs)
        ]

    def test_all_agree_passes(self):
        got = apply_mod.gate_for(self._rows([("是", "是"), ("否", "否")]), method="nli")
        assert got["agreement"] == 1.0 and got["passed"] is True

    def test_unlabeled_rows_excluded(self):
        got = apply_mod.gate_for(
            self._rows([("是", "是"), ("否", None), ("是", None)]), method="nli"
        )
        assert got["rows_labeled"] == 1 and got["rows_unlabeled"] == 2
        assert got["agreement"] == 1.0

    def test_below_threshold_fails_and_lists_disagreements(self):
        rows = self._rows([("是", "是")] + [("是", "否")] * 4)
        got = apply_mod.gate_for(rows, method="judge")
        assert got["passed"] is False
        assert got["agreement"] == pytest.approx(0.2)
        assert len(got["disagreements"]) == 4

    def test_no_labels_is_undecided_not_zero(self):
        got = apply_mod.gate_for(self._rows([("是", None)]), method="nli")
        assert got["agreement"] is None and got["passed"] is None
        assert "不可算" in got["reason"]


class TestReadLeg:
    def test_reads_human_and_machine_columns(self, tmp_path: Path):
        path = tmp_path / "2026-09-17-p2-calibration-b1.csv"
        columns = [
            "unit_id",
            "ticker",
            "风险点",
            "机器判定(被吸收?是/否)",
            "人工判定(被吸收?是/否)",
            "人工判定(是否新增?是/否)",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerow(
                {
                    "unit_id": "600519::b1::0",
                    "ticker": "600519",
                    "风险点": "r",
                    "机器判定(被吸收?是/否)": "是",
                    "人工判定(被吸收?是/否)": "否",
                    "人工判定(是否新增?是/否)": "是",
                }
            )
        rows = apply_mod.read_leg(path, apply_mod.LEGS["b1"])
        assert rows[0]["machine"] == "是" and rows[0]["human"] == "否"
        assert rows[0]["human_newness"] == "是"

    def test_missing_file_is_empty(self, tmp_path: Path):
        assert apply_mod.read_leg(tmp_path / "nope.csv", apply_mod.LEGS["b1"]) == []


class TestNewnessSweep:
    def test_no_labels_reports_not_calibratable(self):
        got = apply_mod.newness_threshold_sweep(
            [{"ticker": "600519", "risk_point": "r", "human_newness": None}], tickers=["600519"]
        )
        assert got["status"].startswith("无可标定样本")

    def test_sweep_picks_threshold_matching_human(self, monkeypatch):
        """相似度 0.1 的点人工判「新增」：阈值 >0.1 才对 → best 应落在 >0.1 的档位。"""
        import evals.causal_ablation.family_b_materials as fb
        import evals.causal_ablation.family_b_text as ft

        monkeypatch.setattr(fb, "load_material", lambda *a, **k: {"state": {"analyst_reports": {}}})
        monkeypatch.setattr(ft, "analyst_reference_points", lambda state: [""])
        monkeypatch.setattr(
            ft,
            "text_similarity",
            lambda point, ref: 0.1,
        )
        rows = [{"ticker": "600519", "risk_point": "r", "human_newness": "是"}]
        got = apply_mod.newness_threshold_sweep(rows, tickers=["600519"])
        assert got["status"] == "ok"
        assert got["best"]["agreement"] == 1.0
        assert got["best"]["threshold"] > 0.1
