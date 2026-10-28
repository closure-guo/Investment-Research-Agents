"""选股规则运行时配置单测（delta add-screening-rules-config-ui）。

覆盖：默认值解析、默认值与 pipeline 常量钉死、保存/持久化、非法值整体拒绝、
env 回退与披露、文件损坏/字段非法按项回退、file > env 优先级。
"""

import json

import pytest

from finance_agent.screening import rules_config


@pytest.fixture()
def rules_path(tmp_path, monkeypatch):
    path = tmp_path / "screening_rules.json"
    monkeypatch.setenv("SCREENING_RULES_CONFIG_PATH", str(path))
    for var in (
        "SCREENING_MIN_TRADING_DAYS",
        "SCREENING_MIN_AVG_AMOUNT_20D",
        "SCREENING_L2_TOP_N",
    ):
        monkeypatch.delenv(var, raising=False)
    return path


def test_default_when_no_file_no_env(rules_path):
    state = rules_config.resolve()
    assert state["min_trading_days"] == {
        "value": 60,
        "source": "default",
        "default": 60,
        "min": 1,
        "max": 250,
        "env_value": None,
    }
    assert state["min_avg_amount_20d"]["value"] == 50_000_000.0
    assert state["l2_top_n"]["value"] == 100
    assert state["file_status"] == "missing"
    assert state["updated_at"] is None


def test_default_l2_top_n_pins_pipeline_constant():
    # 防两处字面量漂移（design.md 风险 1）
    from finance_agent.screening.pipeline import L2_TOP_N

    assert rules_config.DEFAULT_L2_TOP_N == L2_TOP_N == 100


def test_save_then_resolve_file_source(rules_path):
    state = rules_config.save(
        {"min_trading_days": 80, "min_avg_amount_20d": 80_000_000, "l2_top_n": 50}
    )
    assert state["min_trading_days"]["value"] == 80
    assert state["min_trading_days"]["source"] == "file"
    assert state["l2_top_n"]["value"] == 50
    assert state["file_status"] == "ok"
    assert state["updated_at"] is not None
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    assert data["min_trading_days"] == 80


@pytest.mark.parametrize(
    "payload",
    [
        {"min_trading_days": 0, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        {"min_trading_days": 80, "min_avg_amount_20d": 8e7, "l2_top_n": 0},
        {"min_trading_days": 80, "min_avg_amount_20d": 1e10, "l2_top_n": 50},
        {"min_trading_days": 3.5, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        {"min_trading_days": True, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        {"min_trading_days": 80, "min_avg_amount_20d": "8000万", "l2_top_n": 50},
    ],
)
def test_save_invalid_rejected(rules_path, payload):
    rules_config.save({"min_trading_days": 60, "min_avg_amount_20d": 5e7, "l2_top_n": 100})
    before = rules_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        rules_config.save(payload)
    assert rules_path.read_text(encoding="utf-8") == before


def test_save_missing_field_rejected(rules_path):
    with pytest.raises(ValueError):
        rules_config.save({"l2_top_n": 50})
    assert rules_config.resolve()["file_status"] == "missing"


def test_env_fallback_and_disclosure(rules_path, monkeypatch):
    monkeypatch.setenv("SCREENING_L2_TOP_N", "50")
    monkeypatch.setenv("SCREENING_MIN_TRADING_DAYS", "abc")  # 非法 env 忽略
    state = rules_config.resolve()
    assert state["l2_top_n"]["value"] == 50
    assert state["l2_top_n"]["source"] == "env"
    assert state["l2_top_n"]["env_value"] == 50
    assert state["min_trading_days"]["source"] == "default"


def test_corrupt_file_falls_back(rules_path):
    rules_path.write_text("{not json", encoding="utf-8")
    state = rules_config.resolve()
    assert state["file_status"] == "corrupt"
    assert state["l2_top_n"]["source"] == "default"


def test_invalid_field_falls_back_per_item(rules_path):
    rules_path.write_text(
        json.dumps({"min_trading_days": 99999, "min_avg_amount_20d": 8e7, "l2_top_n": 50}),
        encoding="utf-8",
    )
    state = rules_config.resolve()
    assert state["file_status"] == "invalid"
    assert state["min_trading_days"]["source"] == "default"
    assert state["min_avg_amount_20d"]["source"] == "file"
    assert state["l2_top_n"]["source"] == "file"


def test_file_overrides_env(rules_path, monkeypatch):
    monkeypatch.setenv("SCREENING_L2_TOP_N", "30")
    rules_config.save({"min_trading_days": 60, "min_avg_amount_20d": 5e7, "l2_top_n": 50})
    assert rules_config.resolve()["l2_top_n"]["value"] == 50
