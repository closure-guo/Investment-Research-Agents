"""人工终裁材料：证据回填 + 去重案例表 + 机器预读（spec「逃逸须终裁确认」）。

口径：机器预读只是证据整理，SHALL NOT 被读成终裁（human_verdict 列由人填写）。
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

from evals.causal_ablation.adjudication import (
    READING_COUNTERFACTUAL,
    READING_COVERAGE_GAP,
    READING_ESCAPE,
    READING_INERT,
    READING_NO_SURFACE,
    READING_SELF_CERTIFIED,
    READING_SPURIOUS,
    adjudicable_units,
    adjudicated_case_ids,
    case_groups,
    machine_reading,
    worklist_rows,
    write_cases_csv,
    write_worklist_csv,
)

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "tests" / "scripts" / "p1_adjudication_material.py"


def _load_cli():
    """以模块方式加载材料生成脚本（tests/scripts 非包，走 importlib——同 test_pilot_cli）。"""
    spec = importlib.util.spec_from_file_location("p1_adjudication_material_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _unit(
    unit_id: str,
    *,
    pollution_type: str = "direction_error",
    mechanism_id: str = "A3",
    ticker: str = "600519",
    on_state: str = "escaped",
    off_state: str = "escaped",
    status: str = "ok",
    injected: dict | None = None,
    polluted_values: list | None = None,
    on_claim: dict | None = None,
    on_verdict: dict | None = None,
    on_flagged: bool = False,
    mutated_flat_index: int | None = 0,
    mutated_fields: list | None = None,
) -> dict:
    claim = on_claim or {"field_ref": "growth_rates.profitability.营业收入", "stated_value": -1.21}
    verdict = on_verdict or {"status": "PASS", "bucket": None, "ground_truth": -1.21, "delta": 0.0}
    return {
        "unit_id": unit_id,
        "case_id": unit_id.split("::")[0],
        "ticker": ticker,
        "leg": "offline_replay",
        "cost_class": "offline_replay",
        "pollution_type": pollution_type,
        "injection_point": "analyst_output",
        "mechanism_id": mechanism_id,
        "on_state": on_state,
        "off_state": off_state,
        "status": status,
        "status_reason": "",
        "llm_calls": 0,
        "evidence_paths": [f"unit:{unit_id}"],
        "injection": {
            "polluted_values": polluted_values if polluted_values is not None else [-1.21],
            "mutated_flat_index": mutated_flat_index,
            "injected_values": injected or {"direction": "positive"},
            "mutated_fields": mutated_fields or ["fundamental.claims[0].direction"],
        },
        "on": {
            "claims": [claim],
            "verdicts": [verdict],
            "flagged": on_flagged,
            "polluted_present": True,
        },
        "off": {
            "claims": [claim],
            "verdicts": [{"status": "PASS", "bucket": None}],
            "flagged": False,
            "polluted_present": True,
        },
    }


class TestWorklistRows:
    def test_only_ok_and_off_escaped_units_enter_worklist(self):
        units = [
            _unit("a::offline_replay"),
            _unit("b::offline_replay", off_state="caught"),
            _unit("c::offline_replay", status="void_injection"),
            _unit("d::offline_replay", status="error"),
        ]
        rows = worklist_rows(units)
        assert [r["unit_id"] for r in rows] == ["a::offline_replay"]

    def test_row_carries_per_state_evidence(self):
        """四列证据必须回填（round-2 交付里全空——人工无从判读）。"""
        rows = worklist_rows([_unit("a::offline_replay", on_flagged=True, on_state="caught")])
        row = rows[0]
        assert row["on_polluted_present"] is True
        assert row["on_flagged"] is True
        assert row["off_polluted_present"] is True
        assert row["off_flagged"] is False

    def test_row_carries_flag_bucket(self):
        """拦截原因桶：伪拦截（path_unresolvable）与真拦（direction_mismatch）必须可分辨。"""
        unit = _unit(
            "a::offline_replay",
            on_flagged=True,
            on_state="caught",
            on_verdict={"status": "FAIL", "bucket": "direction_mismatch"},
        )
        assert worklist_rows([unit])[0]["on_flag_bucket"] == "direction_mismatch"

    def test_state_side_pollution_has_no_flag_bucket(self):
        unit = _unit("a::offline_replay", mutated_flat_index=None, polluted_values=["2026-05-01"])
        assert worklist_rows([unit])[0]["on_flag_bucket"] == ""


class TestCaseGroups:
    def test_identical_instances_collapse_to_one_case(self):
        units = [_unit(f"600519-direction_error-{i}::offline_replay") for i in range(4)]
        groups = case_groups(units)
        assert len(groups) == 1
        assert groups[0]["instances"] == 4
        assert groups[0]["unit_ids"] == [u["unit_id"] for u in units]

    def test_different_injection_splits_cases(self):
        units = [
            _unit("600519-direction_error-0::offline_replay", injected={"direction": "positive"}),
            _unit("600519-direction_error-1::offline_replay", injected={"direction": "negative"}),
        ]
        assert len(case_groups(units)) == 2

    def test_case_key_carries_ticker_and_type(self):
        groups = case_groups([_unit("600519-direction_error-0::offline_replay")])
        assert groups[0]["ticker"] == "600519"
        assert groups[0]["pollution_type"] == "direction_error"

    def test_cases_cover_worklist_population_exactly(self):
        """案例表与单元清单同一口径：实例数之和 == 清单行数（否则案例表虚增）。"""
        units = [
            _unit("a-0::offline_replay"),
            _unit("a-1::offline_replay"),
            _unit("b-0::offline_replay", off_state="caught"),
            _unit("c-0::offline_replay", status="void_injection"),
        ]
        population = adjudicable_units(units)
        assert len(worklist_rows(units)) == 2
        assert sum(g["instances"] for g in case_groups(population)) == 2

    def test_case_row_labels_delta_readability(self):
        """FAIL 的 delta 是未归一原始差（面值百分数 vs 真值小数会跨单位）→ 逐行标注读法。"""
        caught = _unit(
            "600519-direction_error-0::offline_replay",
            on_flagged=True,
            on_state="caught",
            on_verdict={
                "status": "FAIL",
                "bucket": "direction_mismatch",
                "ground_truth": 0.4228,
                "delta": 42.70,
            },
        )
        semantic = _unit(
            "600519-period_shift-0::offline_replay",
            pollution_type="period_shift",
            mechanism_id="A2",
            on_flagged=True,
            on_state="caught",
            on_verdict={"status": "FAIL", "bucket": "semantic_period_mismatch"},
        )
        assert "不读作幅度" in case_groups([caught])[0]["delta_note"]
        assert "无 delta" in case_groups([semantic])[0]["delta_note"]

    def test_case_row_carries_polluted_field(self):
        """终裁要知道「动了哪条 claim」——污染值列不足以定位（方向型尤其）。"""
        units = [_unit("600519-direction_error-0::offline_replay")]
        groups = case_groups(units)
        assert groups[0]["polluted_field"] == "growth_rates.profitability.营业收入"

    def test_input_side_types_excluded_from_adjudication(self):
        """输入侧面型（A7）无「拦截/逃逸」语义 → 不进清单（判读走输入侧证据块）。"""
        units = [_unit("a::offline_replay", pollution_type="stale_macro", mechanism_id="A7")]
        assert adjudicable_units(units) == []
        assert worklist_rows(units) == []


class TestAdjudicatedCaseIds:
    """终裁回填 → escape_rate 的入参：只认**确认真逃逸**的行（其余为 pending）。"""

    def test_expands_case_level_unit_ids(self, tmp_path):
        path = tmp_path / "cases.csv"
        path.write_text(
            "case_key,unit_ids,human_verdict(真逃逸?/误报?/待查)\n"
            '"a-0","[""a-0::offline_replay"", ""a-1::offline_replay""]",真逃逸\n'
            '"b-0","[""b-0::offline_replay""]",误报\n'
            '"c-0","[""c-0::offline_replay""]",\n',
            encoding="utf-8-sig",
        )
        assert adjudicated_case_ids(path) == {"a-0", "a-1"}

    def test_reads_unit_level_rows(self, tmp_path):
        path = tmp_path / "units.csv"
        path.write_text(
            "unit_id,human_verdict(真逃逸?/误报?/待查)\n"
            "u-0::offline_replay,真逃逸\n"
            "u-1::offline_replay,误报\n",
            encoding="utf-8-sig",
        )
        assert adjudicated_case_ids(path) == {"u-0"}

    def test_missing_file_yields_empty_set(self, tmp_path):
        assert adjudicated_case_ids(tmp_path / "nope.csv") == set()


class TestMachineReading:
    def test_caught_with_attributable_bucket_is_counterfactual_escape(self):
        unit = _unit(
            "a",
            on_flagged=True,
            on_state="caught",
            on_verdict={"status": "FAIL", "bucket": "direction_mismatch"},
        )
        assert machine_reading(unit) == READING_COUNTERFACTUAL

    def test_path_unresolvable_is_spurious_catch(self):
        """FAIL 来自 field_ref 本就解析不出 → 与污染无关，不得算机制功劳。"""
        unit = _unit(
            "a",
            on_flagged=True,
            on_state="caught",
            on_verdict={"status": "FAIL", "bucket": "path_unresolvable"},
        )
        assert machine_reading(unit) == READING_SPURIOUS

    def test_unverifiable_is_coverage_gap(self):
        unit = _unit(
            "a",
            on_verdict={"status": "UNVERIFIABLE", "bucket": "comparative_delta_unregistered"},
        )
        assert machine_reading(unit).startswith(READING_COVERAGE_GAP)

    def test_state_side_pollution_has_no_surface(self):
        unit = _unit("a", mutated_flat_index=None)
        assert machine_reading(unit) == READING_NO_SURFACE

    def test_injected_source_in_snapshot_is_self_certified(self):
        """假事件与假引用同源注入（mutated_fields 含 snapshot.*）→ 出处是注入自造的。"""
        headline = "某公司宣布重大战略合作（注入）"
        unit = _unit(
            "a",
            pollution_type="fabricated_event",
            polluted_values=[headline],
            on_verdict={"status": "PASS", "bucket": None, "unit_normalized": "echo"},
            mutated_fields=["technical.claims[20]", "snapshot.news_list"],
        )
        assert machine_reading(unit) == READING_SELF_CERTIFIED

    def test_pass_within_tolerance_is_inert(self):
        unit = _unit(
            "a",
            on_verdict={
                "status": "PASS",
                "bucket": None,
                "ground_truth": 14195371894.42,
                "delta": 371894.42,
            },
        )
        assert machine_reading(unit) == READING_INERT

    def test_pass_normalized_unit_is_inert(self):
        unit = _unit(
            "a",
            on_verdict={
                "status": "PASS",
                "bucket": None,
                "ground_truth": -0.01206,
                "delta": 3.99e-05,
                "unit_normalized": "percent",
            },
        )
        assert machine_reading(unit) == READING_INERT

    def test_pass_with_real_discrepancy_is_escape_candidate(self):
        unit = _unit(
            "a",
            on_verdict={
                "status": "PASS",
                "bucket": None,
                "ground_truth": 91.18,
                "delta": 50.0,
            },
        )
        assert machine_reading(unit) == READING_ESCAPE


class TestWriters:
    def test_worklist_csv_preserves_existing_human_verdict(self, tmp_path):
        """重生成不得覆盖人工已填的裁定（按 unit_id 继承）。"""
        path = tmp_path / "worklist.csv"
        path.write_text(
            "unit_id,ticker,pollution_type,mechanism_id,leg,on_state,off_state,"
            "on_polluted_present,on_flagged,off_polluted_present,off_flagged,polluted_values,"
            "evidence_paths,human_verdict(真逃逸?/误报?/待查)\n"
            "a::offline_replay,600519,direction_error,A3,offline_replay,escaped,escaped,,"
            ",,,,x,真逃逸\n",
            encoding="utf-8",
        )
        write_worklist_csv(path, worklist_rows([_unit("a::offline_replay")]))
        rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
        assert rows[0]["human_verdict(真逃逸?/误报?/待查)"] == "真逃逸"
        assert rows[0]["on_flagged"] == "False"
        assert rows[0]["on_polluted_present"] == "True"

    def test_cases_csv_writes_one_row_per_case(self, tmp_path):
        path = tmp_path / "cases.csv"
        units = [_unit(f"600519-direction_error-{i}::offline_replay") for i in range(4)]
        write_cases_csv(path, case_groups(units))
        rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
        assert len(rows) == 1
        assert rows[0]["instances"] == "4"
        assert json.loads(rows[0]["unit_ids"]) == [u["unit_id"] for u in units]


class TestCli:
    def test_cli_writes_both_csvs_from_resume(self, tmp_path):
        resume = tmp_path / "resume.json"
        units = [_unit(f"600519-direction_error-{i}::offline_replay") for i in range(4)] + [
            _unit("600519-stale_macro-0::offline_replay", mutated_flat_index=None)
        ]
        resume.write_text(json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")
        worklist, cases = tmp_path / "worklist.csv", tmp_path / "cases.csv"
        code = cli.main(
            ["--resume", str(resume), "--worklist", str(worklist), "--cases", str(cases)]
        )
        assert code == 0
        assert len(list(csv.DictReader(worklist.read_text(encoding="utf-8-sig").splitlines()))) == 5
        assert len(list(csv.DictReader(cases.read_text(encoding="utf-8-sig").splitlines()))) == 2

    def test_locked_output_reports_actionable_error(self, tmp_path, monkeypatch, capsys):
        """材料常被 Excel/WPS 打开：被占用时给可执行提示（退出码 3），不甩 traceback。"""
        resume = tmp_path / "resume.json"
        resume.write_text(json.dumps({"units": []}), encoding="utf-8")

        def boom(path, rows):
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr(cli, "write_cases_csv", boom)
        code = cli.main(
            [
                "--resume",
                str(resume),
                "--worklist",
                str(tmp_path / "w.csv"),
                "--cases",
                str(tmp_path / "c.csv"),
            ]
        )
        assert code == 3
        assert "被占用" in capsys.readouterr().err

    def test_missing_resume_is_refused_without_writing(self, tmp_path):
        worklist, cases = tmp_path / "worklist.csv", tmp_path / "cases.csv"
        code = cli.main(
            [
                "--resume",
                str(tmp_path / "missing.json"),
                "--worklist",
                str(worklist),
                "--cases",
                str(cases),
            ]
        )
        assert code == 2
        assert not worklist.exists() and not cases.exists()
