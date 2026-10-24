"""LLM 配置 API 端点集成测试。

覆盖 tasks.md 4.9-4.12：
- GET /api/llm-config 返回默认配置且不含 apiKey
- POST /api/llm-config/models 成功返回模型列表、不支持时返回空列表
- POST /api/llm-config/test 成功返回 latency、失败返回正确 errorType
- AnalyzeRequest / ChatRequest 携带 llm_config 时不报错
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from finance_agent.api import AnalyzeRequest, ChatRequest, app

# ── 4.10: GET /api/llm-config ──


def test_get_llm_config_returns_defaults():
    """GET /api/llm-config 返回 model/baseUrl/thinking 三字段，不含 apiKey。"""
    with (
        patch.dict(
            "os.environ",
            {"LLM_MODEL": "deepseek/deepseek-chat", "LLM_BASE_URL": "https://api.test.com/v1"},
        ),
        TestClient(app) as client,
    ):
        resp = client.get("/api/llm-config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "deepseek/deepseek-chat"
    assert data["baseUrl"] == "https://api.test.com/v1"
    assert "thinking" in data
    # 安全：不返回 apiKey
    assert "apiKey" not in data
    assert "api_key" not in data


def test_get_llm_config_no_env():
    """无环境变量时 GET /api/llm-config 返回内置默认值。"""
    with patch.dict("os.environ", {}, clear=True), TestClient(app) as client:
        resp = client.get("/api/llm-config")

    assert resp.status_code == 200
    data = resp.json()
    # 内置默认 model
    assert "model" in data
    assert data["baseUrl"] == ""
    assert data["thinking"] == "enabled"


# ── 4.11: POST /api/llm-config/models ──


def test_list_models_success():
    """POST /api/llm-config/models 成功返回模型列表。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {"id": "deepseek-chat"},
            {"id": "deepseek-reasoner"},
            {"id": "deepseek-coder"},
        ]
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client), TestClient(app) as client:
        resp = client.post(
            "/api/llm-config/models",
            json={"baseUrl": "https://api.deepseek.com/v1", "apiKey": "sk-test"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == ["deepseek-chat", "deepseek-coder", "deepseek-reasoner"]
    assert data["error"] is None


def test_list_models_no_base_url():
    """无 baseUrl 时返回空列表 + 错误提示。"""
    with patch.dict("os.environ", {}, clear=True), TestClient(app) as client:
        resp = client.post("/api/llm-config/models", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["error"]


def test_list_models_no_base_url_no_env_fallback():
    """决策 A：baseUrl 为空时即使环境变量 LLM_BASE_URL 有值也不回退。

    模型发现是「探测用户指定端点」的工具，回退环境端点会悄悄返回该端点模型，
    违背用户直觉。分析链路 call_llm 的回退不受影响。
    """
    with (
        patch.dict("os.environ", {"LLM_BASE_URL": "https://env-fallback.example.com/v1"}),
        TestClient(app) as client,
    ):
        resp = client.post("/api/llm-config/models", json={"baseUrl": "", "apiKey": "sk-x"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert "请先配置" in data["error"]


def test_list_models_connection_error():
    """baseUrl 不可达时返回空列表 + 错误提示（非阻塞降级）。"""
    with patch("httpx.AsyncClient") as mock_factory:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=ConnectionError("连接被拒绝"))
        mock_factory.return_value = mock_client

        with TestClient(app) as client:
            resp = client.post(
                "/api/llm-config/models",
                json={"baseUrl": "https://invalid.example.com/v1"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert "ConnectionError" in data["error"]


# ── 4.12: POST /api/llm-config/test ──


def _mock_probe_report(*, error=None, **cap):
    from finance_agent.llm.probes import ProbeReport

    defaults = {
        "non_stream": True,
        "stream": True,
        "tool_call": True,
        "tool_followup": True,
        "json_output": True,
        "latency_ms": 50,
        "warnings": [],
    }
    defaults.update(cap)
    return ProbeReport(error=error, **defaults)


def test_test_llm_config_success():
    """POST /api/llm-config/test 升级后返回 capability 矩阵。"""
    with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
        mock_probe.return_value = _mock_probe_report()
        with TestClient(app) as client:
            resp = client.post(
                "/api/llm-config/test",
                json={
                    "model": "deepseek/deepseek-chat",
                    "baseUrl": "https://api.deepseek.com/v1",
                    "apiKey": "sk-test",
                    "thinking": "enabled",
                },
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["capability"]["tool_call"] is True
    assert data["capability"]["json_output"] is True
    # max_output：静态能力推断随 probe 响应返回，供前端「最大输出」只读展示
    assert data["capability"]["max_output"] == 8192
    assert data["model"] == "deepseek/deepseek-chat"
    mock_probe.assert_called_once()
    kw = mock_probe.call_args[1]
    assert kw["model"] == "deepseek/deepseek-chat"


def test_test_llm_config_chat_only_capability():
    """能聊天但无工具能力 → success 但 capability.tool_call=false。"""
    with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
        mock_probe.return_value = _mock_probe_report(
            tool_call=False, tool_followup=False, warnings=["tool_choice_required_unsupported"]
        )
        with TestClient(app) as client:
            data = client.post("/api/llm-config/test", json={"apiKey": "sk-test"}).json()
    assert data["success"] is True
    assert data["capability"]["tool_call"] is False
    assert "tool_choice_required_unsupported" in data["warnings"]


def test_test_llm_config_error_report():
    """probe 内部错误 → success=false + error 字段。"""
    with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
        mock_probe.return_value = _mock_probe_report(
            error="openai.AuthenticationError: Invalid API key", non_stream=False
        )
        with TestClient(app) as client:
            data = client.post("/api/llm-config/test", json={"apiKey": "sk-invalid"}).json()
    assert data["success"] is False
    assert "AuthenticationError" in data["error"]


def test_test_llm_config_unknown_prefix_structured_error():
    """未知 provider 前缀 → 结构化失败（errorType=model_prefix_invalid），非 500 裸栈。"""
    with TestClient(app) as client:
        resp = client.post(
            "/api/llm-config/test",
            json={
                "model": "kimi/kimi-k2-0905-preview",
                "baseUrl": "https://api.kimi.ai/v1",
                "apiKey": "sk-test",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["errorType"] == "model_prefix_invalid"
    assert "openai/" in data["error"]
    assert "kimi" in data["error"]


# ── 4.9: AnalyzeRequest / ChatRequest 携带 llm_config 不报错 ──


def test_analyze_request_accepts_llm_config():
    """AnalyzeRequest 接受 llm_config 字段且默认为 None。"""
    req = AnalyzeRequest(query="测试")
    assert req.llm_config is None

    req_with_config = AnalyzeRequest.model_validate(
        {
            "query": "茅台",
            "llm_config": {
                "model": "deepseek/deepseek-chat",
                "apiKey": "sk-test",
            },
        }
    )
    assert req_with_config.llm_config is not None
    assert req_with_config.llm_config.model == "deepseek/deepseek-chat"
    assert req_with_config.llm_config.apiKey == "sk-test"


def test_chat_request_accepts_llm_config():
    """ChatRequest 接受 llm_config 字段且默认为 None。"""
    req = ChatRequest(message="测试")
    assert req.llm_config is None

    req_with_config = ChatRequest.model_validate(
        {
            "message": "你好",
            "llm_config": {"model": "openai/gpt-4o"},
        }
    )
    assert req_with_config.llm_config is not None
    assert req_with_config.llm_config.model == "openai/gpt-4o"


def test_analyze_request_without_llm_config_unchanged():
    """不带 llm_config 的 AnalyzeRequest 行为与原有完全一致。"""
    req = AnalyzeRequest(query="茅台", stock_code="600519")
    assert req.query == "茅台"
    assert req.stock_code == "600519"
    assert req.llm_config is None


# ── 辅助：测试用异常类 ──


class AuthenticationError(Exception):
    pass


class NotFoundError(Exception):
    pass


# ── probe 成功写缓存（llm-capability-probe delta Task 2）──


def test_test_llm_config_success_writes_probe_cache():
    """POST /api/llm-config/test 成功探测后写入 probe 缓存（键同 resolver）。"""
    from finance_agent.llm.probe_cache import (
        _reset_probe_cache_for_tests,
        cache_key,
        get_probe_cache,
    )

    _reset_probe_cache_for_tests()
    get_probe_cache().clear()
    try:
        with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
            mock_probe.return_value = _mock_probe_report()
            with TestClient(app) as client:
                resp = client.post(
                    "/api/llm-config/test",
                    json={
                        "model": "deepseek/deepseek-chat",
                        "baseUrl": "https://api.deepseek.com/v1",
                        "apiKey": "sk-cache",
                    },
                )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        key = cache_key(
            model="deepseek/deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-cache",
        )
        cached = get_probe_cache().get(key)
        assert cached is not None
        assert cached.tool_call is True
    finally:
        _reset_probe_cache_for_tests()
        get_probe_cache().clear()


class TestBareModelPrefixNormalization:
    """裸模型名 + 自定义 baseUrl → 探测前强制 openai/ 前缀（§6；终审 #77 缓存键失配同修）。"""

    def test_bare_model_with_base_url_gets_openai_prefix(self):
        from finance_agent.llm.probe_cache import _reset_probe_cache_for_tests

        _reset_probe_cache_for_tests()
        try:
            with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
                mock_probe.return_value = _mock_probe_report()
                with TestClient(app) as client:
                    resp = client.post(
                        "/api/llm-config/test",
                        json={"model": "glm-5.3", "baseUrl": "https://ark/v1", "apiKey": "k"},
                    )
            assert resp.status_code == 200
            assert mock_probe.call_args[1]["model"] == "openai/glm-5.3"
        finally:
            _reset_probe_cache_for_tests()

    def test_bare_model_without_base_url_untouched(self):
        with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
            mock_probe.return_value = _mock_probe_report()
            with TestClient(app) as client:
                client.post("/api/llm-config/test", json={"model": "llama3", "apiKey": "k"})
        assert mock_probe.call_args[1]["model"] == "llama3"

    def test_cache_key_uses_normalized_model(self):
        from finance_agent.llm.probe_cache import (
            _reset_probe_cache_for_tests,
            cache_key,
            get_probe_cache,
        )

        _reset_probe_cache_for_tests()
        try:
            with patch("finance_agent.llm.probes.run_live_probes") as mock_probe:
                mock_probe.return_value = _mock_probe_report()
                with TestClient(app) as client:
                    client.post(
                        "/api/llm-config/test",
                        json={"model": "glm-5.3", "baseUrl": "https://ark/v1", "apiKey": "k"},
                    )
            key = cache_key(model="openai/glm-5.3", base_url="https://ark/v1", api_key="k")
            assert get_probe_cache().get(key) is not None
        finally:
            _reset_probe_cache_for_tests()
