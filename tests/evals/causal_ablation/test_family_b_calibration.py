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


class TestBearGrounding:
    """无源增量 grounding 扫描（bg-v1，预登记 2026-09-18）：宇宙裁剪 + prompt 契约 + 解析 + 报告。"""

    @staticmethod
    def _state() -> dict:
        return {
            "analyst_reports": {
                "macro": {"summary": "M2 增速 7.5%，PMI 49.8 低于荣枯线"},
                "technical": {"summary": "均线空头排列，MACD 零轴下方死叉"},
            },
            "debate_history": [
                {
                    "role": "bear",
                    "round": 1,
                    "content": "c1",
                    "key_arguments": [
                        {"text": "机构资金持续撤离", "kind": "data", "anchors": []},
                        {"text": "PMI 压制需求", "kind": "data", "anchors": ["macro.PMI"]},
                        {"text": "推断性论点", "kind": "inference", "anchors": []},
                    ],
                },
                {
                    "role": "bear",
                    "round": 2,
                    "content": "c2",
                    "key_arguments": [{"text": "r2 数据论点", "kind": "data", "anchors": []}],
                },
            ],
        }

    def test_units_restrict_to_round1_kind_data_with_summaries(self):
        from evals.causal_ablation import bear_grounding as bg

        units = bg.grounding_units("600000", self._state())
        assert [u["argument"] for u in units] == ["机构资金持续撤离", "PMI 压制需求"]
        assert all("M2 增速" in u["summaries"] for u in units)
        assert units[0]["unit_id"] == "600000::bg::0"
        assert units[1]["anchors"] == ["macro.PMI"]

    def test_prompt_carries_material_and_bidirectional_rule(self):
        from evals.causal_ablation import bear_grounding as bg

        prompt = bg.grounding_prompt(
            {"summaries": "宏观：M2 增速 7.5%。", "argument": "机构资金持续撤离"}
        )
        for token in (
            "M2",
            "机构资金",
            "方向相反",
            '"supported"',
            "unsupported_part",
            "confidence",
        ):
            assert token in prompt

    def test_run_parses_and_stamps_rubric(self):
        from evals.causal_ablation import bear_grounding as bg

        def llm(prompt: str) -> str:
            return (
                '{"supported": false, "unsupported_part": "机构资金持续撤离", '
                '"reason": "摘要零资金流", "confidence": "low"}'
            )

        judged = bg.run_grounding(bg.grounding_units("600000", self._state()), llm_fn=llm)
        assert judged[0]["judge_label"] is False
        assert judged[0]["unsupported_part"] == "机构资金持续撤离"
        assert judged[0]["confidence"] == "low"
        assert judged[0]["rubric"] == bg.BG_RUBRIC

    def test_report_rates_and_lists_unsupported(self):
        from evals.causal_ablation import bear_grounding as bg

        rows = [
            {
                "unit_id": "a",
                "ticker": "t1",
                "judge_label": False,
                "unsupported_part": "X",
                "anchors": [],
            },
            {
                "unit_id": "b",
                "ticker": "t1",
                "judge_label": True,
                "unsupported_part": "",
                "anchors": [],
            },
            {
                "unit_id": "c",
                "ticker": "t2",
                "judge_label": None,
                "unsupported_part": "",
                "anchors": [],
            },
        ]
        report = bg.grounding_report(rows)
        assert report["rate"] == 0.5  # None 不进分母
        assert report["unsupported"] == [("a", "X", ())]

    def test_calibration_sample_takes_all_positives_plus_slice_of_supported(self):
        from evals.causal_ablation import bear_grounding as bg

        rows = [
            {"unit_id": f"u{i}", "judge_label": i >= 3, "ticker": "t", "argument": f"a{i}"}
            for i in range(13)
        ]  # u0-u2 无源（正例 label=False），u3-u12 有源
        sample = bg.calibration_sample(rows, supported_fraction=0.1, seed=7)
        ids = {r["unit_id"] for r in sample}
        assert {"u0", "u1", "u2"} <= ids  # 正例全审
        assert sum(1 for i in ids if int(i[1:]) >= 3) == 1  # 有源抽 10%
