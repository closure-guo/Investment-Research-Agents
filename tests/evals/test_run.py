"""run_experiment:evaluator 装配、quick 模式 judge 跳过、langfuse 必达、结果表。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import evals.run
import pytest
from evals.dataset_seed import DATASET_NAME
from evals.run import (
    _citation_ci,
    _mean_rows,
    all_evaluators,
    eval_citation_coverage,
    eval_citation_pass,
)


class TestEvaluatorAssembly:
    def test_sixteen_evaluators(self):
        """4 确定性 + 4 judge + 阶段 5 拆报 6 项 + 单点修复计数 1 项
        （blocked/真错数/归一计数/文本与未注册 UNVERIFIABLE/comparative 差值 1 项/修复回填数）
        + 论点锚点覆盖率 1 项（add-debate-argument-anchors，零 LLM）。"""
        evals = all_evaluators()
        assert len(evals) == 16
        names = {e.__name__ for e in evals}
        assert {
            "eval_citation_blocked",
            "eval_citation_analyst_true_fail",
            "eval_citation_verifier_normalized",
            "eval_citation_unverifiable_text",
            "eval_citation_unverifiable_unregistered",
            "eval_citation_unverifiable_comparative_delta",
            "eval_citation_surgical_repaired",
            "eval_argument_anchor_coverage",
        } <= names

    def test_deterministic_evaluator_shape(self):
        evals = {e.__name__: e for e in all_evaluators()}
        result = evals["eval_section_coverage"](
            input={"query": "q", "mode": "deep"},
            output={
                "report": "偿债能力 盈利能力",
                "ticker": "600519",
                "judge_vars": {},
                "mode": "deep",
            },
            expected_output={"must_cover": ["偿债能力", "盈利能力"]},
            metadata={},
        )
        assert result is not None
        # langfuse Evaluation 或本地 dict 两种形态都可能,统一经 _as_dict
        assert (
            getattr(result, "name", None) == "section_coverage"
            or result["name"] == "section_coverage"
        )

    @patch("evals.run.run_judge_mean")
    def test_judge_skipped_for_quick_mode(self, mock_judge):
        mock_judge.return_value = {"name": "debate_quality", "score": 4, "reason": "x"}
        evals = {e.__name__: e for e in all_evaluators()}
        result = evals["eval_debate_quality"](
            input={"query": "q", "mode": "quick"},
            output={"report": "r", "ticker": None, "judge_vars": {}, "mode": "quick"},
            expected_output={},
            metadata={},
        )
        # quick 无辩论 → 返回空(list)或 None,不调 judge
        mock_judge.assert_not_called()
        assert result in (None, [])

    @patch("evals.run.run_judge_mean")
    def test_judge_uses_output_judge_vars(self, mock_judge):
        mock_judge.return_value = {
            "name": "report_relevance",
            "score": 5,
            "reason": "切题",
            "scores": [5],
            "score_spread": 0,
            "judge_repeats": 3,
            "judge_failures": 0,
        }
        evals = {e.__name__: e for e in all_evaluators()}
        evals["eval_report_relevance"](
            input={"query": "茅台", "mode": "deep"},
            output={
                "report": "r",
                "ticker": None,
                "judge_vars": {"query": "茅台", "report": "茅台好"},
                "mode": "deep",
            },
            expected_output={},
            metadata={},
        )
        mock_judge.assert_called_once_with(
            "report_relevance", {"query": "茅台", "report": "茅台好"}, repeats=3
        )


class TestMeanRows:
    def test_mean_rows(self):
        rows = [
            {"scores": {"a": 1.0, "b": 4}, "judge_failures": 0},
            {"scores": {"a": 0.0, "b": None}, "judge_failures": 1},
        ]
        means = _mean_rows(rows)
        assert means["a"] == 0.5
        assert means["b"] == 4.0  # None 不计入均值
        assert means["judge_failures"] == 1


class TestJudgeFailureCategorical:
    """#54 观测层修复的配套语义：行侧 scores 只收数值、judge_failures 数
    CATEGORICAL 标记；均值侧过滤非数值（str 进 float() 即崩）。"""

    def _row_from_evals(self, evaluations):
        from types import SimpleNamespace

        item = SimpleNamespace(input={"query": "q", "mode": "deep"})
        r = SimpleNamespace(item=item, output={}, evaluations=evaluations)
        return evals.run._rows_from_results([r])[0]

    def test_rows_scores_numeric_only_and_failures_count_marker(self):
        from evals.run import JUDGE_FAILURE_MARKER

        evals_ = [
            SimpleNamespace(name="consistency", value=4.0),
            SimpleNamespace(name="consistency", value=JUDGE_FAILURE_MARKER),
            SimpleNamespace(name="citation_pass", value=0.8),
        ]
        row = self._row_from_evals(evals_)
        assert row["scores"] == {"consistency": 4.0, "citation_pass": 0.8}
        assert row["judge_failures"] == 1

    def test_rows_legacy_none_value_still_counted(self):
        evals_ = [SimpleNamespace(name="consistency", value=None)]
        row = self._row_from_evals(evals_)
        assert row["scores"] == {}
        assert row["judge_failures"] == 1

    def test_mean_rows_skips_non_numeric(self):
        rows = [
            {"scores": {"a": 1.0, "b": 4, "c": "judge_parse_failed"}, "judge_failures": 1},
            {"scores": {"a": 3.0, "b": None}, "judge_failures": 0},
        ]
        means = _mean_rows(rows)
        assert means["a"] == 2.0
        assert means["b"] == 4.0  # None 不计入均值
        assert "c" not in means  # CATEGORICAL 标记不进均值
        assert means["judge_failures"] == 1


class TestLangfuseRequired:
    def test_no_langfuse_exits_nonzero_without_scores(self, monkeypatch):
        """无 langfuse → main() 必须显式报错退出,绝不本地循环产出分数。"""
        monkeypatch.setattr(sys, "argv", ["evals/run.py", "test-exp"])
        with (
            patch("evals.run.get_langfuse", return_value=None),
            patch("evals.run.run_task") as mock_task,
            pytest.raises(SystemExit) as exc_info,
        ):
            evals.run.main()
        assert exc_info.value.code  # 非零退出码(字符串/1 皆为真)
        mock_task.assert_not_called()  # 不走 run_task,不产出分数

    def test_with_langfuse_runs_run_experiment(self, monkeypatch):
        """有 langfuse → 走 run_experiment 路径,不抛、不降级。"""
        monkeypatch.setattr(sys, "argv", ["evals/run.py", "test-exp"])
        fake = MagicMock()
        fake_result = MagicMock()
        fake_result.item_results = []
        fake.get_dataset.return_value.run_experiment.return_value = fake_result
        with (
            patch("evals.run.get_langfuse", return_value=fake),
            patch("evals.run._write_report"),
            patch("evals.run._verify_prompt_sync", return_value=[]),  # 门禁放行
        ):
            evals.run.main()
        fake.get_dataset.assert_called_once_with(DATASET_NAME)
        fake.get_dataset.return_value.run_experiment.assert_called_once()
        fake.flush.assert_called_once()


class TestVerifyPromptSync:
    """eval 前置门禁：Langfuse production vs 本地 .md 一致性校验。"""

    def _mock_client(self, texts: dict):
        client = MagicMock()

        def fake_get(name):
            p = MagicMock()
            p.prompt = texts.get(name, "")
            return p

        client.get_prompt.side_effect = fake_get
        return client

    def test_all_consistent_returns_empty(self):
        from evals import run

        prompts_dir = Path(__file__).resolve().parents[2] / "src/finance_agent/prompts"
        local = {
            n: (prompts_dir / f"{n}.md").read_text(encoding="utf-8") for n in run._PROMPT_NAMES
        }
        client = self._mock_client(local)
        assert run._verify_prompt_sync(client) == []

    def test_mismatch_lists_differing_prompt(self):
        from evals import run

        texts = dict.fromkeys(run._PROMPT_NAMES, "x")
        client = self._mock_client(texts)
        result = run._verify_prompt_sync(client)
        assert len(result) == len(run._PROMPT_NAMES)
        assert run._PROMPT_NAMES[0] in result

    def test_get_prompt_failure_marks_mismatch(self):
        from evals import run

        client = self._mock_client({})
        client.get_prompt.side_effect = RuntimeError("boom")
        result = run._verify_prompt_sync(client)
        assert len(result) == len(run._PROMPT_NAMES)
        assert all("拉取失败" in r for r in result)

    def test_crlf_both_sides_literal_normalized_not_mismatched(self, monkeypatch, tmp_path):
        """本地/远端都为字面 CRLF 文本 → 两侧 .replace 归一化后应一致（不误报）。

        突变敏感：删除生产端任一 .replace 本用例即红。
        """
        from evals import run

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        # 复制全部 .md 到 tmp（本地侧固定为纯 CRLF）；远端 fake 侧以 newline=""
        # 读出同一内容的字面 CRLF（模拟 Langfuse 存储原始 CRLF）。两侧都保留
        # CRLF 原文、都依赖 .replace("\r\n","\n") 归一化 → 删除任一 replace 都会红。
        real = Path(__file__).resolve().parents[2] / "src/finance_agent/prompts"
        for name in run._PROMPT_NAMES:
            content = (real / f"{name}.md").read_bytes()
            crlf = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            (prompts_dir / f"{name}.md").write_bytes(crlf)

        def fake_get(name):
            p = MagicMock()
            # 远端 = 本地文件内容的 CRLF 字面版（newline="" 不触发 universal-newlines 归一化）
            p.prompt = (prompts_dir / f"{name}.md").read_text(encoding="utf-8", newline="")
            return p

        client = MagicMock()
        client.get_prompt.side_effect = fake_get
        monkeypatch.setattr(run, "_PROMPTS_DIR", prompts_dir)
        # 本地/远端读入都是字面 CRLF：靠 .replace 归一化后一致 → 门禁不得误报
        assert run._verify_prompt_sync(client) == []


class TestCitationMetricEvaluators:
    def test_pass_evaluator_reads_output(self):
        ev = eval_citation_pass(
            input={}, output={"citation_pass": 1.0}, expected_output={}, metadata={}
        )
        assert ev.name == "citation_pass"
        assert ev.value == 1.0

    def test_coverage_evaluator_reads_output(self):
        ev = eval_citation_coverage(
            input={}, output={"citation_coverage": 0.85}, expected_output={}, metadata={}
        )
        assert ev.name == "citation_coverage"
        assert ev.value == 0.85

    def test_missing_output_returns_none(self):
        """quick/无 citation 数据 → None 不计入（与 expected 缺省跳过同口径）。"""
        assert (
            eval_citation_pass(input={}, output={"mode": "quick"}, expected_output={}, metadata={})
            is None
        )
        assert (
            eval_citation_coverage(input={}, output=None, expected_output={}, metadata={}) is None
        )

    def test_citation_ci_deterministic(self):
        lo, hi = _citation_ci([0.8, 0.9, 1.0, 0.7])
        assert lo <= 0.875 <= hi
        assert _citation_ci([0.8, 0.9, 1.0, 0.7]) == (lo, hi)  # seed 固定可复现
        assert _citation_ci([]) == (0.0, 0.0)

    def test_anchor_coverage_evaluator_reads_output(self):
        """论点锚点覆盖率经 output 键透传为 Score；拆项随 comment 落库
        （spec Scenario：comment 含 unanchored_inference=2 / unresolved=1 /
        missing_required=0 / unspecified=1）；无数据（quick/旧 trace）→ None。"""
        ev = {e.__name__: e for e in all_evaluators()}["eval_argument_anchor_coverage"]
        result = ev(
            input={"query": "q", "mode": "deep"},
            output={
                "argument_anchor_coverage": 0.6667,
                "argument_anchor_coverage_detail": {
                    "total": 12,
                    "anchored": 8,
                    "unanchored_inference": 2,
                    "unresolved": 1,
                    "missing_required": 0,
                    "unspecified": 1,
                },
            },
            expected_output={},
            metadata={},
        )
        assert (getattr(result, "name", None) or result["name"]) == "argument_anchor_coverage"
        assert float(getattr(result, "value", None) or result["value"]) == 0.6667
        comment = getattr(result, "comment", None) or result["comment"]
        assert (
            "unanchored_inference=2 / unresolved=1 / missing_required=0 / unspecified=1" in comment
        )
        assert ev(input={}, output={"mode": "quick"}, expected_output={}, metadata={}) is None


class TestDebateCapEvidenceInComment:
    """debate_quality v6：封顶/枚举缺失证据随分数 comment 落库（可审计）。"""

    def _call(self, judge_result: dict):
        from evals.run import all_evaluators

        evals = {e.__name__: e for e in all_evaluators()}
        return evals["eval_debate_quality"](
            input={"query": "q", "mode": "deep"},
            output={
                "report": "r",
                "ticker": "600519",
                "judge_vars": {"debate_history": "【bull】论点: x"},
                "mode": "deep",
            },
            expected_output={},
            metadata={},
        )

    @patch("evals.run.run_judge_mean")
    def test_cap_applied_marked_in_comment(self, mock_judge):
        mock_judge.return_value = {
            "name": "debate_quality",
            "score": 4,
            "reason": "交锋充分",
            "confidence": 0.9,
            "points": [],
            "qualitative_points": 2,
            "cap_applied": True,
            "enumeration_missing": False,
            "scores": [4, 4, 4],
            "score_spread": 0,
            "judge_repeats": 3,
            "judge_failures": 0,
        }
        result = self._call(mock_judge.return_value)
        comment = getattr(result, "comment", None) or result["comment"]
        assert "[cap=qualitative×2]" in comment
        assert "[conf=0.90]" in comment

    @patch("evals.run.run_judge_mean")
    def test_enumeration_missing_marked_in_comment(self, mock_judge):
        mock_judge.return_value = {
            "name": "debate_quality",
            "score": 5,
            "reason": "交锋充分",
            "confidence": None,
            "points": [],
            "qualitative_points": 0,
            "cap_applied": False,
            "enumeration_missing": True,
            "scores": [5, 5, 5],
            "score_spread": 0,
            "judge_repeats": 3,
            "judge_failures": 0,
        }
        result = self._call(mock_judge.return_value)
        comment = getattr(result, "comment", None) or result["comment"]
        assert "[enum-missing]" in comment


class TestJudgeKMean:
    """hosted 判分取 K 次均值（delta switch-hosted-judge-to-k-mean）。

    round11 实测单次调用在 4/5 边界双峰翻转（同材料 n=13：4 分 7 次/5 分 6 次），
    与 hosted 回归要检测的效应同阶——点估计改为 K 次均值，scores/spread 随
    comment 落库（Langfuse Scores 为逐 trace 明细真源）。
    """

    def _call(self, dim="debate_quality", mode="deep"):
        fns = {e.__name__: e for e in all_evaluators()}
        return fns[f"eval_{dim}"](
            input={"query": "q", "mode": mode},
            output={
                "report": "r",
                "ticker": "600519",
                "judge_vars": {dim: "材料"},
                "mode": mode,
            },
            expected_output={},
            metadata={},
        )

    @staticmethod
    def _comment(result):
        return getattr(result, "comment", None) if hasattr(result, "comment") else result["comment"]

    @staticmethod
    def _value(result):
        return getattr(result, "value", None) if hasattr(result, "value") else result["value"]

    def test_default_repeats_is_three(self):
        assert evals.run._JUDGE_REPEATS == 3

    def test_adapter_calls_run_judge_mean_with_configured_repeats(self, monkeypatch):
        seen = {}

        def fake_mean(dim, variables, *, repeats):
            seen.update(dim=dim, variables=variables, repeats=repeats)
            return {
                "name": dim,
                "score": 4.333,
                "reason": "交锋充分",
                "confidence": 0.8,
                "scores": [4, 5, 4],
                "score_spread": 1,
                "judge_repeats": repeats,
                "judge_failures": 0,
            }

        monkeypatch.setattr(evals.run, "run_judge_mean", fake_mean)
        monkeypatch.setattr(evals.run, "_JUDGE_REPEATS", 5)
        result = self._call()
        assert seen == {
            "dim": "debate_quality",
            "variables": {"debate_quality": "材料"},
            "repeats": 5,
        }
        assert self._value(result) == 4.333

    def test_comment_carries_scores_and_spread(self, monkeypatch):
        def fake_mean(dim, variables, *, repeats):
            return {
                "name": dim,
                "score": 4.333,
                "reason": "交锋充分",
                "confidence": None,
                "scores": [4, 5, 4],
                "score_spread": 1,
                "judge_repeats": 3,
                "judge_failures": 0,
            }

        monkeypatch.setattr(evals.run, "run_judge_mean", fake_mean)
        comment = self._comment(self._call())
        assert "[K=3" in comment
        assert "scores=[4, 5, 4]" in comment
        assert "spread=1" in comment
        assert "fail=" not in comment  # 无失败不标 fail

    def test_partial_failure_marked_mean_of_valid(self, monkeypatch):
        def fake_mean(dim, variables, *, repeats):
            return {
                "name": dim,
                "score": 4.5,
                "reason": "一致",
                "confidence": None,
                "scores": [4, 5, None],
                "score_spread": 1,
                "judge_repeats": 3,
                "judge_failures": 1,
            }

        monkeypatch.setattr(evals.run, "run_judge_mean", fake_mean)
        result = self._call(dim="consistency")
        assert self._value(result) == 4.5
        assert "fail=1" in self._comment(result)

    def test_all_failed_emits_categorical_marker(self, monkeypatch):
        """#54:全败不再发 value=None(langfuse 4.13 ScoreBody.value required,
        None 经 pydantic ValidationError 被 SDK 吞掉,失败维度从未进 dataset
        run),改发 CATEGORICAL value=JUDGE_FAILURE_MARKER。"""

        def fake_mean(dim, variables, *, repeats):
            return {
                "name": dim,
                "score": None,
                "reason": "parse_failed",
                "scores": [None, None, None],
                "score_spread": None,
                "judge_repeats": 3,
                "judge_failures": 3,
            }

        monkeypatch.setattr(evals.run, "run_judge_mean", fake_mean)
        result = self._call(dim="consistency")
        assert self._value(result) == evals.run.JUDGE_FAILURE_MARKER
        assert self._value(result) == "judge_parse_failed"
        comment = self._comment(result)
        assert "[K=3 fail=3]" in comment
        assert "parse_failed" in comment

    def test_non_debate_dims_do_not_gain_debate_keys_in_comment(self, monkeypatch):
        """非 debate 维度结果形状不变：comment 只含 K 均值标注与 reason。"""

        def fake_mean(dim, variables, *, repeats):
            return {
                "name": dim,
                "score": 5.0,
                "reason": "切题",
                "confidence": 0.9,
                "scores": [5, 5, 5],
                "score_spread": 0,
                "judge_repeats": 3,
                "judge_failures": 0,
            }

        monkeypatch.setattr(evals.run, "run_judge_mean", fake_mean)
        comment = self._comment(self._call(dim="report_relevance"))
        assert "cap=" not in comment and "enum-missing" not in comment
        assert "[conf=0.90]" in comment

    def test_report_json_contains_judge_repeats(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = evals.run._write_report(
            rows=[],
            means={"judge_failures": 0},
            name="t",
            prompt_versions={},
            citation_ci={},
            judge_repeats=3,
        )
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["judge_repeats"] == 3

    def test_cli_judge_repeats_threaded_to_report(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["evals/run.py", "test-exp", "--judge-repeats", "5"])
        fake = MagicMock()
        fake_result = MagicMock()
        fake_result.item_results = []
        fake.get_dataset.return_value.run_experiment.return_value = fake_result
        with (
            patch("evals.run.get_langfuse", return_value=fake),
            patch("evals.run._verify_prompt_sync", return_value=[]),
            patch("evals.run._write_report") as mock_write,
        ):
            evals.run.main()
        assert evals.run._JUDGE_REPEATS == 5
        assert mock_write.call_args.kwargs["judge_repeats"] == 5


class TestQuickNoJudge:
    """quick 条目停评全部 judge 维（delta resolve-report-relevance-zero-variance）。

    report_relevance 在 quick 段自 r3 起零方差（全 5，Spearman 不可算），判据失败面
    已被确定性指标覆盖；quick 不再发起任何 judge 调用，确定性指标照常。
    """

    def test_report_relevance_skipped_for_quick(self, monkeypatch):
        from unittest.mock import MagicMock

        mock_judge = MagicMock()
        # 判分入口双防御：K 均值切换（PR #149）合入前后都能挡住调用
        monkeypatch.setattr(evals.run, "run_judge_mean", mock_judge, raising=False)
        monkeypatch.setattr(evals.run, "run_judge", mock_judge, raising=False)
        fns = {e.__name__: e for e in all_evaluators()}
        result = fns["eval_report_relevance"](
            input={"query": "q", "mode": "quick"},
            output={"report": "r", "ticker": None, "judge_vars": {"query": "q"}, "mode": "quick"},
            expected_output={},
            metadata={},
        )
        mock_judge.assert_not_called()
        assert result in (None, [])
