"""Agent 工具权威注册表（harden-eval-implementation-decoupling）。

工具名唯一权威来源：agent_factory 注册名与 evals/toolcall 默认允许集均引用
此处的常量，避免「评估与实现各维护一份允许集」导致漂移（构造性一致防御：
同源点应是一个注册表，而不是各自副本）。
"""

from __future__ import annotations

TOOL_WEB_SEARCH = "web_search"
TOOL_BATCH_WEB_SEARCH = "batch_web_search"
TOOL_SEARCH_STOCK = "search_stock"
TOOL_RUN_DEEP_ANALYSIS = "run_deep_analysis"
TOOL_START_STOCK_ANALYSIS = "start_stock_analysis"

# 快速模式只读系统查询（P2）
TOOL_GET_SCREENING_POOL = "get_screening_pool"
TOOL_GET_TRACK_RECORD_OVERVIEW = "get_track_record_overview"
TOOL_GET_BROKER_OVERVIEW = "get_broker_overview"
TOOL_GET_DAILY_OPS_STATUS = "get_daily_ops_status"
TOOL_GET_MEMORY_CLAUSES = "get_memory_clauses"
TOOL_GET_FACTOR_LIBRARY = "get_factor_library"
TOOL_GET_FACTOR_RESEARCH_STATUS = "get_factor_research_status"

# 快速模式受控写操作（P3）
TOOL_SET_FACTOR_RESEARCH_RUNNING = "set_factor_research_running"
TOOL_RUN_OPS_JOB = "run_ops_job"
TOOL_REVIEW_MEMORY_CLAUSE = "review_memory_clause"
TOOL_START_POOL_ANALYSIS = "start_pool_analysis"
TOOL_INGEST_RESEARCH_REPORT = "ingest_research_report"

# 读写分类：写 = 产生会话/管线/订单/批次/控制文件等副作用。
READ_TOOLS = frozenset(
    {
        TOOL_WEB_SEARCH,
        TOOL_BATCH_WEB_SEARCH,
        TOOL_SEARCH_STOCK,
        TOOL_GET_SCREENING_POOL,
        TOOL_GET_TRACK_RECORD_OVERVIEW,
        TOOL_GET_BROKER_OVERVIEW,
        TOOL_GET_DAILY_OPS_STATUS,
        TOOL_GET_MEMORY_CLAUSES,
        TOOL_GET_FACTOR_LIBRARY,
        TOOL_GET_FACTOR_RESEARCH_STATUS,
    }
)
WRITE_TOOLS = frozenset(
    {
        TOOL_RUN_DEEP_ANALYSIS,
        TOOL_START_STOCK_ANALYSIS,
        TOOL_SET_FACTOR_RESEARCH_RUNNING,
        TOOL_RUN_OPS_JOB,
        TOOL_REVIEW_MEMORY_CLAUSE,
        TOOL_START_POOL_ANALYSIS,
        TOOL_INGEST_RESEARCH_REPORT,
    }
)

# 全部已注册工具（quick/deep/follow-up 模式）
AGENT_TOOL_NAMES = frozenset(READ_TOOLS | WRITE_TOOLS)

# 快速模式可用的系统操作工具（P1 + P2 + P3）
QUICK_OPERATOR_TOOLS = frozenset(
    {
        TOOL_SEARCH_STOCK,
        TOOL_START_STOCK_ANALYSIS,
        TOOL_GET_SCREENING_POOL,
        TOOL_GET_TRACK_RECORD_OVERVIEW,
        TOOL_GET_BROKER_OVERVIEW,
        TOOL_GET_DAILY_OPS_STATUS,
        TOOL_GET_MEMORY_CLAUSES,
        TOOL_GET_FACTOR_LIBRARY,
        TOOL_GET_FACTOR_RESEARCH_STATUS,
        TOOL_SET_FACTOR_RESEARCH_RUNNING,
        TOOL_RUN_OPS_JOB,
        TOOL_REVIEW_MEMORY_CLAUSE,
        TOOL_START_POOL_ANALYSIS,
        TOOL_INGEST_RESEARCH_REPORT,
    }
)
