"""族 B 文本侧 code 腿测试（零 LLM）：相似度、B1 差集与阈值披露、B2 锚定率、判读材料导出。"""

from __future__ import annotations

import pytest

from evals.causal_ablation import family_b_text as ft


def _state() -> dict:
    return {
        "analyst_reports": {
            "fundamental": {
                "key_findings": ["营收与利润罕见负增长，基本面出现拐点信号"],
                "markdown": "…",
            },
            "technical": {"key_findings": ["MA5/10/20 空头排列，短期动能向下"], "markdown": "…"},
        },
        "debate_history": [
            {
                "role": "bear",
                "round": 1,
                "key_arguments": [
                    {"text": "营收与利润罕见负增长，基本面出现拐点信号", "kind": "data"},
                    {"text": "存货跌价准备计提不充分，潜在减值未反映", "kind": "inference"},
                ],
                "rebuttal_to": [],
            },
            {
                "role": "bull",
                "round": 2,
                "key_arguments": [{"text": "增长停滞是基数效应而非需求崩塌", "kind": "inference"}],
                "rebuttal_to": [1, 2],
            },
        ],
        "debate_anchor_checks": [
            {"anchored": True, "status": "resolved"},
            {"anchored": False, "status": "unresolved"},
        ],
        "final_trade_decision": {
            "action": "hold",
            "reasoning": "考虑到潜在减值未反映，维持观望",
            "evidence_refs": [{"claim": "空头排列", "source": "technical"}],
        },
        "fund_manager_decision": "approve",
        "fund_manager_decision_reasoning": "同意",
    }


class TestSimilarity:
    def test_identical_text_is_one(self):
        assert ft.text_similarity("营收负增长", "营收负增长") == pytest.approx(1.0)

    def test_paraphrase_is_low(self):
        """改写型文本字面相似度低——这正是 B1 需要阈值披露的原因。"""
        got = ft.text_similarity(
            "2025年营收与利润罕见负增长，基本面出现拐点信号",
            "营收/利润罕见负增长：2025年营业收入同比-1.21%；归母净利润同比-4.53%",
        )
        assert 0.0 < got < 0.35

    def test_empty_texts_are_zero(self):
        assert ft.text_similarity("", "任意") == 0.0
        assert ft.text_similarity("任意", "") == 0.0


class TestB1:
    def test_exact_duplicate_is_not_new(self):
        got = ft.b1_unit(_state())
        assert got["debate_points"] == 2
        assert got["new_points"] == 1  # 与分析师发现逐字相同的那条不算新增
        assert got["points"][0]["text"].startswith("存货跌价准备")

    def test_threshold_sweep_is_disclosed(self):
        got = ft.b1_unit(_state())
        sweep = got["threshold_sweep"]
        assert set(sweep) == {f"{t:.2f}" for t in ft.THRESHOLD_SWEEP}
        # 单调性：阈值越松，「新增」越少（或持平）
        values = [sweep[f"{t:.2f}"] for t in ft.THRESHOLD_SWEEP]
        assert all(a >= b for a, b in zip(values, values[1:], strict=False))

    def test_no_debate_yields_none_share(self):
        state = _state()
        state["debate_history"] = []
        got = ft.b1_unit(state)
        assert got["debate_points"] == 0
        assert got["new_share"] is None  # 不得记 0

    def test_bull_points_are_not_risk_points(self):
        """风险点只取空方论点（bull 的辩护不进风险点集）。"""
        texts = [p["text"] for p in ft.debate_risk_points(_state())]
        assert all("基数效应" not in t for t in texts)


class TestB2:
    def test_rebuttal_rate_is_message_level(self):
        got = ft.b2_unit(_state())
        assert got["later_messages"] == 1
        assert got["messages_with_rebuttal"] == 1
        assert got["rebuttal_rate"] == pytest.approx(1.0)  # ≤1（按论点算会出现 >1 的伪值）
        assert got["rebuttal_targets_total"] == 2

    def test_anchor_check_counts(self):
        got = ft.b2_unit(_state())
        assert got["anchor_checks_total"] == 2
        assert got["anchor_checks_anchored"] == 1
        assert got["anchor_checks_unresolved"] == 1

    def test_no_later_round_is_none(self):
        state = _state()
        state["debate_history"] = state["debate_history"][:1]
        assert ft.b2_unit(state)["rebuttal_rate"] is None


class TestMaterials:
    def test_b1_rows_export_all_points_with_similarity(self):
        """导出**全部**空方论点（带 max_similarity）——阈值未标定前不预筛，
        否则未校准的「新增」判据会焊进判定集。"""
        unit = {"ticker": "600519", "_state": _state(), "b1": ft.b1_unit(_state())}
        rows = ft.judge_material_rows([unit])
        assert len(rows) == 2  # 两条 bear 论点都在，不论阈值判它新旧
        assert all(isinstance(r["max_similarity"], float) for r in rows)
        row = rows[0]
        assert row["decision_action"] == "hold"
        assert "潜在减值" in row["decision_reasoning"]
        assert row["nli_target(被吸收?是/否)"] == ""  # 判定留空：门控后的腿填
        assert row["human_label(被吸收?是/否)"] == ""

    def test_b2_rows_map_rebuttal_to_previous_points(self):
        rows = ft.b2_material_rows([{"ticker": "600519", "_state": _state()}])
        assert len(rows) == 2  # 两个 rebuttal_to 目标
        assert rows[0]["rebutted_point"].startswith("营收与利润")
        assert rows[0]["judge_target(被修正?是/否)"] == ""

    def test_calibration_sample_is_deterministic_and_bounded(self):
        rows = [{"i": i} for i in range(10)]
        sample = ft.calibration_sample(rows, fraction=0.2)
        assert [r["i"] for r in sample] == [0, 5]  # 等距，重跑同结果
        assert ft.calibration_sample([], fraction=0.2) == []
        assert ft.calibration_sample(rows, fraction=0.0) == []
