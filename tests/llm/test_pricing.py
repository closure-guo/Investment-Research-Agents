"""模型价格解析与成本计算（成本可观测性，2026-10-22）。"""

from __future__ import annotations

import json

from finance_agent.llm.pricing import (
    ModelPrice,
    cost_details,
    load_prices,
    parse_prices,
)


class TestParsePrices:
    def test_empty_or_invalid_returns_empty(self):
        assert parse_prices(None) == []
        assert parse_prices("") == []
        assert parse_prices("not-json") == []
        assert parse_prices("[]") == []
        assert parse_prices('{"m": {"input": "x", "output": 1}}') == []

    def test_negative_or_zero_prices_dropped(self):
        raw = json.dumps(
            {
                "a": {"input": -1, "output": 1},
                "b": {"input": 0, "output": 0},
                "c": {"input": 0.1, "output": 0.2},
            }
        )
        assert [p.name for p in parse_prices(raw)] == ["c"]

    def test_valid_prices_sorted(self):
        raw = json.dumps(
            {"zeta": {"input": 1, "output": 2}, "alpha": {"input": 0.1, "output": 0.2}}
        )
        assert [p.name for p in parse_prices(raw)] == ["alpha", "zeta"]

    def test_langfuse_payload_converts_per_mtok_to_per_token(self):
        price = ModelPrice(name="deepseek-v4.1-flash", input_per_mtok=0.27, output_per_mtok=1.1)
        payload = price.as_langfuse_payload()
        assert payload["unit"] == "TOKENS"
        assert payload["inputPrice"] == 0.27 / 1_000_000
        assert payload["outputPrice"] == 1.1 / 1_000_000
        assert payload["matchPattern"].startswith("(?i)^(.*/)?")
        assert "deepseek" in payload["matchPattern"]

    def test_load_prices_reads_env_at_call_time(self):
        env = {"LLM_MODEL_PRICES": json.dumps({"m": {"input": 1, "output": 2}})}
        assert load_prices(env)[0].name == "m"
        assert load_prices({}) == []


class TestCostDetails:
    def test_unknown_model_returns_none(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_MODEL_PRICES", json.dumps({"deepseek-v4.1-flash": {"input": 0.27, "output": 1.1}})
        )
        assert cost_details("glm-5.3-flash", {"input": 1000, "output": 500}) is None
        assert cost_details(None, {"input": 1, "output": 1}) is None

    def test_prefixed_model_matches_and_computes_cost(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_MODEL_PRICES",
            json.dumps({"deepseek-v4.1-flash": {"input": 0.27, "output": 1.1}}),
        )
        cost = cost_details("openai/deepseek-v4.1-flash", {"input": 1_000_000, "output": 1_000_000})
        assert cost is not None
        assert cost["input"] == 0.27
        assert cost["output"] == 1.1
        assert cost["total"] == 1.37

    def test_zero_usage_returns_none(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL_PRICES", json.dumps({"m": {"input": 1, "output": 2}}))
        assert cost_details("m", {"input": 0, "output": 0}) is None
        assert cost_details("m", {}) is None

    def test_no_config_no_cost(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL_PRICES", raising=False)
        assert cost_details("any", {"input": 10, "output": 10}) is None
