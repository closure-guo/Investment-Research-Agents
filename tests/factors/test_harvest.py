"""factors/harvest.py 单测（add-factor-harvest-bridge）。

契约（spec factor-proposal「外部研发供给接入」收割通道 + 「外部表达式翻译契约」）：

- 候选文件 → LLM 转译（二值裁决：合法 Qlib 表达式或显式不可翻译，MUST NOT 猜测）
  → 确定性翻译（qlib_translate）→ 同一五道闸（source=external）；
- 不可翻译/转译失败/预检失败逐条留痕（gate=None → errors 桶），单条失败不阻断整批；
- 已处理候选记状态文件，重复运行不重复导入（幂等）；
- LLM 网关不可用 → fail loud（上抛，不静默跳过、不伪造候选）；
- 拒收记录携带闸口读数 detail（missing_rate/icir 等）。

面板为合成数据（零网络）；LLM 全 stub；库路径 tmp 注入。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.harvest import (
    TRANSCRIPTION_MAX_ATTEMPTS,
    GatewayTranscriptionClient,
    TranscriptionResponseError,
    build_transcription_prompt,
    parse_transcription_payload,
    run_factor_harvest,
    run_factor_harvest_until_drained,
    transcribe_candidate,
)
from finance_agent.llm.errors import OutputTruncatedError


def _candidate(name: str, description: str = "d", formulation: str = "f") -> dict:
    return {"name": name, "description": description, "formulation": formulation}


class _StubClient:
    """按 name 返回预设载荷的转译客户端（记录调用）。"""

    def __init__(self, payloads: dict[str, dict]) -> None:
        self._payloads = payloads
        self.calls: list[str] = []

    def complete_json(self, prompt, *, purpose, metadata):
        self.calls.append(metadata["factor_name"])
        assert purpose == "factor_transcription"
        return self._payloads[metadata["factor_name"]]


class _FailingClient:
    def complete_json(self, prompt, *, purpose, metadata):
        raise RuntimeError("gateway down")


class _FlakyClient:
    """前 ``failures`` 次抛合同失败（TranscriptionResponseError），其后返回预设载荷。"""

    def __init__(self, failures: int, payload: dict) -> None:
        self._remaining = failures
        self._payload = payload
        self.calls = 0

    def complete_json(self, prompt, *, purpose, metadata):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise TranscriptionResponseError("转译响应不是合法 JSON：bad", raw_text="bad")
        return self._payload


class _CountingFailingClient:
    """网关级失败（RuntimeError）+ 调用计数（证「网关异常不重试」）。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt, *, purpose, metadata):
        self.calls += 1
        raise RuntimeError("gateway down")


def _panel(dates: int = 70, codes: int = 40, seed: int = 7) -> MarketPanel:
    """合成面板：确定性随机游走（闸门全链可在其上运行；读数不预设）。"""
    rng = np.random.default_rng(seed)
    index = [f"2026-01-{d:02d}" for d in range(1, dates + 1)]
    columns = [f"{600000 + i:06d}" for i in range(codes)]
    close = pd.DataFrame(
        100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, size=(dates, codes)), axis=0)),
        index=index,
        columns=columns,
    )
    volume = pd.DataFrame(rng.uniform(1e5, 1e6, size=(dates, codes)), index=index, columns=columns)
    return MarketPanel(fields={"close": close, "volume": volume})


@pytest.fixture(autouse=True)
def _library_db(tmp_path, monkeypatch):
    """因子库/记忆桥路径指向 tmp（调用期读 env，不落开发库与生产桥文件）。"""
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(tmp_path / "factor_library.db"))
    monkeypatch.setenv(
        "FACTOR_FEEDBACK_PATH", str(tmp_path / "explored_factors.json")
    )
    monkeypatch.setenv(
        "FACTOR_RESEARCH_HEARTBEAT_PATH", str(tmp_path / "harvest_heartbeat.json")
    )


class TestTranscriptionPayload:
    def test_qlib_expression_payload(self):
        result = parse_transcription_payload({"qlib_expression": " Mean($close, 5) "})
        assert result.qlib_expression == "Mean($close, 5)"
        assert result.reason is None

    def test_untranslatable_payload(self):
        result = parse_transcription_payload({"untranslatable": True, "reason": "滚动最大值"})
        assert result.qlib_expression is None
        assert result.reason == "滚动最大值"

    def test_untranslatable_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            parse_transcription_payload({"untranslatable": True})

    def test_missing_both_rejected(self):
        with pytest.raises(ValueError):
            parse_transcription_payload({})


class TestPrompt:
    def test_prompt_carries_factor_block(self):
        prompt = build_transcription_prompt(
            _candidate("mom20", description="20 日动量", formulation="C_t/C_{t-20} - 1")
        )
        assert "mom20" in prompt and "20 日动量" in prompt and "C_t/C_{t-20} - 1" in prompt
        assert "{{factor_block}}" not in prompt


class TestTranscribeCandidate:
    def test_transcribes_via_client(self):
        client = _StubClient({"a": {"qlib_expression": "$close / Ref($close, 20) - 1"}})
        result = transcribe_candidate(_candidate("a"), client)
        assert result.qlib_expression == "$close / Ref($close, 20) - 1"
        assert client.calls == ["a"]

    def test_client_gateway_error_propagates(self):
        with pytest.raises(RuntimeError, match="gateway down"):
            transcribe_candidate(_candidate("a"), _FailingClient())


class TestRunFactorHarvest:
    def _write_candidates(self, tmp_path, candidates):
        path = tmp_path / "candidates.json"
        path.write_text(
            json.dumps({"generated_at": "2026-10-17T12:00:00+08:00", "candidates": candidates}),
            encoding="utf-8",
        )
        return path

    def test_end_to_end_mixed_batch(self, tmp_path):
        """三态混合：可翻译（过闸读数）+ LLM 拒译 + 翻译器拒译（errors 桶）。"""
        candidates = [
            _candidate("tsmean", description="5 日均价", formulation="mean(C,5)"),
            _candidate("rollmax", description="20 日滚动最大", formulation="max_i(C)"),
            _candidate("badop", description="滚动偏度 qlib 形态", formulation="skew"),
        ]
        cand_path = self._write_candidates(tmp_path, candidates)
        state_path = tmp_path / "harvest_state.json"
        client = _StubClient(
            {
                "tsmean": {"qlib_expression": "Mean($close, 5)"},
                "rollmax": {"untranslatable": True, "reason": "公式需要滚动回归，本系统无对应算子"},
                # Max/Min 自 extend-factor-dsl-sign-extremes 起可映射（ts_max/ts_min）；
                # 翻译器拒译样本改用仍缺的滚动偏度
                "badop": {"qlib_expression": "Skew($close, 20)"},
            }
        )
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
        )
        assert summary["proposed"] == 3
        assert summary["transcribed"] == 1
        assert summary["untranslatable"] == 2  # LLM 拒译 1 + 翻译器拒译 1
        # 合成面板随机数据：可翻译候选过不了闸②（ICIR 近零）→ rejected；两条拒译 → errors
        assert summary["admitted"] == 0
        assert summary["rejected"] == 1
        assert summary["errors"] == 2
        # 拒收记录携带读数 detail（add-factor-harvest-bridge）
        rejected = [r for r in summary["rejections"] if r["gate"] is not None]
        assert rejected and "icir" in rejected[0]["detail"]
        # 状态回填：三条全部 processed（不重复导入）
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert set(state["processed"]) == {"tsmean", "rollmax", "badop"}
        assert state["processed"]["rollmax"]["status"] == "untranslatable"
        assert state["processed"]["badop"]["stage"] == "translator"

    def test_refreshes_explored_feedback_bridge(self, tmp_path, monkeypatch):
        """收割成功 → 跨会话记忆桥自动刷新（生成侧据此避免重复提案）。"""
        target = tmp_path / "explored.json"
        monkeypatch.setenv("FACTOR_FEEDBACK_PATH", str(target))
        cand_path = self._write_candidates(
            tmp_path, [_candidate("tsmean", description="5 日均价", formulation="mean(C,5)")]
        )
        client = _StubClient({"tsmean": {"qlib_expression": "Mean($close, 5)"}})
        run_factor_harvest(
            candidates_path=cand_path,
            state_path=tmp_path / "harvest_state.json",
            client=client,
            panel=_panel(),
        )
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["explored"][0]["name"] == "tsmean"
        assert payload["explored"][0]["status"] in {"rejected", "admitted", "untranslatable"}

    def test_writes_in_batch_heartbeat(self, tmp_path):
        """批内逐候选心跳：整批结束后 phase=done 且 processed==total（观测用，不承载裁决）。"""
        cand_path = self._write_candidates(tmp_path, [_candidate("a"), _candidate("b")])
        client = _StubClient(
            {
                "a": {"qlib_expression": "Mean($close, 5)"},
                "b": {"qlib_expression": "Std($close, 5)"},
            }
        )
        run_factor_harvest(
            candidates_path=cand_path,
            state_path=tmp_path / "harvest_state.json",
            client=client,
            panel=_panel(),
        )
        heartbeat = json.loads(
            (tmp_path / "harvest_heartbeat.json").read_text(encoding="utf-8")
        )
        assert heartbeat["phase"] == "done"
        assert heartbeat["processed_in_batch"] == heartbeat["total_in_batch"] == 2

    def test_feedback_export_failure_is_soft(self, tmp_path, monkeypatch):
        """记忆桥是附加产物：导出失败不得让收割批失败。"""
        blocked = tmp_path / "as_directory"
        blocked.mkdir()
        monkeypatch.setenv("FACTOR_FEEDBACK_PATH", str(blocked))
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"
        client = _StubClient({"a": {"qlib_expression": "Mean($close, 5)"}})
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert summary["proposed"] == 1
        assert state_path.exists()

    def test_idempotent_second_run(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"
        client = _StubClient({"a": {"qlib_expression": "Mean($close, 5)"}})
        first = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert first["proposed"] == 1
        second = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert second["proposed"] == 0
        assert second["admitted"] == 0 and second["rejected"] == 0 and second["errors"] == 0
        assert client.calls == ["a"]  # 第二次零 LLM 调用

    def test_gateway_failure_fail_loud(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"
        with pytest.raises(RuntimeError, match="gateway down"):
            run_factor_harvest(
                candidates_path=cand_path,
                state_path=state_path,
                client=_FailingClient(),
                panel=_panel(),
            )
        # fail loud：不写状态（候选下次重试，不静默跳过）
        assert not state_path.exists()

    def test_limit_bounds_batch(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate(f"f{i}") for i in range(5)])
        state_path = tmp_path / "harvest_state.json"
        client = _StubClient({f"f{i}": {"qlib_expression": "Mean($close, 5)"} for i in range(5)})
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
            limit=2,
        )
        assert summary["proposed"] == 2
        assert len(client.calls) == 2

    def test_missing_candidates_file_zero_summary(self, tmp_path):
        summary = run_factor_harvest(
            candidates_path=tmp_path / "absent.json",
            state_path=tmp_path / "state.json",
            client=_StubClient({}),
            panel=_panel(),
        )
        assert summary["proposed"] == 0


class _HarvestFixtures:
    def _write_candidates(self, tmp_path, candidates):
        path = tmp_path / "candidates.json"
        path.write_text(
            json.dumps({"generated_at": "2026-10-19T00:00:00+08:00", "candidates": candidates}),
            encoding="utf-8",
        )
        return path

    def _write_state(self, tmp_path, processed):
        path = tmp_path / "harvest_state.json"
        path.write_text(
            json.dumps({"updated_at": "2026-10-18T00:00:00+08:00", "processed": processed}),
            encoding="utf-8",
        )
        return path

    def _read_state(self, state_path) -> dict:
        return json.loads(state_path.read_text(encoding="utf-8"))["processed"]


class TestTranscriptionRetry(_HarvestFixtures):
    """转译响应合同失败（非法 JSON / 缺字段）→ 重试；网关级异常不重试（fail loud）。"""

    def test_retry_recovers_from_bad_response(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"
        client = _FlakyClient(failures=1, payload={"qlib_expression": "Mean($close, 5)"})
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert client.calls == 2  # 首次失败 + 重试成功
        assert summary["transcribed"] == 1
        assert summary["transcription_errors"] == 0
        assert self._read_state(state_path)["a"]["attempts"] == 2

    def test_retry_exhausted_records_attempts_and_continues(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a"), _candidate("b")])
        state_path = tmp_path / "harvest_state.json"

        class _AlwaysBadThenGood:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, prompt, *, purpose, metadata):
                self.calls += 1
                if metadata["factor_name"] == "a":
                    raise TranscriptionResponseError("bad", raw_text="bad")
                return {"qlib_expression": "Mean($close, 5)"}

        client = _AlwaysBadThenGood()
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert summary["transcription_errors"] == 1  # a 留痕
        assert summary["transcribed"] == 1  # b 照常裁决（单条失败不阻断整批）
        processed = self._read_state(state_path)
        assert processed["a"]["attempts"] == TRANSCRIPTION_MAX_ATTEMPTS
        # b 在合成面板（seed=7）上裁决确定性：ts_mean(close,5) → 闸② WEAK_ICIR
        assert processed["b"]["status"] == "rejected"
        assert processed["b"]["gate"] == 2
        assert processed["b"]["reason"] == "WEAK_ICIR"

    def test_last_allowed_attempt_succeeds(self, tmp_path):
        """最后一次允许的尝试成功（failures=2 → attempts=3，不留 transcription_error）。"""
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"
        client = _FlakyClient(failures=2, payload={"qlib_expression": "Mean($close, 5)"})
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert client.calls == 3
        assert summary["transcribed"] == 1
        assert summary["transcription_errors"] == 0
        assert self._read_state(state_path)["a"]["attempts"] == 3

    def test_payload_contract_violation_also_retries(self, tmp_path):
        """缺 reason 的 untranslatable 载荷（ValueError）同走重试面。"""
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])

        class _BadPayloadThenGood:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, prompt, *, purpose, metadata):
                self.calls += 1
                if self.calls == 1:
                    return {"untranslatable": True}  # 缺 reason → ValueError
                return {"untranslatable": True, "reason": "缺运算符"}

        client = _BadPayloadThenGood()
        state_path = tmp_path / "harvest_state.json"
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert client.calls == 2
        assert summary["untranslatable"] == 1
        assert summary["transcription_errors"] == 0

    def test_gateway_error_not_retried(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"
        client = _CountingFailingClient()
        with pytest.raises(RuntimeError, match="gateway down"):
            run_factor_harvest(
                candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
            )
        assert client.calls == 1  # 网关级异常不重试（fail loud 语义不变）
        assert not state_path.exists()

    def test_output_truncated_is_per_candidate_error_not_batch_abort(self, tmp_path):
        """单候选输出截断（思考模型 reasoning 吃满预算）→ 留痕 transcription_error，不杀整批。

        与网关不可用（RateLimit/连接）区分：后者仍 fail loud；前者是单条输出不可用，
        按损耗回收先例记录状态，后续可经 ``retry_statuses={"transcription_error"}`` 重放。
        """
        cand_path = self._write_candidates(tmp_path, [_candidate("a"), _candidate("b")])
        state_path = tmp_path / "harvest_state.json"

        class _TruncatedThenGood:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, prompt, *, purpose, metadata):
                self.calls += 1
                if metadata["factor_name"] == "a":
                    raise OutputTruncatedError("续写仍 finish_reason=length：输出被截断")
                return {"qlib_expression": "Mean($close, 5)"}

        client = _TruncatedThenGood()
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )

        assert summary["transcription_errors"] == 1
        assert summary["transcribed"] == 1  # b 照常裁决（不阻断整批）
        processed = self._read_state(state_path)
        assert processed["a"]["status"] == "transcription_error"
        assert "截断" in processed["a"]["detail"]
        assert processed["b"]["status"] == "rejected"

    def test_llm_config_error_not_retried_and_fails_loud(self, tmp_path):
        """LLM 配置类 ValueError（resolver 家族）不在重试面内——fail loud、不写状态。

        review 修复回归：`ValueError` 曾作为重试面泛捕获，会把部署配置错误
        （半套配置 / 未知 provider 前缀）降级为逐条 transcription_error 并落状态。
        """
        from finance_agent.llm.resolver import IncompleteLLMConfigError

        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = tmp_path / "harvest_state.json"

        class _ConfigErrorClient:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, prompt, *, purpose, metadata):
                self.calls += 1
                raise IncompleteLLMConfigError("半套配置：有 model 无端点")

        client = _ConfigErrorClient()
        with pytest.raises(IncompleteLLMConfigError):
            run_factor_harvest(
                candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
            )
        assert client.calls == 1  # 不重试
        assert not state_path.exists()  # 不记状态（配置修复后可重试）


class TestRetryStatusesReplay(_HarvestFixtures):
    """按状态重放：命中状态的已处理候选重新入批；未指定时幂等行为逐字不变。"""

    def test_replay_overwrites_and_records_retried_from(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = self._write_state(
            tmp_path,
            {"a": {"status": "untranslatable", "stage": "llm", "detail": "缺算子", "at": "t0"}},
        )
        client = _StubClient({"a": {"qlib_expression": "Mean($close, 5)"}})
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
            retry_statuses={"untranslatable"},
        )
        assert summary["proposed"] == 1
        assert summary["replayed"] == 1
        assert client.calls == ["a"]
        verdict = self._read_state(state_path)["a"]
        # 合成面板（seed=7）裁决确定性：ts_mean(close,5) → 闸② WEAK_ICIR
        assert verdict["status"] == "rejected"
        assert verdict["gate"] == 2
        assert verdict["retried_from"] == "untranslatable"

    def test_replay_mixed_pool_with_sufficient_limit(self, tmp_path):
        """命中重放 + 未命中 + 未处理三者共存、limit 充足：未命中者不得入批。"""
        cand_path = self._write_candidates(
            tmp_path, [_candidate("a"), _candidate("b"), _candidate("c")]
        )
        state_path = self._write_state(
            tmp_path,
            {
                "a": {"status": "untranslatable", "stage": "llm", "detail": "x", "at": "t0"},
                "b": {"status": "rejected", "gate": 2, "reason": "WEAK_ICIR", "at": "t0"},
            },
        )
        client = _StubClient(
            {
                "a": {"qlib_expression": "Mean($close, 5)"},
                "c": {"qlib_expression": "Mean($close, 5)"},
            }
        )
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
            retry_statuses={"untranslatable"},
        )
        assert summary["proposed"] == 2
        assert summary["replayed"] == 1
        assert client.calls == ["a", "c"]  # b（未命中状态）不入批
        processed = self._read_state(state_path)
        assert processed["b"]["status"] == "rejected"
        assert "retried_from" not in processed["b"]
        assert "retried_from" not in processed["c"]  # 未处理候选非重放

    def test_replay_preserves_previous_qlib_expression(self, tmp_path):
        """重放覆盖不得丢失历次转译产物（复译链路依赖 qlib_expression）。"""
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = self._write_state(
            tmp_path,
            {
                "a": {
                    "status": "untranslatable",
                    "stage": "translator",
                    "detail": "复杂度超限",
                    "qlib_expression": "-1 * $close",
                    "at": "t0",
                }
            },
        )
        # 重放这次转译失败（无 qlib_expression 的新裁决）→ 旧表达式应被保留
        client = _FlakyClient(failures=99, payload={})
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
            retry_statuses={"untranslatable"},
        )
        assert summary["transcription_errors"] == 1
        verdict = self._read_state(state_path)["a"]
        assert verdict["status"] == "transcription_error"
        assert verdict["qlib_expression"] == "-1 * $close"
        assert verdict["retried_from"] == "untranslatable"  # 转译失败分支同样带重放审计

    def test_replay_priority_over_unprocessed(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a"), _candidate("b")])
        state_path = self._write_state(
            tmp_path,
            {
                "a": {
                    "status": "untranslatable",
                    "stage": "translator",
                    "detail": "负数常量",
                    "at": "t0",
                }
            },
        )
        client = _StubClient(
            {
                "a": {"qlib_expression": "-1 * $close"},
                "b": {"qlib_expression": "Mean($close, 5)"},
            }
        )
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
            limit=1,
            retry_statuses={"untranslatable"},
        )
        assert summary["proposed"] == 1
        assert client.calls == ["a"]  # 重放优先于未处理候选（限批下不被挤压）

    def test_replay_leaves_other_statuses_untouched(self, tmp_path):
        cand_path = self._write_candidates(tmp_path, [_candidate("a"), _candidate("b")])
        state_path = self._write_state(
            tmp_path,
            {
                "a": {"status": "untranslatable", "stage": "llm", "detail": "x", "at": "t0"},
                "b": {"status": "rejected", "gate": 2, "reason": "WEAK_ICIR", "at": "t0"},
            },
        )
        client = _StubClient({"a": {"untranslatable": True, "reason": "仍缺算子"}})
        summary = run_factor_harvest(
            candidates_path=cand_path,
            state_path=state_path,
            client=client,
            panel=_panel(),
            retry_statuses={"transcription_error"},
        )
        assert summary["proposed"] == 0  # 未命中任何重放状态 → 零新候选
        assert client.calls == []
        processed = self._read_state(state_path)
        assert processed["b"]["status"] == "rejected"  # 其他状态不被触碰

    def test_none_retry_statuses_keeps_idempotency(self, tmp_path):
        """retry_statuses 缺省（日批形态）：已处理候选不重新入批，零 LLM 调用。"""
        cand_path = self._write_candidates(tmp_path, [_candidate("a")])
        state_path = self._write_state(
            tmp_path, {"a": {"status": "untranslatable", "stage": "llm", "detail": "x", "at": "t0"}}
        )
        client = _StubClient({})
        summary = run_factor_harvest(
            candidates_path=cand_path, state_path=state_path, client=client, panel=_panel()
        )
        assert summary["proposed"] == 0
        assert client.calls == []
        assert self._read_state(state_path)["a"]["status"] == "untranslatable"


def test_gateway_client_is_default_llm_entry():
    """默认客户端经 gateway complete_text（唯一生产 LLM 入口）——仅断言可构造。"""
    assert GatewayTranscriptionClient() is not None


class TestUntilDrained:
    """日批入口：内部串行多批，直到无待处理（每批 ≤limit，独立 FDR 家族）。"""

    @staticmethod
    def _write(tmp_path, names):
        path = tmp_path / "candidates.json"
        path.write_text(
            json.dumps(
                {"generated_at": "2026-10-22T00:00:00+08:00", "candidates": [_candidate(n) for n in names]}
            ),
            encoding="utf-8",
        )
        return path

    def test_loops_until_no_pending(self, tmp_path):
        names = [f"f{i}" for i in range(5)]
        cand_path = self._write(tmp_path, names)
        client = _StubClient({n: {"qlib_expression": "Mean($close, 5)"} for n in names})
        summary = run_factor_harvest_until_drained(
            limit=2,
            candidates_path=cand_path,
            state_path=tmp_path / "harvest_state.json",
            client=client,
            panel=_panel(),
        )
        assert summary["batches"] == 3  # 2 + 2 + 1
        assert summary["proposed"] == 5
        assert summary["drained"] is True
        assert client.calls == names

    def test_stops_at_max_batches(self, tmp_path):
        names = [f"f{i}" for i in range(5)]
        cand_path = self._write(tmp_path, names)
        client = _StubClient({n: {"qlib_expression": "Mean($close, 5)"} for n in names})
        summary = run_factor_harvest_until_drained(
            limit=2,
            max_batches=1,
            candidates_path=cand_path,
            state_path=tmp_path / "harvest_state.json",
            client=client,
            panel=_panel(),
        )
        assert summary["batches"] == 1
        assert summary["proposed"] == 2
        assert summary["drained"] is False

    def test_zero_pending_marks_drained(self, tmp_path):
        cand_path = self._write(tmp_path, [])
        summary = run_factor_harvest_until_drained(
            limit=60,
            candidates_path=cand_path,
            state_path=tmp_path / "harvest_state.json",
            client=_StubClient({}),
            panel=_panel(),
        )
        assert summary["batches"] == 0
        assert summary["drained"] is True
