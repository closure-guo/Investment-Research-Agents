"""quick_mode 提示词契约（add-quick-mode-operator）。"""

from pathlib import Path


def test_quick_prompt_has_operator_dispatch_policy():
    text = Path("src/finance_agent/prompts/quick_mode.md").read_text(encoding="utf-8")

    assert "search_stock" in text
    assert "start_stock_analysis" in text
    assert "{operator_note}" in text
    assert "不得" in text
    assert "已完成" in text


def test_quick_prompt_has_read_and_write_tool_policy():
    text = Path("src/finance_agent/prompts/quick_mode.md").read_text(encoding="utf-8")

    for name in (
        "get_screening_pool",
        "get_track_record_overview",
        "get_broker_overview",
        "get_daily_ops_status",
        "get_memory_clauses",
        "get_factor_library",
        "get_factor_research_status",
        "set_factor_research_running",
        "run_ops_job",
        "review_memory_clause",
        "start_pool_analysis",
    ):
        assert name in text, name


def test_quick_prompt_has_confirmation_protocol():
    text = Path("src/finance_agent/prompts/quick_mode.md").read_text(encoding="utf-8")

    assert "confirm=false" in text
    assert "confirm=true" in text
    assert "确认执行" in text
    assert "不得自确认" in text or "不要自己确认" in text
    assert "pending" in text and "honor" in text
