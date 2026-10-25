"""系统操作工具注册表契约（add-quick-mode-operator）。"""

from finance_agent.tool_registry import (
    AGENT_TOOL_NAMES,
    QUICK_OPERATOR_TOOLS,
    READ_TOOLS,
    TOOL_INGEST_RESEARCH_REPORT,
    TOOL_REVIEW_MEMORY_CLAUSE,
    TOOL_RUN_OPS_JOB,
    TOOL_SEARCH_STOCK,
    TOOL_SET_FACTOR_RESEARCH_RUNNING,
    TOOL_START_POOL_ANALYSIS,
    TOOL_START_STOCK_ANALYSIS,
    WRITE_TOOLS,
)


def test_quick_operator_tools_are_single_sourced():
    expected = {
        TOOL_SEARCH_STOCK,
        TOOL_START_STOCK_ANALYSIS,
        "get_screening_pool",
        "get_track_record_overview",
        "get_broker_overview",
        "get_daily_ops_status",
        "get_memory_clauses",
        "get_factor_library",
        "get_factor_research_status",
        TOOL_SET_FACTOR_RESEARCH_RUNNING,
        TOOL_RUN_OPS_JOB,
        TOOL_REVIEW_MEMORY_CLAUSE,
        TOOL_START_POOL_ANALYSIS,
        TOOL_INGEST_RESEARCH_REPORT,
    }
    assert frozenset(expected) == QUICK_OPERATOR_TOOLS
    assert QUICK_OPERATOR_TOOLS <= AGENT_TOOL_NAMES


def test_read_and_write_sets_are_disjoint():
    assert not (READ_TOOLS & WRITE_TOOLS)
    assert QUICK_OPERATOR_TOOLS <= (READ_TOOLS | WRITE_TOOLS)
    assert QUICK_OPERATOR_TOOLS & READ_TOOLS
    assert QUICK_OPERATOR_TOOLS & WRITE_TOOLS
    assert TOOL_SEARCH_STOCK in READ_TOOLS
    assert TOOL_START_STOCK_ANALYSIS in WRITE_TOOLS
    assert TOOL_SET_FACTOR_RESEARCH_RUNNING in WRITE_TOOLS
    assert TOOL_RUN_OPS_JOB in WRITE_TOOLS
    assert TOOL_REVIEW_MEMORY_CLAUSE in WRITE_TOOLS
    assert TOOL_START_POOL_ANALYSIS in WRITE_TOOLS
    assert TOOL_INGEST_RESEARCH_REPORT in WRITE_TOOLS
