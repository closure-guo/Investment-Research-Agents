"""P1 跑批驱动（CLI）预算接线测试（零 LLM）：meter 读数进报告 + 真跑腿消耗全量计账。

库侧预算语义见 `test_pilot_runner.TestBudgetHonesty`；本文件只验驱动层：
① 真跑腿已尝试单元（含 void/预判 void）的消耗全部入账，不得只算 ok 单元；
② meter 绝对读数经 CLI 传进 `pilot_report`（`llm_meter_total`）并可交叉核对；
③ 非决策点的注入不可施加 → 不运行图（零消耗）；决策点（illegal_price）产物无决策 →
   后置流程跑 1 趟图产出决策，该趟成本如实入账（P1 重校轮：不得据「快照无决策」预判 void）。

`graph_runner` / `llm_meter` 全注入（假图返回 `{}`，不碰真实管线与 LLM）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from evals.causal_ablation import pilot_runner as pr

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "tests" / "scripts" / "p1_injection_pilot.py"


def _load_cli():
    """以模块方式加载跑批脚本（tests/scripts 非包，走 importlib——同 test_ablation_pilot）。"""
    spec = importlib.util.spec_from_file_location("p1_injection_pilot_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _product() -> dict:
    """最小可跑产物：快照含真跑型两类目标结构，且**无决策 dict**。

    `income_statement` → value_error 可施加（偶数实例主靶点 derived_series 缺失时回落）；
    `kline` → mirror_narrative 可施加（主靶点 technical_indicators 缺失时回落）；
    缺 `trader_plan` / `final_trade_decision` → illegal_price 走后置流程（跑图产出决策）。
    """
    return {
        "ticker": "600519",
        "snapshot_digest": "deadbeef",
        "snapshot": {
            "stock_code": "600519",
            "revenue": 1.0e9,
            "growth": -0.1005,
            "price": 100.0,
            "entry": 100.0,
            "income_statement": pd.DataFrame(
                {"报告日": ["20251231", "20241231"], "营业总收入": [1.0e9, 9.0e8]}
            ),
            "kline": pd.DataFrame({"日期": ["2026-08-01", "2026-08-04"], "收盘": [99.0, 100.0]}),
        },
        "analyst_reports": {},
        "citation": {},
        "base_values": {"revenue": 1.0e9, "price": 100.0, "growth": -0.1005, "entry": 100.0},
        "paths": {},
    }


def _write_materials(tmp_path: Path) -> Path:
    materials = tmp_path / "materials"
    product = _product()
    pr.save_product(pr.product_path(materials, "600519"), product)
    return materials


def _write_prereg(tmp_path: Path) -> Path:
    prereg_dir = tmp_path / "prereg"
    prereg_dir.mkdir(parents=True, exist_ok=True)
    (prereg_dir / "2026-09-16-p1-injection-pilot.md").write_text(
        "- 主指标: 逃逸率\n- MDE: 6pp\n- 决策阈值: 拦截率 < 50% 判薄防线（依据：见 D6）\n"
        "- 样本量依据: 10 标的 × 8 类 × 4 实例\n- 停止规则: 不一致对子 < 10% 停跑\n"
        "- rubric 版本: judge-v8\n",
        encoding="utf-8",
    )
    return prereg_dir


def _run(tmp_path: Path, *, calls_per_run: int = 5) -> tuple[dict, int]:
    ledger: list[int] = []

    def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
        ledger.extend(range(calls_per_run))  # 每次图运行消耗 calls_per_run 次 LLM 调用
        return {}

    report_path = cli.run_pilot(
        tickers=("600519",),
        instances=1,
        legs="real",
        real_run_limit=0,
        materials_dir=_write_materials(tmp_path),
        out_dir=tmp_path / "out",
        prereg_dir=_write_prereg(tmp_path),
        graph_runner=fake_runner,
        llm_meter=lambda: len(ledger),
    )
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return report, len(ledger)


class TestRealLegBudgetAccounting:
    def test_all_attempted_units_are_booked(self, tmp_path: Path):
        """3 个真跑单元（value_error / mirror_narrative 各 2 态；illegal_price 后置流程 1 趟）。"""
        report, meter_calls = _run(tmp_path)
        real = report["budget"]["real_run"]
        assert real["units"] == 3
        assert real["units_by_status"] == {"ok": 0, "void_injection": 3, "error": 0}
        # 旧实现只算 ok 单元 → llm_calls/runs 均为 0（消耗被静默丢掉）
        # 新流程：决策点产物无决策 → 后置流程跑 1 趟图产出决策（本 fixture 产出 {} → 仍判 void），
        # 该趟成本如实入账（不得写 0：图确实跑了）
        assert real["runs"] == 5  # 2 个可施加单元 × 2 态 + 决策点后置流程 1 趟
        assert real["runs_unknown_units"] == 0
        assert real["llm_calls"] == 5 * 5
        assert real["llm_calls_unknown_units"] == 0
        assert real["llm_calls_by_status"]["void_injection"] == real["llm_calls"]
        assert meter_calls == real["llm_calls"]

    def test_meter_total_cross_checks_unit_sum(self, tmp_path: Path):
        report, meter_calls = _run(tmp_path)
        real = report["budget"]["real_run"]
        assert real["llm_meter_total_calls"] == meter_calls
        assert real["llm_meter_matches_unit_sum"] is True

    def test_decision_point_unit_runs_once_to_produce_decision(self, tmp_path: Path):
        """决策点（illegal_price）产物无决策 → 后置流程跑 1 趟图（成本入账），不再据「快照无决策」预判 void。

        P1 轮 1 实证：材料步只跑 analysts 变体 → 产物无决策 → 4/4 预判 void，决策层注入
        拿不到任何信息。重校后改为「跑图产出决策 → 内存注入 → A5 两态校验」；本 fixture 的
        假图返回 {}（无决策）→ 仍判 void，但**图已跑 1 趟**，成本必须如实入账。
        """
        report, _ = _run(tmp_path)
        illegal = [u for u in report["units"] if u["pollution_type"] == "illegal_price"]
        assert len(illegal) == 1
        unit = illegal[0]
        assert unit["status"] == pr.STATUS_VOID
        assert unit["injection"]["applied"] is False
        assert unit["graph_runs"] == 1  # 后置流程图已跑（不是 0：不得漏计）
        assert unit["llm_calls"] == 5
        assert "决策 dict" in unit["status_reason"]
        assert unit["on_state"] == "" and unit["off_state"] == ""  # 未判定，不得读成 caught
        # 该型其余单元照常两态跑图（本批 4 次运行 = value_error 2 + mirror_narrative 2）
        ran = [u for u in report["units"] if u["graph_runs"] == 2]
        assert {u["pollution_type"] for u in ran} == {"value_error", "mirror_narrative"}
        assert report["budget"]["offline_replay"]["units"] == 0  # legs=real 不跑离线腿

    def test_non_decision_unapplicable_injection_is_prevoided(self, tmp_path: Path):
        """非决策点仍按预判 void 零消耗：目标结构缺失时不跑图（浪费的消耗是合规缺陷）。"""
        ledger: list[int] = []

        def counting_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            ledger.extend(range(5))
            return {}

        product = _product()
        product["snapshot"].pop(
            "kline"
        )  # mirror_narrative 主靶点（technical_indicators）与回落靶点均缺
        materials = tmp_path / "materials"
        pr.save_product(pr.product_path(materials, "600519"), product)
        report_path = cli.run_pilot(
            tickers=("600519",),
            instances=1,
            legs="real",
            real_run_limit=0,
            materials_dir=materials,
            out_dir=tmp_path / "out",
            prereg_dir=_write_prereg(tmp_path),
            graph_runner=counting_runner,
            llm_meter=lambda: len(ledger),
        )
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        mirror = [u for u in report["units"] if u["pollution_type"] == "mirror_narrative"]
        assert len(mirror) == 1
        unit = mirror[0]
        assert unit["status"] == pr.STATUS_VOID
        assert unit["injection"]["applied"] is False
        assert unit["graph_runs"] == 0 and unit["llm_calls"] == 0
        assert "预判" in unit["status_reason"]
        assert "0 次图运行" in unit["status_reason"]


# ── 材料快照口径（白名单漂移两次后改排除式 + 完整性守卫）──


class TestSnapshotKeys:
    def _complete_state(self) -> dict:
        """能跑通 compute_metrics 的最小 state（原始输入齐 + 注入基值）。"""
        from finance_agent.nodes.compute import compute_metrics

        raw = {
            "stock_code": "600519",
            "income_statement": pd.DataFrame(
                {"报告日": ["20251231", "20241231"], "营业总收入": [1.0e9, 9.0e8]}
            ),
            "balance_sheet": pd.DataFrame(
                {"报告日": ["20251231", "20241231"], "总资产": [5.0e9, 4.6e9]}
            ),
            "cash_flow_statement": pd.DataFrame(
                {"报告日": ["20251231", "20241231"], "经营活动产生的现金流量净额": [3.0e8, 2.5e8]}
            ),
            "kline": pd.DataFrame(
                {
                    "日期": [f"2026-07-{d:02d}" for d in range(1, 31)],
                    "开盘": [95.0] * 30,
                    "最高": [96.0] * 30,
                    "最低": [94.0] * 30,
                    "收盘": [95.5 + i * 0.2 for i in range(30)],
                    "成交量": [1.0e6] * 30,
                }
            ),
            "benchmark_kline": pd.DataFrame(
                {"日期": [f"2026-07-{d:02d}" for d in range(1, 31)], "收盘": [3000.0] * 30}
            ),
            "news_list": [],
        }
        return {**raw, **compute_metrics(raw)}  # type: ignore[arg-type]

    def test_keys_include_every_compute_output(self):
        """白名单曾漏 6 键（anomalies/garp_result/health_score/price_levels/
        risk_metrics/traffic_lights）→ 改排除式后必须全含。"""
        from finance_agent.nodes.compute import compute_metrics

        state = self._complete_state()
        keys = cli._snapshot_keys(state)
        missing = set(compute_metrics(state)) - set(keys)  # type: ignore[arg-type]
        assert missing == set(), f"快照缺 compute 输出键 {missing}"

    def test_non_data_keys_are_excluded(self):
        state = {**self._complete_state(), "llm_config": {"api_key": "x"}, "query": "q"}
        keys = cli._snapshot_keys(state)
        assert "llm_config" not in keys and "query" not in keys
        assert "analyst_reports" not in keys  # 单独以 claims+markdown 落盘

    def test_compute_output_missing_from_state_raises(self, monkeypatch):
        """state 里 compute 跑得出键、但它被排除式漏掉 → 显式报错（不静默丢面）。"""
        state = self._complete_state()
        monkeypatch.setattr(
            cli, "_SNAPSHOT_EXCLUDE", frozenset(set(cli._SNAPSHOT_EXCLUDE) | {"health_score"})
        )
        with pytest.raises(pr.ProductError, match="health_score"):
            cli._snapshot_keys(state)

    def test_state_without_raw_inputs_skips_guard_with_warning(self, capsys):
        keys = cli._snapshot_keys({"stock_code": "600519"})
        assert keys == ["stock_code"]
        assert "跳过 compute 输出键完整性校验" in capsys.readouterr().out


class TestMaterialsBackfill:
    def _load_backfill(self):
        spec = importlib.util.spec_from_file_location(
            "backfill_materials_under_test",
            _ROOT / "tests" / "scripts" / "backfill_materials_compute_outputs.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_backfill_adds_only_missing_keys_and_records_trace(self):
        backfill = self._load_backfill()
        snapshot = TestSnapshotKeys()._complete_state()
        for key in ("health_score", "risk_metrics", "price_levels"):
            snapshot.pop(key, None)
        kept = snapshot["cashflow_metrics"]
        product = {"ticker": "600519", "snapshot": snapshot, "snapshot_digest": "injected"}
        result = backfill.backfill_product(product)
        assert result["keys_added"] == ["health_score", "price_levels", "risk_metrics"]
        assert result["written"] is True
        filled = result["product"]
        assert filled["snapshot_backfill"]["keys_added"] == result["keys_added"]
        assert filled["snapshot_backfill"]["digest_before"] == "injected"
        assert filled["snapshot_backfill"]["digest_after"] != "injected"
        assert filled["snapshot"]["cashflow_metrics"] is kept  # 已有值原样（不覆盖）
        assert product["snapshot_digest"] == "injected"  # 入参不被就地改写

    def test_complete_product_is_noop(self):
        backfill = self._load_backfill()
        product = {"ticker": "600519", "snapshot": TestSnapshotKeys()._complete_state()}
        result = backfill.backfill_product(product)
        assert result["keys_added"] == []
        assert result["written"] is False
