"""TDD tests for graph.py build_5layer_graph — ADR-0011 五层架构图拓扑。

测试图节点注册和边连接是否正确。
"""

from finance_agent.graph import build_5layer_graph


class TestBuild5LayerGraph:
    """5 层架构图构建测试。"""

    def test_graph_compiles(self):
        """图能成功编译。"""
        graph = build_5layer_graph()
        assert graph is not None

    def test_has_prep_nodes(self):
        """包含 PREP 阶段节点。"""
        graph = build_5layer_graph()
        nodes = set(graph.nodes.keys())
        assert "compute_metrics" in nodes

    def test_has_analyst_nodes(self):
        """包含 Layer I 分析师节点。"""
        graph = build_5layer_graph()
        nodes = set(graph.nodes.keys())
        assert "technical_analyst" in nodes

    def test_has_debate_nodes(self):
        """包含 Layer II 辩论节点（两轮）。"""
        graph = build_5layer_graph()
        nodes = set(graph.nodes.keys())
        assert "bull_r1" in nodes
        assert "bear_r1" in nodes
        assert "bull_r2" in nodes
        assert "bear_r2" in nodes
        assert "research_manager" in nodes

    def test_has_trader_and_risk_nodes(self):
        """包含 Layer III/IV 节点。"""
        graph = build_5layer_graph()
        nodes = set(graph.nodes.keys())
        assert "trader" in nodes
        assert "risk_judge" in nodes
        assert "aggressive_r1" in nodes
        assert "conservative_r1" in nodes
        assert "neutral_r1" in nodes

    def test_has_fund_manager_and_report(self):
        """包含 Layer V 和报告生成节点。"""
        graph = build_5layer_graph()
        nodes = set(graph.nodes.keys())
        assert "fund_manager" in nodes
        assert "generate_report" in nodes

    def test_has_citation_verification(self):
        """包含引用校验节点。"""
        graph = build_5layer_graph()
        nodes = set(graph.nodes.keys())
        assert "verify_citations" in nodes


class TestFmReturnReasoningContract:
    """calibrate-fm-approval 回路契约（线上 4.3 复现修复）：

    FM return 的 reasoning 必须被 state schema 声明——未声明键会被 LangGraph
    节点输出合并时丢弃，trader 重跑上下文拿不到退回意见（实测重跑输入与首跑
    逐字节相同，注入条件 ``and fm_reasoning`` 恒为假）。
    """

    def test_state_schema_declares_reasoning_key(self):
        from finance_agent.state import AnalysisState

        assert "fund_manager_decision_reasoning" in AnalysisState.__annotations__

    def test_compiled_graph_channel_keeps_reasoning(self):
        from finance_agent.graph import build_5layer_graph

        graph = build_5layer_graph()
        assert "fund_manager_decision_reasoning" in graph.channels


class TestNodeOutputChannels:
    """图通道契约（incident 027 守卫推广为系统性门禁）：

    节点写入 state 的每个键都必须在 AnalysisState 声明且已建图通道——未声明键
    会被 LangGraph 静默丢弃（027 实测：price_check 家族/citation 回路键整条哑火）。
    本门禁覆盖 compute_metrics 的全部产出键（含可选分支），新键未声明即红。
    """

    @staticmethod
    def _full_branch_state(balance_sheet, income_statement, cash_flow, indicators) -> dict:
        """构造触发 compute_metrics 全部分支的 state（K 线/基准/季报/同业/行情）。"""
        import pandas as pd

        n = 40
        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-02", periods=n, freq="B"),
                "开盘": [10.0 + i * 0.1 for i in range(n)],
                "收盘": [10.1 + i * 0.1 for i in range(n)],
                "最高": [10.2 + i * 0.1 for i in range(n)],
                "最低": [9.9 + i * 0.1 for i in range(n)],
                "成交量": [1000 + i for i in range(n)],
            }
        )
        return {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "cash_flow_statement": cash_flow,
            "financial_indicators": indicators,
            "industry_info": {"industry": "白酒"},
            "stock_quote": {"PE": 20.0, "PB": 3.0},
            "peer_financials": [{"code": "000001", "PE": 12.0, "PB": 1.1}],
            "kline": kline,
            "benchmark_kline": kline.copy(),
            "quarterly_income": income_statement.copy(),
        }

    def test_compute_outputs_all_declared_and_channeled(
        self, balance_sheet, income_statement, cash_flow, indicators
    ):
        from finance_agent.graph import build_5layer_graph
        from finance_agent.nodes.compute import compute_metrics
        from finance_agent.state import AnalysisState

        produced = set(
            compute_metrics(
                self._full_branch_state(balance_sheet, income_statement, cash_flow, indicators)
            )
        )
        declared = set(AnalysisState.__annotations__)
        missing_declared = produced - declared
        assert not missing_declared, (
            f"未声明键会被 LangGraph 静默丢弃（027 教训）：{sorted(missing_declared)}"
        )

        channels = set(build_5layer_graph().channels)
        missing_channel = produced - channels
        assert not missing_channel, f"已声明但未建图通道：{sorted(missing_channel)}"
