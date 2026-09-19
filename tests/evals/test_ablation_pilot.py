"""消融驱动测试（delta judge-enumeration-cap-and-ablation-materials §2）。

零 LLM：ablation 模块与 usage meter 全部注入假件，只验驱动层契约——
① judge 材料按 `(variant, ticker, repeat)` 落盘且内容即 judge_vars；
② run 记录携带材料路径与 judge 明细（分数/纯定性条数/是否封顶/枚举是否缺失）；
③ `--tickers/--repeats` 参数化并写入产物 config；
④ 断点续跑键仍为 `(variant, ticker, repeat)`，已完成 run 不重复消费。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tests" / "scripts" / "ablation_pilot.py"


def _load_module():
    """以模块方式加载脚本（tests/scripts 非包，走 importlib）。"""
    spec = importlib.util.spec_from_file_location("ablation_pilot_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pilot = _load_module()


class _FakeAblation:
    """假 ablation 模块：不跑管线、不调 LLM。

    判分入口 `score_run` 与库侧同契约（G1/1.2）：维度过滤/均值判分由假件替代
    （不碰 LLM），明细塑形与材料落盘直接复用库侧纯函数（真实现）——假件不得
    掩盖这两段库侧契约的漂移。
    """

    JUDGE_DIMS = ["report_relevance", "debate_quality", "decision_grounding", "consistency"]
    _VARIANTS = ["analysts", "plus_debate", "full"]

    def __init__(self):
        self.verify_calls = 0
        self.run_calls: list[tuple[str, str]] = []
        self.judge_calls: list[str] = []
        self.score_calls: list[tuple] = []
        self.direct_judge_calls = 0  # 驱动绕过库侧入口直接判分的次数（须恒 0）
        self._scoring = False

    def _applicable_dims(self, variant: str) -> tuple[str, ...]:
        """与 `evals.ablation._applicable_dims` 同实现（#112 维度适用性）。"""
        if variant == "analysts":
            return ("report_relevance",)
        if variant == "plus_debate":
            return ("report_relevance", "debate_quality")
        if variant == "through_trader":
            return ("report_relevance", "debate_quality", "decision_grounding")
        return tuple(self.JUDGE_DIMS)

    # 被驱动 monkeypatch 的入口
    def verify_citations(self, state: dict) -> dict:
        self.verify_calls += 1
        return {"citation_pass": True, "citation_coverage": 0.95}

    # citation 腿四桶拆报（G2/1.3）：假件按库侧真协议返回（桶名/校验走共享常量）
    _BUCKET_COUNTS = {
        "analysts": {"blocked": 1, "analyst_true_fail": 2, "verifier_normalized": 3},
        "plus_debate": {
            "blocked": 0,
            "analyst_true_fail": 1,
            "surgical_repaired": 1,
            "verifier_normalized": 2,
        },
        "full": {"analyst_true_fail": 0, "surgical_repaired": 2, "verifier_normalized": 1},
    }

    def run_variant_once(self, variant: str, snapshot: dict, query: str) -> dict:
        from evals.causal_ablation.escape import split_verifier_buckets

        self.run_calls.append((variant, query))
        # 锚点覆盖率（delta add-debate-argument-anchors 4.4）：analysts 无辩论层 → None；
        # 真实协议为 value + 拆项（与 evals/task.py 的 detail 同口径）
        coverage = {
            "plus_debate": {
                "value": 0.5,
                "total": 4,
                "anchored": 2,
                "unanchored_inference": 1,
                "unresolved": 1,
                "missing_required": 0,
                "unspecified": 0,
            },
            "full": {
                "value": 0.75,
                "total": 4,
                "anchored": 3,
                "unanchored_inference": 0,
                "unresolved": 1,
                "missing_required": 0,
                "unspecified": 0,
            },
        }.get(variant)
        return {
            "final_report": f"report-{variant}",
            "citation_pass": True,
            "judge_vars": {"debate_history": f"【bull】论点: {variant}"},
            "decision": None,
            "anchor_coverage": coverage,
            "citation_buckets": split_verifier_buckets(self._BUCKET_COUNTS[variant]),
        }

    def build_snapshot(self, ticker: str) -> dict:
        return {"stock_code": ticker}

    def snapshot_digest(self, state: dict) -> str:
        return f"digest-{state['stock_code']}"

    def run_judge(self, dimension: str, variables: dict) -> dict:
        # debate 维度返回带遥测的结果（模拟 v6 封顶）
        self.judge_calls.append(dimension)
        if dimension == "debate_quality":
            return {
                "name": dimension,
                "score": 4,
                "reason": "标头含纯定性论点",
                "confidence": 0.8,
                "points": [],
                "qualitative_points": 1,
                "cap_applied": True,
                "enumeration_missing": False,
            }
        return {"name": dimension, "score": 5, "reason": "ok", "confidence": 0.9}

    def run_judge_mean(self, dimension: str, variables: dict, *, repeats: int = 3) -> dict:
        """假均值协议：debate 维度模拟 [5,4,4]（round11 实测形态）。"""
        if not self._scoring:
            self.direct_judge_calls += 1  # 驱动不得绕过库侧入口自行判分（G1/1.2）
        self.judge_calls.append(dimension)
        if dimension == "debate_quality":
            return {
                "name": dimension,
                "score": 4.333 if repeats >= 3 else 4.0,
                "reason": "标头含纯定性论点",
                "confidence": 0.8,
                "points": [],
                "qualitative_points": 1,
                "cap_applied": False,
                "enumeration_missing": False,
                "scores": [5, 4, 4] if repeats >= 3 else [4],
                "score_spread": 1 if repeats >= 3 else 0,
                "judge_repeats": repeats,
                "judge_failures": 0,
            }
        return {
            "name": dimension,
            "score": 5,
            "reason": "ok",
            "confidence": 0.9,
            "scores": [5] * repeats,
            "score_spread": 0,
            "judge_repeats": repeats,
            "judge_failures": 0,
        }

    def score_run(
        self,
        variant: str,
        out: dict,
        *,
        ticker: str,
        repeat: int,
        judge_repeats: int = 3,
        materials_dir: Path | None = None,
    ) -> dict:
        """库侧唯一判分入口的同契约假件（G1/1.2，零 LLM）。

        契约与 `evals.ablation.score_run` 一致：维度过滤 + K 次均值 + 明细塑形 +
        材料落盘 + 锚点字段；塑形/落盘走库侧真函数，判分走假 judge。
        """
        from evals import ablation as _lib

        self.score_calls.append((variant, ticker, repeat, judge_repeats, materials_dir))
        judge_scores: dict[str, float | None] = {}
        judge_details: dict[str, dict] = {}
        self._scoring = True
        try:
            for dim in self.JUDGE_DIMS:
                if dim not in self._applicable_dims(variant):
                    judge_scores[dim] = None
                    continue
                result = self.run_judge_mean(dim, out["judge_vars"], repeats=judge_repeats)
                judge_scores[dim] = float(result["score"]) if result["score"] is not None else None
                judge_details[dim] = _lib.judge_detail(result)
        finally:
            self._scoring = False
        coverage = out.get("anchor_coverage")
        return {
            "judge": judge_scores,
            "judge_detail": judge_details,
            "materials_path": (
                _lib.persist_materials(materials_dir, variant, ticker, repeat, out["judge_vars"])
                if materials_dir is not None
                else None
            ),
            "argument_anchor_coverage": (coverage or {}).get("value"),
            "argument_anchor_coverage_detail": (
                {k: v for k, v in coverage.items() if k != "value"} if coverage else None
            ),
        }

    def aggregate_results(self, runs: list[dict]) -> dict:
        return {"n_runs": len(runs)}


class _FakeMeter:
    def __init__(self):
        self._usage_ledger: list[dict] = []


@pytest.fixture
def env(tmp_path):
    return SimpleNamespace(
        ablation=_FakeAblation(),
        meter=_FakeMeter(),
        resume=tmp_path / "resume.json",
        materials=tmp_path / "judge_vars",
        out=tmp_path / "out",
    )


def _run(env, **kwargs):
    return pilot.run_pilot(
        tickers=kwargs.pop("tickers", ["600519"]),
        repeats=kwargs.pop("repeats", 1),
        resume_path=env.resume,
        materials_dir=env.materials,
        out_dir=env.out,
        ablation_mod=env.ablation,
        meter=env.meter,
        prompt_versions_fn=lambda: {"debate_quality": "v6"},
        **kwargs,
    )


class TestMaterialPersistence:
    def test_materials_written_per_run(self, env):
        _run(env, tickers=["600519", "000001"], repeats=2)
        names = sorted(p.name for p in env.materials.glob("*.json"))
        assert names == sorted(
            f"{variant}-{ticker}-{i}.json"
            for variant in ["analysts", "plus_debate", "full"]
            for ticker in ["600519", "000001"]
            for i in range(2)
        ), "每个 (variant, ticker, repeat) 一条材料"

    def test_material_content_is_judge_vars(self, env):
        _run(env)
        payload = json.loads((env.materials / "full-600519-0.json").read_text(encoding="utf-8"))
        assert payload == {"debate_history": "【bull】论点: full"}

    def test_run_record_carries_materials_path(self, env):
        path = _run(env, tickers=["600519"], repeats=1)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        runs = artifact["run_costs"]
        assert len(runs) == 3  # 1 标的 × 3 变体 × 1 重复
        for row in runs:
            assert row["materials_path"].endswith(
                f"{row['variant']}-{row['ticker']}-{row['repeat']}.json"
            )
            assert (env.materials / Path(row["materials_path"]).name).exists()

    def test_judge_detail_recorded(self, env):
        path = _run(env)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        full = next(r for r in artifact["run_costs"] if r["variant"] == "full")
        detail = full["judge_detail"]["debate_quality"]
        assert detail["score"] == 4.333
        assert detail["cap_applied"] is False
        assert detail["qualitative_points"] == 1
        assert detail["enumeration_missing"] is False
        assert detail["scores"] == [5, 4, 4], "每次分数随 run 落盘（噪声可见）"
        assert detail["score_spread"] == 1
        assert detail["judge_repeats"] == pilot.JUDGE_REPEATS


class TestAnchorCoverageRecord:
    """delta add-debate-argument-anchors 4.4：驱动 run 记录携带 argument_anchor_coverage。"""

    def test_run_record_carries_anchor_coverage_value_and_detail(self, env):
        path = _run(env, tickers=["600519"], repeats=1)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        by_variant = {r["variant"]: r for r in artifact["run_costs"]}
        assert by_variant["analysts"]["argument_anchor_coverage"] is None, "无辩论层不得伪造 0"
        assert by_variant["analysts"]["argument_anchor_coverage_detail"] is None
        assert by_variant["plus_debate"]["argument_anchor_coverage"] == 0.5
        assert by_variant["full"]["argument_anchor_coverage"] == 0.75
        assert by_variant["plus_debate"]["argument_anchor_coverage_detail"] == {
            "total": 4,
            "anchored": 2,
            "unanchored_inference": 1,
            "unresolved": 1,
            "missing_required": 0,
            "unspecified": 0,
        }, "拆项（去 value）随 run 记录落盘，供归因"
        assert by_variant["full"]["argument_anchor_coverage_detail"] == {
            "total": 4,
            "anchored": 3,
            "unanchored_inference": 0,
            "unresolved": 1,
            "missing_required": 0,
            "unspecified": 0,
        }

    def test_resume_record_carries_anchor_coverage_value_and_detail(self, env):
        """断点续跑台账与产物同批落盘 → resume 记录同样携带 value 与拆项。"""
        _run(env, tickers=["600519"], repeats=1)
        resume = json.loads(env.resume.read_text(encoding="utf-8"))
        by_variant = {r["variant"]: r for r in resume["runs"]}
        assert by_variant["analysts"]["argument_anchor_coverage"] is None
        assert by_variant["analysts"]["argument_anchor_coverage_detail"] is None
        assert by_variant["full"]["argument_anchor_coverage"] == 0.75
        assert by_variant["full"]["argument_anchor_coverage_detail"]["anchored"] == 3
        assert by_variant["full"]["argument_anchor_coverage_detail"]["unanchored_inference"] == 0


class TestCitationBucketsRecord:
    """G2（delta tasks 1.3/F3）：驱动 run 记录携带 citation 四桶拆报（additive）。

    标量 `citation_pass` 仍产出（向后兼容），但不进层增量结论——层增量由四桶承担。
    """

    _ANALYSTS = {
        "blocked": 1,
        "analyst_true_fail": 2,
        "surgical_repaired": 0,
        "verifier_normalized": 3,
        "claim_contract_error": 0,
        "total": 6,
    }
    _FULL = {
        "blocked": 0,
        "analyst_true_fail": 0,
        "surgical_repaired": 2,
        "verifier_normalized": 1,
        "claim_contract_error": 0,
        "total": 3,
    }

    def test_run_record_carries_citation_buckets(self, env):
        path = _run(env, tickers=["600519"], repeats=1)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        by_variant = {r["variant"]: r for r in artifact["run_costs"]}
        assert by_variant["analysts"]["citation_buckets"] == self._ANALYSTS
        assert by_variant["full"]["citation_buckets"] == self._FULL
        # additive：既有字段原样保留（标量 / 锚点 / judge）
        assert by_variant["full"]["citation_pass"] is True
        assert by_variant["full"]["argument_anchor_coverage"] == 0.75
        assert by_variant["full"]["judge"]["report_relevance"] == 5
        assert by_variant["full"]["judge_detail"]["debate_quality"]["score"] == 4.333

    def test_resume_record_carries_citation_buckets(self, env):
        """断点续跑台账与产物同批落盘 → resume 记录同样携带四桶。"""
        _run(env, tickers=["600519"], repeats=1)
        resume = json.loads(env.resume.read_text(encoding="utf-8"))
        by_variant = {r["variant"]: r for r in resume["runs"]}
        assert by_variant["analysts"]["citation_buckets"] == self._ANALYSTS
        assert by_variant["plus_debate"]["citation_buckets"]["surgical_repaired"] == 1
        assert by_variant["full"]["citation_buckets"] == self._FULL


class TestParameterization:
    def test_config_records_actual_tickers_and_repeats(self, env):
        path = _run(env, tickers=["600519", "300308"], repeats=2)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        assert artifact["config"]["tickers"] == ["600519", "300308"]
        assert artifact["config"]["repeats"] == 2
        assert artifact["config"]["judge_repeats"] == pilot.JUDGE_REPEATS
        assert artifact["config"]["materials_dir"].endswith("judge_vars")
        assert len(artifact["run_costs"]) == 2 * 3 * 2

    def test_resume_keys_unchanged(self, env):
        _run(env, tickers=["600519"], repeats=2)
        resume = json.loads(env.resume.read_text(encoding="utf-8"))
        keys = {tuple(k) for k in resume["done_keys"]}
        assert keys == {
            (variant, "600519", i)
            for variant in ["analysts", "plus_debate", "full"]
            for i in range(2)
        }

    def test_second_run_skips_completed(self, env):
        _run(env, tickers=["600519"], repeats=1)
        calls_after_first = len(env.ablation.run_calls)
        _run(env, tickers=["600519"], repeats=1)  # 同 resume → 全部跳过
        assert len(env.ablation.run_calls) == calls_after_first, "已完成 run 不得重复消耗"

    def test_extending_repeats_only_runs_new(self, env):
        _run(env, tickers=["600519"], repeats=1)
        calls_after_first = len(env.ablation.run_calls)
        _run(env, tickers=["600519"], repeats=2)  # 只应新增 repeat=1 的三条
        assert len(env.ablation.run_calls) == calls_after_first + 3


class TestCli:
    def test_parse_args_defaults(self):
        args = pilot.parse_args([])
        assert args.tickers == pilot.TICKERS
        assert args.repeats == pilot.REPEATS

    def test_parse_args_overrides(self):
        args = pilot.parse_args(
            ["--tickers", "600519", "000001", "--repeats", "10", "--judge-repeats", "5"]
        )
        assert args.tickers == ["600519", "000001"]
        assert args.repeats == 10
        assert args.judge_repeats == 5

    def test_judge_repeats_default_is_mean_protocol(self):
        assert pilot.JUDGE_REPEATS >= 3, "默认须为多次均值（单次调用 5/4 边界有 ±1 噪声）"

    def test_main_forwards_judge_repeats_end_to_end(self, env, monkeypatch):
        """G7/⑧ 回归：CLI `--judge-repeats` 须经 `main()` 透传到判分与产物。

        历史形态：`main()` 调 `run_pilot(...)` 时漏传 `judge_repeats`，旗标静默失效
        （产出的 `config["judge_repeats"]` 恒为默认值，判分也按默认 K 次跑）。
        """
        captured: dict = {}
        produced: dict = {}
        real_run_pilot = pilot.run_pilot

        def spy(**kwargs):
            captured.update(kwargs)
            # 只注入假件；路径类参数由 CLI 传入 tmp_path（不碰仓库内 reports/）
            produced["path"] = real_run_pilot(
                **kwargs,
                ablation_mod=env.ablation,
                meter=env.meter,
                prompt_versions_fn=lambda: {"debate_quality": "v6"},
            )
            return produced["path"]

        monkeypatch.setattr(pilot, "run_pilot", spy)
        monkeypatch.setattr(pilot.pilot_util, "_pin_pipeline_model", lambda: None)
        monkeypatch.setattr(pilot.pilot_util, "install_usage_meter", lambda: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "ablation_pilot.py",
                "--tickers",
                "600519",
                "--repeats",
                "1",
                "--judge-repeats",
                "5",
                "--resume",
                str(env.resume),
                "--materials-dir",
                str(env.materials),
                "--out-dir",
                str(env.out),
            ],
        )
        pilot.main()

        assert captured["judge_repeats"] == 5, "CLI 旗标不得静默失效"
        assert captured["tickers"] == ["600519"] and captured["repeats"] == 1
        artifact = json.loads(produced["path"].read_text(encoding="utf-8"))
        assert artifact["config"]["judge_repeats"] == 5, "CLI → 产物 config"
        assert [call[3] for call in env.ablation.score_calls] == [5] * 3, (
            "CLI → 库侧判分入口的 K 次均值参数"
        )


class TestApplicableDimsParity:
    """驱动不得自带变体→维度映射（#112）：唯一实现在库侧，驱动必须问库侧。"""

    def test_fake_matches_library_attribute_surface(self):
        """假件照库侧实现提供 `_applicable_dims`/`score_run`——库侧改名/删除时本用例转红。"""
        from evals import ablation as real_ablation

        assert hasattr(real_ablation, "_applicable_dims"), "库侧适用性过滤的唯一实现"
        assert callable(getattr(real_ablation, "score_run", None)), (
            "库侧判分+落盘入口的唯一实现（G1/1.2）"
        )
        assert callable(getattr(real_ablation, "split_verifier_buckets", None)), (
            "库侧须透出四桶拆报入口（G2/1.3）——假件按该入口构造 run 记录"
        )
        for variant in real_ablation._VARIANTS:
            assert _FakeAblation()._applicable_dims(variant) == real_ablation._applicable_dims(
                variant
            ), f"假件与库侧的 {variant} 维度集合必须一致"

    def test_nonexistent_layers_not_judged(self, env):
        """plus_debate 无 Trader/风控/FM 层 → grounding/consistency 记 None 且不判分。"""
        path = _run(env, tickers=["600519"], repeats=1)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        row = next(r for r in artifact["run_costs"] if r["variant"] == "plus_debate")
        assert row["judge"]["decision_grounding"] is None
        assert row["judge"]["consistency"] is None
        assert "decision_grounding" not in row["judge_detail"], "过滤维度不得产生 judge 调用"
        assert "consistency" not in row["judge_detail"]

    def test_filtered_dims_produce_no_judge_calls(self, env):
        """1 标的 × 1 重复的判分次数：analysts 1 + plus_debate 2 + full 4 = 7。

        硬编码 analysts 分支（bug 形态）会判 1 + 4 + 4 = 9——次数差异即 #112 的复现点。
        """
        _run(env, tickers=["600519"], repeats=1)
        assert env.ablation.judge_calls.count("decision_grounding") == 1, "仅 full 评该维度"
        assert env.ablation.judge_calls.count("consistency") == 1, "仅 full 评该维度"
        assert len(env.ablation.judge_calls) == 7

    def test_driver_fails_without_library_scoring_entry(self, env):
        """库侧不暴露 `score_run` 时驱动必须炸——证明判分只经库侧入口而非驱动自算。"""

        class _FakeWithoutScoringEntry(_FakeAblation):
            @property
            def score_run(self):
                raise AttributeError("score_run")

        env.ablation = _FakeWithoutScoringEntry()
        with pytest.raises(AttributeError):
            _run(env, tickers=["600519"], repeats=1)


class TestScoringDelegation:
    """G1（delta tasks 1.2）：判分/过滤/明细/落盘只经库侧 `score_run`，驱动不得内联。"""

    def test_driver_scores_each_run_via_library_entry(self, env):
        """每 run 恰好一次库侧判分调用，且带 run 键 / judge_repeats / 材料目录。"""
        _run(env, tickers=["600519"], repeats=2, judge_repeats=5)
        assert [call[:4] for call in env.ablation.score_calls] == [
            (variant, "600519", repeat, 5)
            for variant in ["analysts", "plus_debate", "full"]
            for repeat in range(2)
        ], "run 键与 judge_repeats 须经库侧入口透传"
        assert all(call[4] == env.materials for call in env.ablation.score_calls), (
            "材料目录须交给库侧落盘，驱动不得自行写 judge_vars"
        )

    def test_driver_does_not_run_own_judge_loop(self, env):
        """驱动侧直接判分计数须为 0（判分只发生在 score_run 内部）。"""
        _run(env)
        assert env.ablation.direct_judge_calls == 0, "驱动不得绕过库侧入口调用 run_judge_mean"
        assert len(env.ablation.judge_calls) == 7, "判分仍按适用维度发生（1 + 2 + 4）"

    def test_driver_delegates_materials_and_detail_to_real_library(self, env, monkeypatch):
        """真库侧 `score_run` 接线：材料由库侧真写盘、明细由库侧真塑形（假件不掩盖契约）。"""
        import evals.ablation as real_ablation

        monkeypatch.setattr(
            real_ablation,
            "verify_citations",
            lambda state: {"citation_pass": True, "citation_coverage": 0.9},
        )
        monkeypatch.setattr(real_ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(
            real_ablation, "snapshot_digest", lambda state: f"digest-{state['stock_code']}"
        )
        monkeypatch.setattr(
            real_ablation,
            "run_variant_once",
            lambda variant, snapshot, query: {
                "final_report": f"report-{variant}",
                "citation_pass": True,
                "judge_vars": {"debate_history": f"【bull】论点: {variant}"},
                "anchor_coverage": (
                    None
                    if variant == "analysts"
                    else {
                        "value": 0.5,
                        "total": 2,
                        "anchored": 1,
                        "unanchored_inference": 1,
                        "unresolved": 0,
                        "missing_required": 0,
                        "unspecified": 0,
                    }
                ),
                # 真库协议含四桶（G2/F3）：假件缺该键即属协议漂移
                "citation_buckets": {
                    "blocked": 0,
                    "analyst_true_fail": 1,
                    "surgical_repaired": 0,
                    "verifier_normalized": 2,
                    "total": 3,
                },
            },
        )
        monkeypatch.setattr(
            real_ablation, "aggregate_results", lambda runs, **kwargs: {"n_runs": len(runs)}
        )
        monkeypatch.setattr(
            real_ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {
                "score": 4.0,
                "scores": [4] * repeats,
                "score_spread": 0,
                "judge_repeats": repeats,
                "judge_failures": 0,
            },
        )
        original = getattr(real_ablation, "score_run", None)
        assert original is not None, "库侧须暴露唯一判分入口 score_run（G1/1.2）"
        scored: list[tuple] = []

        def spy(variant, out, *, ticker, repeat, judge_repeats=3, materials_dir=None):
            scored.append((variant, ticker, repeat))
            return original(
                variant,
                out,
                ticker=ticker,
                repeat=repeat,
                judge_repeats=judge_repeats,
                materials_dir=materials_dir,
            )

        monkeypatch.setattr(real_ablation, "score_run", spy)
        env.ablation = real_ablation
        path = _run(env, tickers=["600519"], repeats=1)

        assert scored == [
            (v, "600519", 0) for v in ["analysts", "plus_debate", "through_trader", "full"]
        ]
        for variant in ["analysts", "plus_debate", "full"]:
            material = env.materials / f"{variant}-600519-0.json"
            assert json.loads(material.read_text(encoding="utf-8")) == {
                "debate_history": f"【bull】论点: {variant}"
            }, "库侧按 run 三元组落盘 judge_vars"
        artifact = json.loads(path.read_text(encoding="utf-8"))
        rows = {r["variant"]: r for r in artifact["run_costs"]}
        assert rows["analysts"]["judge"]["debate_quality"] is None, "无辩论层不评该维度"
        assert rows["plus_debate"]["judge"]["debate_quality"] == 4.0
        assert rows["plus_debate"]["judge"]["decision_grounding"] is None
        assert rows["full"]["judge"]["decision_grounding"] == 4.0
        assert (
            rows["full"]["judge_detail"]["debate_quality"]["judge_repeats"] == pilot.JUDGE_REPEATS
        )
        assert rows["plus_debate"]["argument_anchor_coverage"] == 0.5
        assert rows["analysts"]["argument_anchor_coverage"] is None
        assert rows["full"]["citation_buckets"] == {
            "blocked": 0,
            "analyst_true_fail": 1,
            "surgical_repaired": 0,
            "verifier_normalized": 2,
            "total": 3,
        }, "真库协议产出四桶 → 驱动 run 记录须携带（G2/F3）"


class TestSnapshotDigestVerification:
    """跨进程续跑的快照一致性：digest 不一致必须显式失败，不得静默混批。"""

    def test_mismatched_digest_fails_loudly(self, env):
        env.resume.parent.mkdir(parents=True, exist_ok=True)
        env.resume.write_text(
            json.dumps(
                {
                    "done_keys": [["full", "600519", 0]],
                    "runs": [],
                    "snapshot_digests": {"600519": "digest-STALE"},
                }
            ),
            encoding="utf-8",
        )
        calls_before = len(env.ablation.run_calls)
        with pytest.raises(RuntimeError) as excinfo:
            _run(env, tickers=["600519"], repeats=1)
        message = str(excinfo.value)
        assert "600519" in message and "digest-STALE" in message and "digest-600519" in message
        assert len(env.ablation.run_calls) == calls_before, "失败前不得消耗 token 跑 run"

    def test_consistent_digest_allows_resume(self, env):
        _run(env, tickers=["600519"], repeats=1)
        _run(env, tickers=["600519"], repeats=2)  # 重建 digest 一致 → 正常续跑
        resume = json.loads(env.resume.read_text(encoding="utf-8"))
        assert resume["snapshot_digests"] == {"600519": "digest-600519"}

    def test_digest_registered_per_ticker_on_first_run(self, env):
        _run(env, tickers=["600519", "000001"], repeats=1)
        resume = json.loads(env.resume.read_text(encoding="utf-8"))
        assert resume["snapshot_digests"] == {
            "600519": "digest-600519",
            "000001": "digest-000001",
        }

    # ── G5 通路验证缺陷回归：真实 digest × 驱动（object 列帧） ──
    # 旧实现按 ndarray.tobytes() 哈希 DataFrame → 对象列为 PyObject 指针，
    # 同内容两次构建 digest 不同，核验在真实数据上恒失败、整条跑批被堵死。

    @staticmethod
    def _patch_real_digest_with_object_frames(env, monkeypatch, *, day: str = "20241231"):
        import evals.ablation as real_ablation
        import pandas as pd

        def build(ticker: str) -> dict:
            return {
                "stock_code": ticker,
                "balance_sheet": pd.DataFrame(
                    {
                        "报告日": [day, "20231231"],
                        "资产总计": [1000.0, 900.0],
                        "负债合计": [400.0, 360.0],
                    }
                ),
            }

        monkeypatch.setattr(env.ablation, "build_snapshot", build)
        monkeypatch.setattr(env.ablation, "snapshot_digest", real_ablation.snapshot_digest)
        return real_ablation.snapshot_digest(build("600519"))

    def test_real_digest_allows_resume_across_rebuilds(self, env, monkeypatch):
        """同内容重建（含报告日 object 列）→ 续跑核验通过，digest 落盘即重建值。"""
        expected = self._patch_real_digest_with_object_frames(env, monkeypatch)
        _run(env, tickers=["600519"], repeats=1)
        calls_before = len(env.ablation.run_calls)
        _run(env, tickers=["600519"], repeats=2)  # 重建 digest 一致 → 正常续跑
        resume = json.loads(env.resume.read_text(encoding="utf-8"))
        assert resume["snapshot_digests"] == {"600519": expected}
        assert len(env.ablation.run_calls) > calls_before, "一致则须继续跑完待办 run"

    def test_real_digest_detects_refreshed_data_source(self, env, monkeypatch):
        """登记后数据源刷新（报告期变更）→ 内容哈希必须变，核验显式失败。"""
        self._patch_real_digest_with_object_frames(env, monkeypatch)
        _run(env, tickers=["600519"], repeats=1)
        calls_before = len(env.ablation.run_calls)
        self._patch_real_digest_with_object_frames(env, monkeypatch, day="20240930")
        with pytest.raises(RuntimeError) as excinfo:
            _run(env, tickers=["600519"], repeats=2)
        message = str(excinfo.value)
        assert "600519" in message and "快照 digest 与本次跑批登记值不一致" in message
        assert len(env.ablation.run_calls) == calls_before, "失败前不得消耗 token 跑 run"
