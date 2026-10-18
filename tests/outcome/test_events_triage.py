"""LLM 分诊测试（delta add-event-driven-lane T4）：合同校验 / 拒收降级 / 指纹落库。

口径权威：docs/evals/metrics.md §1.15 + 变更 design.md「分诊合同与降级」。
全部 stub client，零网络；prompt 模板经 load_prompt 本地兜底读取。
"""

from __future__ import annotations

import json

import pytest

from finance_agent.llm.errors import RateLimitError
from finance_agent.outcome.events.model import list_untriaged, upsert_forecasts
from finance_agent.outcome.events.triage import (
    DEFAULT_MAX_WORKERS,
    DEGRADED_REASON,
    MAX_INPUT_FIELDS,
    MAX_REASON_CHARS,
    VERDICTS,
    EventTriageResponseError,
    GatewayTriageClient,
    build_triage_prompt,
    triage_forecasts,
    validate_triage_payload,
)


def _forecast_row(
    *,
    code: str = "600519",
    announce_date: str = "2025-04-15",
    change_low: float | None = 11.57,
    change_high: float | None = 18.55,
    forecast_type: str = "略增",
) -> dict:
    return {
        "code": code,
        "name": "贵州茅台",
        "report_period": "2025-03-31",
        "source_record_id": f"归属于上市公司股东的净利润|{announce_date}",
        "forecast_type": forecast_type,
        "change_low": change_low,
        "change_high": change_high,
        "announce_date": announce_date,
        "available_at": announce_date,
        "source": "eastmoney",
    }


class _StubClient:
    """测试 stub：按调用顺序返回 payload（异常对象直接 raise）。"""

    model_label = "stub-model"

    def __init__(self, payloads: list) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def complete_json(self, prompt: str, *, purpose: str = "quick", metadata=None) -> dict:
        self.calls.append({"prompt": prompt, "purpose": purpose, "metadata": metadata})
        result = self._payloads.pop(0) if self._payloads else {"verdict": "in_line", "reason": "x"}
        if isinstance(result, Exception):
            raise result
        return result


_OK_PAYLOAD = {
    "verdict": "outperform",
    "reason": "归母净利润预增 11.57%~18.55%，景气延续",
    "evidence": [
        {"field": "forecast_type", "value": "略增"},
        {"field": "change_low", "value": 11.57},
    ],
}


class TestContract:
    def test_verdict_enum_and_reason_limit(self):
        assert VERDICTS == ("outperform", "in_line", "risk")
        assert MAX_REASON_CHARS == 50

    def test_accepts_valid_payload(self):
        contract, error = validate_triage_payload(_OK_PAYLOAD)
        assert error is None
        assert contract is not None
        verdict, reason, evidence = contract
        assert verdict == "outperform" and reason.startswith("归母净利润预增")
        assert len(evidence) == 2

    def test_rejects_illegal_verdict(self):
        contract, error = validate_triage_payload({**_OK_PAYLOAD, "verdict": "buy"})
        assert contract is None and "verdict" in str(error)

    def test_rejects_overlong_reason(self):
        payload = {**_OK_PAYLOAD, "reason": "长" * (MAX_REASON_CHARS + 1)}
        contract, error = validate_triage_payload(payload)
        assert contract is None and "超长" in str(error)

    def test_rejects_missing_evidence(self):
        assert validate_triage_payload({**_OK_PAYLOAD, "evidence": []})[0] is None
        assert validate_triage_payload({**_OK_PAYLOAD, "evidence": None})[0] is None
        bad_item = {**_OK_PAYLOAD, "evidence": [{"value": 1}]}
        assert validate_triage_payload(bad_item)[0] is None

    def test_contract_returns_stripped_reason(self):
        contract, _ = validate_triage_payload({**_OK_PAYLOAD, "reason": "  理由  "})
        assert contract is not None and contract[1] == "理由"


class TestBuildPrompt:
    def test_renders_forecast_and_snapshot_fields_within_budget(self):
        forecast = _forecast_row()
        snapshot = {
            "close": 1500.0,
            "pct_chg": 1.2,
            "amount": 1e9,
            "pe": 30.0,
            "market_cap": 1.9e12,
            "turnover": 0.5,
            "extra": "不应出现",
        }
        prompt = build_triage_prompt(forecast, snapshot_ctx=snapshot)
        assert "600519" in prompt and "贵州茅台" in prompt and "略增" in prompt
        assert "11.57" in prompt and "18.55" in prompt
        assert "1500.0" in prompt  # 快照字段入 prompt
        assert "不应出现" not in prompt  # 预算截断：超 12 字段不渲染
        assert "{{" not in prompt  # 占位符全部替换
        block = prompt.split("<forecast_data>")[1].split("</forecast_data>")[0]
        rendered = [line for line in block.strip().splitlines() if line.strip()]
        assert MAX_INPUT_FIELDS == 12
        assert len(rendered) == MAX_INPUT_FIELDS  # 预告 7 + 快照 5（预算满额）

    def test_renders_without_snapshot_context(self):
        prompt = build_triage_prompt(_forecast_row(), snapshot_ctx=None)
        assert "600519" in prompt and "{{" not in prompt


class TestTriageBatch:
    def test_valid_batch_persists_with_fingerprint(self, tmp_path, monkeypatch):
        db = tmp_path / "events.db"
        upsert_forecasts([_forecast_row()], db_path=db)
        monkeypatch.setattr(
            "finance_agent.outcome.events.triage.load_prompt_with_meta",
            lambda name: type(
                "Info", (), {"template": "t", "prompt_name": name, "prompt_version": 7}
            )(),
        )
        client = _StubClient([_OK_PAYLOAD])
        result = triage_forecasts(
            list_untriaged(as_of="2025-04-16", db_path=db), client=client, db_path=db
        )
        assert result["triaged"] == 1
        assert result["outperform"] == 1
        assert result["degraded"] == 0
        assert result["model"] == "stub-model"
        assert result["prompt_fingerprint"] == "event_triage@7"
        assert client.calls[0]["purpose"] == "quick"

        rows = _triage_rows(db)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "outperform"
        assert rows[0]["prompt_fingerprint"] == "event_triage@7"
        assert rows[0]["model"] == "stub-model"
        assert json.loads(rows[0]["evidence_json"])[0]["field"] == "forecast_type"
        # 已分诊：不再出现在未分诊列表
        assert list_untriaged(as_of="2025-04-16", db_path=db) == []

    @pytest.mark.parametrize(
        "payload",
        [
            {**_OK_PAYLOAD, "verdict": "buy"},
            {**_OK_PAYLOAD, "reason": "长" * 51},
            {**_OK_PAYLOAD, "evidence": []},
        ],
    )
    def test_rejections_degrade_to_in_line(self, tmp_path, payload):
        db = tmp_path / "events.db"
        upsert_forecasts([_forecast_row()], db_path=db)
        result = triage_forecasts(
            list_untriaged(db_path=db), client=_StubClient([payload]), db_path=db
        )
        assert result["in_line"] == 1 and result["degraded"] == 1
        row = _triage_rows(db)[0]
        assert row["verdict"] == "in_line"
        assert row["reason"] == DEGRADED_REASON
        assert result["warnings"][0]["reason"]

    def test_single_llm_error_does_not_block_batch(self, tmp_path):
        db = tmp_path / "events.db"
        rows = [
            _forecast_row(code="600519", announce_date="2025-04-15"),
            _forecast_row(code="000001", announce_date="2025-04-16"),
            _forecast_row(code="600000", announce_date="2025-04-17"),
        ]
        upsert_forecasts(rows, db_path=db)
        client = _StubClient(
            [
                _OK_PAYLOAD,
                RateLimitError("限流"),
                {**_OK_PAYLOAD, "verdict": "risk", "reason": "预减"},
            ]
        )
        result = triage_forecasts(list_untriaged(db_path=db), client=client, db_path=db)
        assert result["triaged"] == 3
        assert result["outperform"] == 1 and result["risk"] == 1 and result["in_line"] == 1
        assert result["degraded"] == 1
        assert result["warnings"][0]["error"] == "RateLimitError"
        assert len(_triage_rows(db)) == 3  # 19/20 类比：其余记录照常产出

    def test_parse_error_degrades(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_forecasts([_forecast_row()], db_path=db)
        client = _StubClient([EventTriageResponseError("不是 JSON", raw_text="{bad")])
        result = triage_forecasts(list_untriaged(db_path=db), client=client, db_path=db)
        assert result["in_line"] == 1 and result["degraded"] == 1

    def test_as_of_guard_skips_future_available_at(self, tmp_path):
        """时点纪律防御：available_at 晚于 as_of 的行不进分诊（MUST NOT 前视）。"""
        db = tmp_path / "events.db"
        upsert_forecasts([_forecast_row(code="600519", announce_date="2025-04-15")], db_path=db)
        rows = list_untriaged(db_path=db)  # 显式传入全部（不预过滤）
        result = triage_forecasts(rows, client=_StubClient([]), db_path=db, as_of="2025-04-14")
        assert result["triaged"] == 0 and result["skipped"] == 1
        assert _triage_rows(db) == []

    def test_concurrency_bound_is_eight(self):
        assert DEFAULT_MAX_WORKERS == 8


class TestGatewayClient:
    def test_wraps_complete_text_and_extracts_json(self, monkeypatch):
        captured: dict = {}

        def _fake_complete_text(messages, **kwargs):
            captured.update(kwargs)
            captured["messages"] = messages
            return '前置说明 {"verdict": "in_line", "reason": "ok"}', {"model": "m-x"}

        monkeypatch.setattr(
            "finance_agent.outcome.events.triage.complete_text", _fake_complete_text
        )
        client = GatewayTriageClient()
        payload = client.complete_json("提示词", purpose="quick", metadata={"k": "v"})
        assert payload["verdict"] == "in_line"
        assert captured["purpose"] == "quick"  # 钉死 quick（成本纪律）
        assert captured["messages"][0]["content"] == "提示词"
        assert client.last_model == "m-x"

    def test_unparseable_response_raises_typed_error(self, monkeypatch):
        monkeypatch.setattr(
            "finance_agent.outcome.events.triage.complete_text",
            lambda *a, **k: ("完全不是 JSON 的文本", {}),
        )
        with pytest.raises(EventTriageResponseError):
            GatewayTriageClient().complete_json("p", purpose="quick", metadata={})


def _triage_rows(db) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM event_triage").fetchall()]
    finally:
        conn.close()
