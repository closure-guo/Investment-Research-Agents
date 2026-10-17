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
    GatewayTranscriptionClient,
    build_transcription_prompt,
    parse_transcription_payload,
    run_factor_harvest,
    transcribe_candidate,
)


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
    """因子库路径指向 tmp（library 调用期读 env，不落开发库）。"""
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(tmp_path / "factor_library.db"))


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
            _candidate("badop", description="滚动最大 qlib 形态", formulation="max"),
        ]
        cand_path = self._write_candidates(tmp_path, candidates)
        state_path = tmp_path / "harvest_state.json"
        client = _StubClient(
            {
                "tsmean": {"qlib_expression": "Mean($close, 5)"},
                "rollmax": {"untranslatable": True, "reason": "滚动最大值本系统无对应算子"},
                "badop": {"qlib_expression": "Max($close, 20)"},
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


def test_gateway_client_is_default_llm_entry():
    """默认客户端经 gateway complete_text（唯一生产 LLM 入口）——仅断言可构造。"""
    assert GatewayTranscriptionClient() is not None
