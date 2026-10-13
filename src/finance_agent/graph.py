"""LangGraph 主图定义：5 层架构 + 条件路由 + Send 并行派发（ADR-0011）。"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from finance_agent.memory import memory_recall_entry
from finance_agent.nodes._timing import timed_node
from finance_agent.nodes.analysts import (
    fundamental_analyst,
    macro_analyst,
    sentiment_analyst,
    technical_analyst,
)
from finance_agent.nodes.cache import check_cache
from finance_agent.nodes.citation_node import verify_citations
from finance_agent.nodes.compute import compute_metrics
from finance_agent.nodes.debate import bear_debater, bull_debater
from finance_agent.nodes.fetch import fetch_data
from finance_agent.nodes.fund_manager import fund_manager
from finance_agent.nodes.output import generate_file
from finance_agent.nodes.report import generate_report
from finance_agent.nodes.research_manager import research_manager
from finance_agent.nodes.risk import (
    aggressive_debater,
    conservative_debater,
    neutral_debater,
    risk_judge,
)
from finance_agent.nodes.trader import trader
from finance_agent.nodes.validate import validate_node, validate_trade_prices
from finance_agent.routing import (
    after_check_cache,
    after_citation,
    after_fund_manager,
    after_risk_judge,
    after_validate,
    after_validate_trade_prices,
    route_to_analysts,
    route_to_debate_r1,
    route_to_debate_r2,
    route_to_risk_r1,
    route_to_risk_r2,
)
from finance_agent.state import AnalysisState


def _passthrough(state: dict) -> dict:
    """空状态更新，用于 Send 派发的 entry/collector 节点。"""
    return {}


def build_5layer_graph() -> CompiledStateGraph:
    """ADR-0011 五层架构图：PREP -> 5 层 Agent -> 报告。"""
    graph = StateGraph(AnalysisState)  # pyrefly: ignore[bad-specialization]

    # 全部业务节点统一用 timed_node 包裹：在真实入口/出口发 node_start/node_end
    # custom 事件（修复 updates 流对快速节点只产出一个 chunk 导致的计时恒 0）。
    # _passthrough entry 节点无业务逻辑、瞬时完成，不包裹。
    _t = timed_node

    # ── PREP 节点（复用现有）──
    graph.add_node("check_cache", _t("check_cache")(check_cache))
    graph.add_node("fetch_data", _t("fetch_data")(fetch_data))
    graph.add_node("validate_financials", _t("validate_financials")(validate_node))
    graph.add_node("compute_metrics", _t("compute_metrics")(compute_metrics))

    # ── Layer I: Analyst Team ──
    # analysts_entry 兼记忆召回（add-decision-memory）：深度分析启动时检索同标的/同行业
    # 已结算历史 + 已确认语义条款 → state `decision_memory`（无历史 → 空结构，零行为增量）。
    # 节点名不变（既有图结构/计时契约零修改）；citation 重试回边幂等复用已写入的快照。
    graph.add_node("analysts_entry", memory_recall_entry)
    graph.add_node("technical_analyst", _t("technical_analyst")(technical_analyst))
    graph.add_node("macro_analyst", _t("macro_analyst")(macro_analyst))
    graph.add_node("fundamental_analyst", _t("fundamental_analyst")(fundamental_analyst))
    graph.add_node("sentiment_analyst", _t("sentiment_analyst")(sentiment_analyst))

    # ── Citation Verification ──
    graph.add_node("verify_citations", _t("verify_citations")(verify_citations))

    # ── Layer II: Bull/Bear Debate ──
    graph.add_node("debate_r1_entry", _passthrough)
    graph.add_node("bull_r1", _t("bull_r1")(bull_debater))
    graph.add_node("bear_r1", _t("bear_r1")(bear_debater))
    graph.add_node("debate_r2_entry", _passthrough)
    graph.add_node("bull_r2", _t("bull_r2")(bull_debater))
    graph.add_node("bear_r2", _t("bear_r2")(bear_debater))
    graph.add_node("research_manager", _t("research_manager")(research_manager))

    # ── Layer III: Trader ──
    graph.add_node("trader", _t("trader")(trader))
    graph.add_node("validate_trade_prices", _t("validate_trade_prices")(validate_trade_prices))

    # ── Layer IV: Risk Management ──
    graph.add_node("risk_r1_entry", _passthrough)
    graph.add_node("aggressive_r1", _t("aggressive_r1")(aggressive_debater))
    graph.add_node("conservative_r1", _t("conservative_r1")(conservative_debater))
    graph.add_node("neutral_r1", _t("neutral_r1")(neutral_debater))
    graph.add_node("risk_r2_entry", _passthrough)
    graph.add_node("aggressive_r2", _t("aggressive_r2")(aggressive_debater))
    graph.add_node("conservative_r2", _t("conservative_r2")(conservative_debater))
    graph.add_node("neutral_r2", _t("neutral_r2")(neutral_debater))
    graph.add_node("risk_judge", _t("risk_judge")(risk_judge))

    # ── Layer V: Fund Manager ──
    graph.add_node("fund_manager", _t("fund_manager")(fund_manager))

    # ── Report ──
    graph.add_node("generate_report", _t("generate_report")(generate_report))
    graph.add_node("generate_file", _t("generate_file")(generate_file))

    # ── 边：PREP ──
    graph.add_edge(START, "check_cache")
    graph.add_conditional_edges("check_cache", after_check_cache)
    graph.add_edge("fetch_data", "validate_financials")
    graph.add_conditional_edges("validate_financials", after_validate)
    graph.add_edge("compute_metrics", "analysts_entry")

    # ── 边：Layer I ──
    graph.add_conditional_edges("analysts_entry", route_to_analysts)
    graph.add_edge("technical_analyst", "verify_citations")
    graph.add_edge("macro_analyst", "verify_citations")
    graph.add_edge("fundamental_analyst", "verify_citations")
    graph.add_edge("sentiment_analyst", "verify_citations")

    # ── 边：Citation ──
    graph.add_conditional_edges(
        "verify_citations",
        after_citation,
        {"render": "debate_r1_entry", "retry": "analysts_entry"},
    )

    # ── 边：Layer II Debate ──
    graph.add_conditional_edges("debate_r1_entry", route_to_debate_r1)
    graph.add_edge("bull_r1", "debate_r2_entry")
    graph.add_edge("bear_r1", "debate_r2_entry")
    graph.add_conditional_edges("debate_r2_entry", route_to_debate_r2)
    graph.add_edge("bull_r2", "research_manager")
    graph.add_edge("bear_r2", "research_manager")

    # ── 边：Layer III ──
    graph.add_edge("research_manager", "trader")

    # ── 边：Layer III→IV（价位 sanity 校验：fail 打回 trader，pass/corrected 前行）──
    graph.add_edge("trader", "validate_trade_prices")
    graph.add_conditional_edges("validate_trade_prices", after_validate_trade_prices)
    graph.add_conditional_edges("risk_r1_entry", route_to_risk_r1)
    graph.add_edge("aggressive_r1", "risk_r2_entry")
    graph.add_edge("conservative_r1", "risk_r2_entry")
    graph.add_edge("neutral_r1", "risk_r2_entry")
    graph.add_conditional_edges("risk_r2_entry", route_to_risk_r2)
    graph.add_edge("aggressive_r2", "risk_judge")
    graph.add_edge("conservative_r2", "risk_judge")
    graph.add_edge("neutral_r2", "risk_judge")

    # ── 边：Layer V + Report ──
    # 决策价位交叉校验门禁（update-decision-price-gate）：gate fail 阻断——
    # 不进 FM 审批、不产出报告（after_risk_judge 条件路由）。显式 path_map 与
    # verify_citations 同款：langgraph 1.2 无 map 的条件边不可静态推断，draw 塌缩。
    graph.add_conditional_edges(
        "risk_judge",
        after_risk_judge,
        {"__end__": END, "fund_manager": "fund_manager"},
    )
    graph.add_conditional_edges("fund_manager", after_fund_manager)
    graph.add_edge("generate_report", "generate_file")
    graph.add_edge("generate_file", END)

    return graph.compile()
