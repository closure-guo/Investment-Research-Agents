"""A1（确定性指标注入）补测测试（零 LLM）：剥离面、两态同尺、判据、报告口径。

分析师真跑用可控 fake graph_runner 替换；claim 判定走真实 `finance_agent.citation`。
"""

from __future__ import annotations

import pandas as pd
import pytest

from evals.causal_ablation import compute_a1 as a1
from evals.causal_ablation import pilot_runner as pr


def _snapshot() -> dict:
    """含原始输入 + 真实 compute 输出的快照（compute_metrics 能在其上跑）。"""
    from finance_agent.nodes.compute import compute_metrics

    raw = {
        "stock_code": "600519",
        "income_statement": pd.DataFrame(
            {
                "报告日": ["20251231", "20241231"],
                "营业总收入": [1.0e9, 9.0e8],
                "净利润": [2.0e8, 1.8e8],
            }
        ),
        "balance_sheet": pd.DataFrame(
            {"报告日": ["20251231", "20241231"], "总资产": [5.0e9, 4.6e9], "净资产": [3.0e9, 2.7e9]}
        ),
        "cash_flow_statement": pd.DataFrame(
            {"报告日": ["20251231", "20241231"], "经营活动产生的现金流量净额": [3.0e8, 2.5e8]}
        ),
        "kline": pd.DataFrame(
            {
                "日期": [f"2026-07-{d:02d}" for d in range(1, 31)],
                "开盘": [95.0 + i * 0.2 for i in range(30)],
                "最高": [96.0 + i * 0.2 for i in range(30)],
                "最低": [94.0 + i * 0.2 for i in range(30)],
                "收盘": [95.5 + i * 0.2 for i in range(30)],
                "成交量": [1.0e6 + i * 1e4 for i in range(30)],
            }
        ),
        "benchmark_kline": pd.DataFrame(
            {
                "日期": [f"2026-07-{d:02d}" for d in range(1, 31)],
                "收盘": [3000.0 + i * 5 for i in range(30)],
            }
        ),
        "macro_indicators": {"cpi": {"records": [{"月份": "2026-07", "全国-同比增长": 0.3}]}},
        "news_list": [{"title": "公司公告：拟回购股份", "date": "2026-08-01"}],
    }
    return {**raw, **compute_metrics(raw)}  # type: ignore[arg-type]


def _recomputable_path(snapshot: dict) -> tuple[str, float]:
    """从真实 compute 输出里取一条**校验器认**的可重算数值路径（不假设键名）。

    判据用校验器自己的解析（`citation._resolve_field_ref`）反查——夹具若靠人工假设路径，
    指标口径一变测试就假绿/假红。
    """
    from finance_agent import citation as cm

    for root in sorted(cm._COMPUTATIONAL_RECALC):
        value = snapshot.get(root)
        if not isinstance(value, dict):
            continue
        for key, leaf in value.items():
            ref = None
            if isinstance(leaf, (int, float)) and not isinstance(leaf, bool):
                ref = f"{root}.{key}"
            elif isinstance(leaf, dict):
                for period, inner in sorted(leaf.items()):
                    if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                        ref = f"{root}.{key}.{period}"
                        break
            if ref is None:
                continue
            try:
                truth = cm._resolve_field_ref(ref, snapshot, None)
            except Exception:  # noqa: BLE001, S112 - 解析不出就换下一条（读数是挑选，非跳过）
                continue
            if isinstance(truth, (int, float)) and not isinstance(truth, bool):
                return ref, float(truth)
    raise AssertionError("快照无可重算数值路径")


def _claim(value: float, root: str) -> dict:
    return {
        "claim_type": "computational",
        "source_type": "data",
        "field_ref": root,
        "stated_value": value,
        "interpretation": f"指标 {value}",
        "direction": "positive",
    }


def _product(snapshot: dict | None = None) -> dict:
    return {"ticker": "600519", "snapshot": snapshot or _snapshot(), "analyst_reports": {}}


class TestStrip:
    def test_strip_removes_only_compute_outputs(self):
        snapshot = _snapshot()
        off = a1.strip_compute_outputs(snapshot)
        keys = a1.compute_output_keys(snapshot)
        assert keys, "compute_metrics 应有输出键"
        for key in keys:
            assert key not in off
        for key in (
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
            "kline",
            "news_list",
        ):
            assert key in off  # 原始输入保留
        assert off["__a1_stripped_keys__"] == sorted(keys)
        assert "profitability_metrics" in snapshot  # 入参未被就地改写

    def test_strip_is_deep_copy(self):
        snapshot = _snapshot()
        off = a1.strip_compute_outputs(snapshot)
        off["macro_indicators"]["cpi"]["records"][0]["全国-同比增长"] = 9.9
        assert snapshot["macro_indicators"]["cpi"]["records"][0]["全国-同比增长"] == 0.3


class TestClaimStats:
    def test_recomputable_value_fail_counts(self):
        snapshot = _snapshot()
        root, truth = _recomputable_path(snapshot)
        good = _claim(truth, root=root)
        bad = _claim(truth * 2, root=root)
        stats = a1._claim_stats([good, bad], snapshot)
        assert stats["recomputable_claims"] == 2
        assert stats["recomputable_value_fail"] == 1
        assert stats["recomputable_error_rate"] == pytest.approx(0.5)

    def test_non_recomputable_root_is_not_in_denominator(self):
        snapshot = _snapshot()
        stats = a1._claim_stats([_claim(1.0, root="unknown_root.x")], snapshot)
        assert stats["recomputable_claims"] == 0
        assert stats["recomputable_error_rate"] is None

    def test_unparsable_claim_is_counted_not_mixed(self):
        stats = a1._claim_stats([{"field_ref": 1}], _snapshot())
        assert stats["claims_total"] == 1
        assert stats["claims_parsed"] == 0
        assert stats["claims_unparsed"] == 1
        assert stats["recomputable_error_rate"] is None


class TestCase:
    def _runner(self, *, off_claims, on_claims, seen: list[dict]):
        """fake 图：按快照是否带 compute 输出分派两态 claim。"""

        def runner(*, variant, snapshot, query):
            assert variant == "analysts"
            has_compute = "profitability_metrics" in snapshot
            seen.append({"has_compute": has_compute, "snapshot_keys": sorted(snapshot)[:3]})
            claims = on_claims if has_compute else off_claims
            return {
                "analyst_reports": {"fundamental": {"claims": claims, "markdown": "x"}},
                "citation_pass": has_compute,
                "citation_fail_buckets": {},
            }

        return runner

    def test_two_arms_use_only_the_strip_as_variable(self):
        snapshot = _snapshot()
        root, truth = _recomputable_path(snapshot)
        seen: list[dict] = []
        unit = a1.run_a1_case(
            _product(snapshot),
            graph_runner=self._runner(
                off_claims=[
                    _claim(truth * 1.5, root=root),
                    _claim(truth * 1.5, root=root),
                ],  # 自算算错
                on_claims=[_claim(truth, root=root)],  # 直读正确
                seen=seen,
            ),
        )
        assert unit["status"] == pr.STATUS_OK
        assert [s["has_compute"] for s in seen] == [False, True]  # 先 OFF 后 ON
        assert unit["recomputable_error_rate_off"] == pytest.approx(1.0)
        assert unit["recomputable_error_rate_on"] == pytest.approx(0.0)
        assert unit["recomputable_error_rate_delta"] == pytest.approx(1.0)

    def test_ruler_is_the_full_snapshot_in_both_arms(self):
        """两态判定必须用同一把尺子：OFF 态 claim 的判定若去用剥离后的快照，
        真值解析不出 → 全部落 path_unresolvable，错误率失真。"""
        snapshot = _snapshot()
        root, truth = _recomputable_path(snapshot)
        unit = a1.run_a1_case(
            _product(snapshot),
            graph_runner=self._runner(
                off_claims=[_claim(truth * 1.5, root=root)],
                on_claims=[_claim(truth, root=root)],
                seen=[],
            ),
        )
        off = unit["arms"][a1.ARM_OFF]
        assert off["path_unresolvable"] == 0
        assert off["recomputable_value_fail"] == 1  # 自算值被判 value_mismatch（真值可解析）

    def test_missing_arms_is_void(self):
        unit = a1.run_a1_case(
            _product(),
            graph_runner=self._runner(off_claims=[], on_claims=[], seen=[]),
            arms=(a1.ARM_ON,),
        )
        assert unit["status"] == pr.STATUS_VOID
        assert "缺臂" in unit["status_reason"]

    def test_no_denominator_is_void(self):
        """两态都没有可重算 claim → 该标的对主指标零信息，void 而非记 0%。"""
        unit = a1.run_a1_case(
            _product(),
            graph_runner=self._runner(
                off_claims=[
                    {
                        "claim_type": "entity",
                        "source_type": "event",
                        "field_ref": "x",
                        "stated_value": "x",
                        "interpretation": "x",
                    }
                ],
                on_claims=[],
                seen=[],
            ),
        )
        assert unit["status"] == pr.STATUS_VOID
        assert "分母" in unit["status_reason"]

    def test_snapshot_without_raw_inputs_is_void(self):
        unit = a1.run_a1_case(
            {"ticker": "600519", "snapshot": {"stock_code": "600519"}},
            graph_runner=self._runner(off_claims=[], on_claims=[], seen=[]),
        )
        assert unit["status"] == pr.STATUS_VOID
        assert "compute_metrics" in unit["status_reason"]

    def test_missing_compute_keys_in_snapshot_is_void(self):
        """快照若本来就缺 compute 输出键 → 两态无差别，不得当读数。"""
        snapshot = _snapshot()
        for key in a1.compute_output_keys(snapshot):
            snapshot.pop(key, None)
        unit = a1.run_a1_case(
            _product(snapshot),
            graph_runner=self._runner(off_claims=[], on_claims=[], seen=[]),
        )
        assert unit["status"] == pr.STATUS_VOID
        assert "缺 compute 输出键" in unit["status_reason"]


class TestReport:
    def _unit(self, ticker: str, off: float, on: float) -> dict:
        return {
            "unit_id": f"{ticker}::a1",
            "ticker": ticker,
            "status": pr.STATUS_OK,
            "recomputable_error_rate_off": off,
            "recomputable_error_rate_on": on,
            "recomputable_error_rate_delta": off - on,
            "llm_calls": 8,
            "arms": {
                a1.ARM_OFF: {
                    "recomputable_claims": 10,
                    "recomputable_value_fail": int(off * 10),
                    "claims_total": 20,
                    "path_unresolvable": 1,
                    "unverifiable": 0,
                    "llm_calls": 4,
                    "per_claim": [],
                },
                a1.ARM_ON: {
                    "recomputable_claims": 10,
                    "recomputable_value_fail": int(on * 10),
                    "claims_total": 20,
                    "path_unresolvable": 0,
                    "unverifiable": 0,
                    "llm_calls": 4,
                    "per_claim": [],
                },
            },
        }

    def test_rates_and_cluster_ci(self):
        units = [self._unit("600519", 0.8, 0.1), self._unit("000001", 0.6, 0.2)]
        report = a1.a1_report(units, tickers=["600519", "000001"])
        assert report[a1.ARM_OFF]["rate"] == pytest.approx(0.7)
        assert report[a1.ARM_ON]["rate"] == pytest.approx(0.15)
        assert report["delta_rate"] == pytest.approx(0.55)
        assert report["cluster_ci95"] is not None and len(report["cluster_ci95"]) == 2
        assert report[a1.ARM_OFF]["llm_calls"] == 8

    def test_void_units_still_count_toward_cost(self):
        """成本按已尝试单元全计（void 也烧了钱）——只算 ok 会低估。"""
        void = {
            "unit_id": "002415::a1",
            "ticker": "002415",
            "status": pr.STATUS_VOID,
            "status_reason": "分母为 0",
            "llm_calls": 10,
            "arms": {
                a1.ARM_OFF: {"recomputable_claims": 0, "llm_calls": 5, "per_claim": []},
                a1.ARM_ON: {"recomputable_claims": 0, "llm_calls": 5, "per_claim": []},
            },
        }
        report = a1.a1_report([self._unit("600519", 0.5, 0.0), void])
        assert report["units_void"] == 1
        assert report[a1.ARM_OFF]["llm_calls"] == 9  # 4 + 5
        assert report["llm_calls_total"] == 18  # 8 + 10

    def test_single_cluster_has_no_ci(self):
        report = a1.a1_report([self._unit("600519", 0.5, 0.0)])
        assert report["cluster_ci95"] is None  # 单簇不出 CI（不得假装有区间）

    def test_void_and_unknown_cost_are_honest(self):
        unit = self._unit("600519", 0.5, 0.0)
        unit["arms"][a1.ARM_OFF]["llm_calls"] = None
        report = a1.a1_report(
            [unit, {"unit_id": "u2", "status": pr.STATUS_VOID, "status_reason": "r"}]
        )
        assert report["units_void"] == 1
        assert report["void_reasons"] == ["r"]
        assert report[a1.ARM_OFF]["llm_calls"] is None  # 未知不得伪造成 0
