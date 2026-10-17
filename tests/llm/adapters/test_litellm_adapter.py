# tests/llm/adapters/test_litellm_adapter.py
"""litellm adapter 运行时防护收口测试（Task 1.4）。

设计决策 4：库级平台 bug 防护（incident 016 死锁开关、请求超时、
litellm-langfuse 兼容补丁）统一在 adapter 初始化，业务模块不各自设置。
"""

from __future__ import annotations

import threading

from finance_agent.llm.adapters.litellm_adapter import ensure_litellm_runtime


class TestEnsureLitellmRuntime:
    def test_sets_deadlock_guard_flag(self):
        """incident 016：流式 logging 线程死锁开关必须生效。"""
        ensure_litellm_runtime()
        import litellm

        assert litellm.disable_streaming_logging is True

    def test_idempotent(self):
        """幂等初始化：多次调用无副作用、不重复打补丁。"""
        ensure_litellm_runtime()
        ensure_litellm_runtime()
        import litellm

        assert litellm.disable_streaming_logging is True

    def test_thread_safe_concurrent_init(self):
        """并发首次调用安全（管线多入口同时初始化场景）。"""
        results: list[None] = []
        barrier = threading.Barrier(4)

        def call():
            barrier.wait()
            results.append(ensure_litellm_runtime())

        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(results) == 4

    def test_langfuse_compat_patch_applied(self):
        """litellm-langfuse 深度不兼容补丁：LangFuseLogger 方法为空操作。"""
        ensure_litellm_runtime()
        from litellm.integrations.langfuse.langfuse import LangFuseLogger

        assert LangFuseLogger.__init__ is not None


def test_raw_stream_sets_include_usage(monkeypatch):
    """流式调用必须传 stream_options.include_usage，否则 OpenAI 兼容端点(glm 等)
    流式响应不含 usage → Langfuse generation 无 token 用量 → cost 无法计算。"""
    import litellm

    from finance_agent.llm.adapters import litellm_adapter

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        from unittest.mock import MagicMock

        return MagicMock()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    litellm_adapter.raw_stream(
        model="openai/glm-5.3",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert captured.get("stream") is True
    assert captured.get("stream_options") == {"include_usage": True}


def test_raw_completion_opencode_base_injects_session_header(monkeypatch):
    """incident 021：opencode zen/go 网关 2026-09 起要求 x-opencode-session 头，
    缺头直接 400（离线 judge 全挂）。api_base 指向 opencode.ai 时必须注入该头。"""
    import litellm

    from finance_agent.llm.adapters import litellm_adapter

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        from unittest.mock import MagicMock

        return MagicMock()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    litellm_adapter.raw_completion(
        model="openai/deepseek-v4-flash",
        messages=[{"role": "user", "content": "hi"}],
        api_base="https://opencode.ai/zen/go/v1",
        api_key="sk-test",
    )
    headers = captured.get("extra_headers") or {}
    assert headers.get("x-opencode-session")
    assert isinstance(headers["x-opencode-session"], str) and headers["x-opencode-session"]


def test_raw_completion_non_opencode_base_no_session_header(monkeypatch):
    """非 opencode 端点（如方舟 glm）不得注入会话头——它不需要，且避免行为漂移。"""
    import litellm

    from finance_agent.llm.adapters import litellm_adapter

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        from unittest.mock import MagicMock

        return MagicMock()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    litellm_adapter.raw_completion(
        model="openai/glm-5.3",
        messages=[{"role": "user", "content": "hi"}],
        api_base="https://ark.cn-beijing.volces.com/api/plan/v3",
        api_key="sk-test",
    )
    headers = captured.get("extra_headers") or {}
    assert "x-opencode-session" not in headers


def test_raw_stream_opencode_base_injects_session_header(monkeypatch):
    """流式同规约：opencode 端点流式也带会话头（judge 走非流式，防御未来路径）。"""
    import litellm

    from finance_agent.llm.adapters import litellm_adapter

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        from unittest.mock import MagicMock

        return MagicMock()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    litellm_adapter.raw_stream(
        model="openai/deepseek-v4-flash",
        messages=[{"role": "user", "content": "hi"}],
        api_base="https://opencode.ai/zen/go/v1",
    )
    headers = captured.get("extra_headers") or {}
    assert headers.get("x-opencode-session")


def test_raw_completion_opencode_base_url_alias_injects_session_header(monkeypatch):
    """base_url= 是 litellm 的 api_base 别名，能力探测（probes/设置页测试）
    用该形态调用 opencode 端点——同样必须注入会话头，否则探测直接 400。"""
    import litellm

    from finance_agent.llm.adapters import litellm_adapter

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        from unittest.mock import MagicMock

        return MagicMock()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    litellm_adapter.raw_completion(
        model="openai/deepseek-v4.1-flash",
        messages=[{"role": "user", "content": "hi"}],
        base_url="https://opencode.ai/zen/go/v1",
        api_key="sk-test",
    )
    headers = captured.get("extra_headers") or {}
    assert headers.get("x-opencode-session")


def test_raw_stream_opencode_base_url_alias_injects_session_header(monkeypatch):
    """流式同规约：base_url= 别名形态同样触发会话头注入。"""
    import litellm

    from finance_agent.llm.adapters import litellm_adapter

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        from unittest.mock import MagicMock

        return MagicMock()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    litellm_adapter.raw_stream(
        model="openai/deepseek-v4.1-flash",
        messages=[{"role": "user", "content": "hi"}],
        base_url="https://opencode.ai/zen/go/v1",
    )
    headers = captured.get("extra_headers") or {}
    assert headers.get("x-opencode-session")


# ── temperature 拒绝自动降级（update-temperature-degrade-fallback）──


class TestTemperatureDegrade:
    """思考型模型端点仅允许 temperature=1：temperature 相关拒绝剔除后重试一次。"""

    def _temp_reject(self):
        class FakeBadRequestError(Exception):
            pass

        return FakeBadRequestError(
            "Error code: 400 - invalid temperature: only 1 is allowed for this model"
        )

    def test_raw_completion_degrades_and_retries(self, monkeypatch):
        import litellm

        from finance_agent.llm.adapters import litellm_adapter

        calls: list[dict] = []

        def fake_completion(**kwargs):
            calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise self._temp_reject()
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(litellm, "completion", fake_completion)
        litellm_adapter.raw_completion(
            model="openai/kimi-k2-thinking",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.3,
        )
        assert len(calls) == 2
        assert "temperature" not in calls[1]

    def test_raw_completion_non_temperature_error_no_retry(self, monkeypatch):
        import litellm

        from finance_agent.llm.adapters import litellm_adapter

        calls: list[dict] = []

        def fake_completion(**kwargs):
            calls.append(dict(kwargs))
            raise RuntimeError("Error code: 401 - invalid api key")

        monkeypatch.setattr(litellm, "completion", fake_completion)
        import pytest

        with pytest.raises(RuntimeError, match="invalid api key"):
            litellm_adapter.raw_completion(
                model="openai/kimi-k2-thinking",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.3,
            )
        assert len(calls) == 1

    def test_raw_completion_degrade_still_fails_raises_original(self, monkeypatch):
        import litellm

        from finance_agent.llm.adapters import litellm_adapter

        calls: list[dict] = []

        def fake_completion(**kwargs):
            calls.append(dict(kwargs))
            raise RuntimeError("Error code: 400 - invalid temperature: only 1")

        monkeypatch.setattr(litellm, "completion", fake_completion)
        import pytest

        with pytest.raises(RuntimeError, match="invalid temperature"):
            litellm_adapter.raw_completion(
                model="openai/x",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.3,
            )
        assert len(calls) == 2

    def test_raw_completion_without_temperature_untouched(self, monkeypatch):
        import litellm

        from finance_agent.llm.adapters import litellm_adapter

        calls: list[dict] = []

        def fake_completion(**kwargs):
            calls.append(dict(kwargs))
            raise RuntimeError("some temperature unrelated")

        monkeypatch.setattr(litellm, "completion", fake_completion)
        import pytest

        with pytest.raises(RuntimeError):
            litellm_adapter.raw_completion(
                model="openai/x", messages=[{"role": "user", "content": "hi"}]
            )
        assert len(calls) == 1

    def test_raw_stream_degrades_and_retries(self, monkeypatch):
        import litellm

        from finance_agent.llm.adapters import litellm_adapter

        calls: list[dict] = []

        def fake_completion(**kwargs):
            calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise self._temp_reject()
            return iter(["chunk"])

        monkeypatch.setattr(litellm, "completion", fake_completion)
        out = litellm_adapter.raw_stream(
            model="openai/kimi-k2-thinking",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.3,
        )
        assert next(iter(out)) == "chunk"
        assert len(calls) == 2
        assert "temperature" not in calls[1]

    def test_raw_acompletion_degrades_and_retries(self, monkeypatch):
        import litellm

        from finance_agent.llm.adapters import litellm_adapter

        calls: list[dict] = []

        async def fake_acompletion(**kwargs):
            calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise self._temp_reject()
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
        import asyncio

        asyncio.run(
            litellm_adapter.raw_acompletion(
                model="openai/kimi-k2-thinking",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.3,
            )
        )
        assert len(calls) == 2
        assert "temperature" not in calls[1]
