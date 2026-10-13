"""build_agent 工厂函数测试。

验证三种模式的 Agent 配置：工具集、max_iterations、system prompt。
"""

import inspect

from finance_agent.agent_factory import _make_run_deep_analysis, _resolve_peer_codes, build_agent


class TestBuildAgentQuick:
    """快速模式 Agent 配置。"""

    def test_returns_agent_instance(self):
        """build_agent 返回 Agent 实例。"""
        agent = build_agent(mode="quick", api_key="test-key")
        assert agent is not None

    def test_quick_mode_has_web_search_tool(self):
        """快速模式暴露 web_search 工具。"""
        agent = build_agent(mode="quick", api_key="test-key")
        tool_names = agent.tools.get_tool_names()
        assert "web_search" in tool_names

    def test_quick_mode_does_not_have_deep_analysis(self):
        """快速模式不暴露 run_deep_analysis 工具。"""
        agent = build_agent(mode="quick", api_key="test-key")
        tool_names = agent.tools.get_tool_names()
        assert "run_deep_analysis" not in tool_names

    def test_quick_mode_max_iterations_is_3(self):
        """快速模式 max_iterations=3。"""
        agent = build_agent(mode="quick", api_key="test-key")
        assert agent.max_iterations == 3


class TestBuildAgentDeep:
    """深度模式 Agent 配置。"""

    def test_returns_agent_instance(self):
        """build_agent 返回 Agent 实例。"""
        agent = build_agent(mode="deep", api_key="test-key")
        assert agent is not None

    def test_deep_mode_has_search_stock(self):
        """深度模式暴露 search_stock 工具。"""
        agent = build_agent(mode="deep", api_key="test-key")
        tool_names = agent.tools.get_tool_names()
        assert "search_stock" in tool_names

    def test_deep_mode_has_run_deep_analysis(self):
        """深度模式暴露 run_deep_analysis 工具。"""
        agent = build_agent(mode="deep", api_key="test-key")
        tool_names = agent.tools.get_tool_names()
        assert "run_deep_analysis" in tool_names

    def test_deep_mode_has_web_search(self):
        """深度模式暴露 web_search 工具。"""
        agent = build_agent(mode="deep", api_key="test-key")
        tool_names = agent.tools.get_tool_names()
        assert "web_search" in tool_names

    def test_deep_mode_max_iterations_is_10(self):
        """深度模式 max_iterations=10。"""
        agent = build_agent(mode="deep", api_key="test-key")
        assert agent.max_iterations == 10


class TestRunDeepAnalysisPeerCodes:
    """run_deep_analysis 暴露 peer_codes 参数（add-peer-comparison）。

    LLM 显式传参优先于请求级闭包注入；归一化=strip/剔非 6 位数字/去重/
    剔主标的/上限 3。
    """

    def test_tool_signature_has_peer_codes(self):
        fn = _make_run_deep_analysis()
        sig = inspect.signature(fn)
        assert "peer_codes" in sig.parameters
        assert sig.parameters["peer_codes"].default is None

    def test_tool_docstring_documents_peer_codes(self):
        fn = _make_run_deep_analysis()
        assert "peer_codes" in (fn.__doc__ or "")

    def test_resolve_explicit_overrides_closure(self):
        assert _resolve_peer_codes(["000858"], ["601318"]) == ["000858"]
        assert _resolve_peer_codes(None, ["601318"]) == ["601318"]
        assert _resolve_peer_codes(None, None) is None
        assert _resolve_peer_codes([], None) is None

    def test_resolve_sanitizes_and_excludes_target(self):
        assert _resolve_peer_codes(
            [" 000858 ", "bad", "000858", "600519"], None, exclude="600519"
        ) == ["000858"]

    def test_resolve_caps_at_three(self):
        assert _resolve_peer_codes(["000001", "000002", "600000", "600519"], None) == [
            "000001",
            "000002",
            "600000",
        ]

    def test_resolve_tolerates_comma_joined_string(self):
        """schema string 类型容错：LLM 可能传逗号串，须切分而非逐字符校验。

        build_schema_from_function 对 list[str] | None 注解 fallback 为 JSON
        "string"，LLM 端实际可能发 "000858,600519" 这类逗号串。
        """
        assert _resolve_peer_codes("000858,600519", None, exclude="600519") == ["000858"]
        assert _resolve_peer_codes("000858， 601318", None) == ["000858", "601318"]

    def test_resolve_tolerates_json_array_string(self):
        """schema string 槽位下 LLM 高频把列表 JSON 序列化进字符串——须解析而非逐字符丢弃。"""
        assert _resolve_peer_codes('["000858","600519"]', None, exclude="600519") == ["000858"]
        assert _resolve_peer_codes('["000858", "601318"]', None) == ["000858", "601318"]

    def test_llm_schema_exposes_peer_codes(self):
        """工具 schema 对 LLM 可见 peer_codes（build_schema_from_function 从签名内省）。"""
        agent = build_agent(mode="deep", api_key="test-key")
        schemas = agent.tools.get_schemas_for_llm()
        rda = next(
            s
            for s in schemas
            if s.get("function", {}).get("name") == "run_deep_analysis"
            or s.get("name") == "run_deep_analysis"
        )
        params = rda.get("function", rda).get("parameters", {})
        assert "peer_codes" in params.get("properties", {})


# ── add-analyst-factor-tools Task 2：技术分析师因子库工具（注册 + 用途标签） ─────


class TestAnalystFactorToolRegistration:
    """技术分析师工具集（唯一消费方）与用途标签常量逐字。"""

    def test_tool_name_and_purpose_tag_verbatim(self):
        from finance_agent.agent_factory import (
            PURPOSE_TAG_ANALYST_FACTOR_TOOL,
            TOOL_QUERY_FACTOR_LIBRARY,
        )

        assert TOOL_QUERY_FACTOR_LIBRARY == "query_factor_library"
        assert PURPOSE_TAG_ANALYST_FACTOR_TOOL == "analyst_factor_tool"

    def test_technical_toolset_contains_factor_library(self):
        from finance_agent.agent_factory import analyst_tool_names

        assert analyst_tool_names("technical") == ("query_factor_library",)

    def test_other_analysts_unchanged_no_factor_tool(self):
        """非目标：其他三个分析师工具集保持空（零改动）。"""
        from finance_agent.agent_factory import analyst_tool_names

        for analyst in ("macro", "fundamental", "sentiment"):
            assert analyst_tool_names(analyst) == ()
            assert "query_factor_library" not in analyst_tool_names(analyst)

    def test_unknown_analyst_rejected(self):
        import pytest

        from finance_agent.agent_factory import analyst_tool_names

        with pytest.raises(KeyError):
            analyst_tool_names("no_such_analyst")


class TestAnalystFactorToolShell:
    """LLM 面薄壳：symbol → 结构化 JSON；经既有 _trace_tool 记录 span/耗时/用途标签。"""

    _DATES = [f"2026-01-{day:02d}" for day in range(1, 26)]
    _CLOSES = {
        "300750": [100.0 + i for i in range(25)],
        "600519": [200.0 + 2 * i for i in range(25)],
        "000858": [50.0 + 0.5 * i for i in range(25)],
    }

    def _seed_snapshot(self, path) -> None:
        from finance_agent.data.snapshot import write_snapshot_rows

        for day, index in zip(self._DATES, range(25), strict=True):
            rows = []
            for code, closes in self._CLOSES.items():
                close = closes[index]
                rows.append(
                    {
                        "code": code,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close_raw": close,
                        "close_qfq": close,
                        "volume": 1000.0,
                        "amount": 1000.0 * close,
                    }
                )
            write_snapshot_rows(day, rows, db_path=path)

    def _seed_library(self, path, as_of: str) -> None:
        from finance_agent.factors.dsl import parse, serialize
        from finance_agent.factors.library import add_factor, record_ic

        factor_id = add_factor(
            expression="delta(close,20)",
            hypothesis="测试用假设文本，描述该因子与未来收益的关系。",
            category="动量",
            status="active",
            ast_json=serialize(parse("delta(close,20)")),
            db_path=path,
        )
        for day, value in zip(self._DATES[:3], (0.1, 0.2, 0.3), strict=True):
            record_ic(factor_id, day, value, db_path=path)

    def test_shell_payload_value_zscore_ic(self, tmp_path):
        """端到端：快照库 → 面板 → 因子库 → JSON（value/zscore/近 60 日 IC 齐全）。"""
        import json

        from finance_agent.agent_factory import make_query_factor_library

        snapshot_db = tmp_path / "snapshot.db"
        library_db = tmp_path / "library.db"
        self._seed_snapshot(snapshot_db)
        self._seed_library(library_db, as_of=self._DATES[-1])
        tool = make_query_factor_library(
            snapshot_db_path=str(snapshot_db), library_db_path=str(library_db)
        )
        payload = json.loads(tool("300750"))
        assert payload["as_of"] == self._DATES[-1]
        entry = payload["factors"][0]
        # 手算：delta(close,20) = close[-1] − close[-21]
        assert abs(entry["value"] - (124.0 - 104.0)) < 1e-9
        values = {code: closes[24] - closes[4] for code, closes in self._CLOSES.items()}
        numbers = list(values.values())
        mean = sum(numbers) / len(numbers)
        std = (sum((v - mean) ** 2 for v in numbers) / (len(numbers) - 1)) ** 0.5
        assert abs(entry["zscore"] - (values["300750"] - mean) / std) < 1e-9
        assert abs(entry["ic_mean_60d"] - 0.2) < 1e-9
        assert abs(entry["icir_60d"] - 2.0) < 1e-9

    def test_shell_degrades_when_snapshot_empty(self, tmp_path):
        """快照库建表但无行 → 空 factors + note，不抛错（不阻断分析师）。"""
        import json

        from finance_agent.agent_factory import make_query_factor_library
        from finance_agent.data.snapshot import init_snapshot_db

        snapshot_db = tmp_path / "snapshot.db"
        init_snapshot_db(snapshot_db)
        tool = make_query_factor_library(
            snapshot_db_path=str(snapshot_db), library_db_path=str(tmp_path / "library.db")
        )
        payload = json.loads(tool("300750"))
        assert payload["factors"] == []
        assert payload["note"]

    def test_shell_degrades_without_creating_missing_dbs(self, tmp_path):
        """库文件不存在 → 诚实空因 + note，且**不**创建任何库文件（只读纪律）。"""
        import json

        from finance_agent.agent_factory import make_query_factor_library

        snapshot_db = tmp_path / "absent_snapshot.db"
        library_db = tmp_path / "absent_library.db"
        tool = make_query_factor_library(
            snapshot_db_path=str(snapshot_db), library_db_path=str(library_db)
        )
        payload = json.loads(tool("300750"))
        assert payload["factors"] == []
        assert payload["note"]
        assert not snapshot_db.exists()
        assert not library_db.exists()

    def test_shell_span_records_purpose_tag_and_duration(self, tmp_path):
        """经既有 _trace_tool 机制：span 名 tool_call:query_factor_library +
        purpose_tag=analyst_factor_tool + 耗时 duration_ms（可观测性，按既有模式）。"""
        from unittest.mock import patch

        from finance_agent.agent_factory import make_query_factor_library
        from tests.nodes.test_trace_tool import FakeClient

        client = FakeClient()
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=client):
            tool = make_query_factor_library(
                snapshot_db_path=str(tmp_path / "absent_snapshot.db"),
                library_db_path=str(tmp_path / "absent_library.db"),
            )
            tool(symbol="300750")
        name, inputs, span = client.spans[0]
        assert name == "tool_call:query_factor_library"
        assert inputs == {"call": {"symbol": "300750"}}
        assert span.metadata["purpose_tag"] == "analyst_factor_tool"
        duration = span.metadata["duration_ms"]
        assert isinstance(duration, int) and duration >= 0
