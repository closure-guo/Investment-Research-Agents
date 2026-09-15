"""P1 跑批驱动（CLI）预算接线测试（零 LLM）：meter 读数进报告 + 真跑腿消耗全量计账。

库侧预算语义见 `test_pilot_runner.TestBudgetHonesty`；本文件只验驱动层：
① 真跑腿已尝试单元（含 void/预判 void）的消耗全部入账，不得只算 ok 单元；
② meter 绝对读数经 CLI 传进 `pilot_report`（`llm_meter_total`）并可交叉核对；
③ 注入不可施加的单元不运行图（等价于不消耗 LLM）。

`graph_runner` / `llm_meter` 全注入（假图返回 `{}`，不碰真实管线与 LLM）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

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

    `income_statement` → value_error 可施加；`kline` → mirror_narrative 可施加；
    缺 `trader_plan` / `final_trade_decision` → illegal_price 注入不可施加（预判 void）。
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
    (prereg_dir / "2026-09-16-p1.md").write_text(
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
        """3 个真跑单元（value_error / mirror_narrative 跑图，illegal_price 预判 void）。"""
        report, meter_calls = _run(tmp_path)
        real = report["budget"]["real_run"]
        assert real["units"] == 3
        assert real["units_by_status"] == {"ok": 0, "void_injection": 3, "error": 0}
        # 旧实现只算 ok 单元 → llm_calls/runs 均为 0（消耗被静默丢掉）
        assert real["runs"] == 4  # 2 个可施加单元 × 2 态；预判 void 的单元 0 次
        assert real["runs_unknown_units"] == 0
        assert real["llm_calls"] == 2 * 2 * 5
        assert real["llm_calls_unknown_units"] == 0
        assert real["llm_calls_by_status"]["void_injection"] == real["llm_calls"]
        assert meter_calls == real["llm_calls"]

    def test_meter_total_cross_checks_unit_sum(self, tmp_path: Path):
        report, meter_calls = _run(tmp_path)
        real = report["budget"]["real_run"]
        assert real["llm_meter_total_calls"] == meter_calls
        assert real["llm_meter_matches_unit_sum"] is True

    def test_unapplicable_injection_unit_never_ran(self, tmp_path: Path):
        report, _ = _run(tmp_path)
        illegal = [u for u in report["units"] if u["pollution_type"] == "illegal_price"]
        assert len(illegal) == 1
        unit = illegal[0]
        assert unit["status"] == pr.STATUS_VOID
        assert unit["injection"]["applied"] is False
        assert unit["llm_calls"] == 0
        assert unit["graph_runs"] == 0
        assert "预判" in unit["status_reason"]
        # 该型其余单元照常跑图（本批 4 次运行 = value_error 2 + mirror_narrative 2）
        ran = [u for u in report["units"] if u["graph_runs"] == 2]
        assert {u["pollution_type"] for u in ran} == {"value_error", "mirror_narrative"}
        assert report["budget"]["offline_replay"]["units"] == 0  # legs=real 不跑离线腿
