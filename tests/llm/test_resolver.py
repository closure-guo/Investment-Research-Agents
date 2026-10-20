# tests/llm/test_resolver.py
"""ProfileResolver 唯一配置解析入口测试（Task 1.3）。

优先级：请求级 llm_config → 激活 profile → 环境变量 → registry 默认。
实战回归场景：judge 半套漂移（PR #74 修复的回退打错网关）、
「先 import 后 load_dotenv」时序、自定义端点前缀。
"""

from __future__ import annotations

import pytest

from finance_agent.llm.probe_cache import (
    _reset_probe_cache_for_tests,
    cache_key,
    get_probe_cache,
)
from finance_agent.llm.probes import ProbeReport
from finance_agent.llm.resolver import (
    IncompleteLLMConfigError,
    UnknownProviderPrefixError,
    resolve_profile,
)


@pytest.fixture(autouse=True)
def _isolated_probe_cache():
    """每个测试独立的 probe 缓存单例（隔离缓存命中/未命中状态）。"""
    _reset_probe_cache_for_tests()
    get_probe_cache().clear()
    yield
    _reset_probe_cache_for_tests()
    get_probe_cache().clear()


def _report(**overrides) -> ProbeReport:
    defaults = {
        "non_stream": True,
        "stream": True,
        "tool_call": True,
        "tool_followup": True,
        "json_output": True,
        "latency_ms": 100,
        "warnings": [],
    }
    defaults.update(overrides)
    return ProbeReport(**defaults)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离环境变量，测试不依赖 shell/.env。"""
    for var in (
        "LLM_MODEL",
        "LLM_QUICK_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "JUDGE_MODEL",
        "JUDGE_BASE_URL",
        "JUDGE_API_KEY",
        "DAILY_BRIEF_MODEL",
        "DAILY_BRIEF_BASE_URL",
        "DAILY_BRIEF_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


class TestPriorityOrder:
    def test_env_vars_build_profile(self):
        """环境变量齐全 → 构造 openai-compatible profile（openai/ 前缀补齐）。"""
        import os

        monkey: pytest.MonkeyPatch = pytest.MonkeyPatch()
        monkey.setenv("LLM_MODEL", "glm-5.2")  # 无前缀 + base_url → 强制 openai/
        monkey.setenv("LLM_BASE_URL", "https://ark.example/api/v3")
        monkey.setenv("LLM_API_KEY", "sk-test")
        profile = resolve_profile(purpose="deep", _env=os.environ)
        assert profile.model == "openai/glm-5.2"
        assert profile.base_url == "https://ark.example/api/v3"
        monkey.undo()

    def test_named_preset(self):
        profile = resolve_profile(purpose="judge", preset="deepseek-official", _env={})
        assert profile.provider == "deepseek"
        assert profile.capability.reasoning_must_echo_on_tool is True

    def test_request_level_overrides_env(self):
        """请求级 llm_config 完整时整体覆盖环境变量（原子切换）。"""
        import os

        monkey = pytest.MonkeyPatch()
        monkey.setenv("LLM_MODEL", "other-model")
        monkey.setenv("LLM_BASE_URL", "https://other.example/v1")
        monkey.setenv("LLM_API_KEY", "sk-env")
        llm_config = {
            "model": "glm-5.2",
            "baseUrl": "https://ark.example/api/v3",
            "apiKey": "sk-req",
        }
        profile = resolve_profile(purpose="deep", llm_config=llm_config, _env=os.environ)
        assert profile.model == "openai/glm-5.2"
        assert profile.api_key == "sk-req"
        monkey.undo()


class TestNoHalfConfigDrift:
    def test_partial_request_config_raises(self):
        """请求级只有 model 无端点 → 显式报错，禁止与环境变量混搭。"""
        with pytest.raises(IncompleteLLMConfigError):
            resolve_profile(
                purpose="deep",
                llm_config={"model": "glm-5.2"},  # 缺 baseUrl
                _env={"LLM_API_KEY": "sk-env", "LLM_BASE_URL": "https://env.example/v1"},
            )

    def test_keyless_request_config_resolves(self):
        """请求级 model+baseUrl 无 key（keyless 本地端点，如 Ollama preset）→ api_key=None。"""
        profile = resolve_profile(
            purpose="deep",
            llm_config={"model": "openai/llama3", "baseUrl": "http://localhost:11434/v1"},
            _env={},
        )
        assert profile.model == "openai/llama3"
        assert profile.base_url == "http://localhost:11434/v1"
        assert profile.api_key is None

    def test_keyless_request_config_falls_back_to_env_key(self):
        """请求级缺 key 时显式 env 回退：LLM_API_KEY 优先，其次 DEEPSEEK_API_KEY。"""
        profile = resolve_profile(
            purpose="deep",
            llm_config={"model": "glm-5.2", "baseUrl": "https://ark.example/v3"},
            _env={"LLM_API_KEY": "sk-env", "DEEPSEEK_API_KEY": "sk-ds"},
        )
        assert profile.api_key == "sk-env"
        profile2 = resolve_profile(
            purpose="deep",
            llm_config={"model": "glm-5.2", "baseUrl": "https://ark.example/v3"},
            _env={"DEEPSEEK_API_KEY": "sk-ds"},
        )
        assert profile2.api_key == "sk-ds"

    def test_env_keyless_with_base_url_resolves(self):
        """环境变量 model+base_url 无 key → keyless 端点，api_key=None。"""
        profile = resolve_profile(
            purpose="deep",
            _env={"LLM_MODEL": "llama3", "LLM_BASE_URL": "http://localhost:11434/v1"},
        )
        assert profile.model == "openai/llama3"
        assert profile.api_key is None

    def test_env_key_without_base_url_still_raises(self):
        """环境变量 model+key 无端点 → 仍是半套配置，显式报错。"""
        with pytest.raises(IncompleteLLMConfigError):
            resolve_profile(
                purpose="deep",
                _env={"LLM_MODEL": "glm-5.2", "LLM_API_KEY": "k"},
            )

    def test_judge_purpose_no_silent_fallback(self):
        """judge purpose 显式配置优先；环境缺 judge 配置时才用独立默认而非 LLM_*。

        实战回归：换 provider 后 JUDGE_* 回退 LLM_* 打错网关（28 项全败）。
        resolver 语义：judge 未配置时不得静默借用 LLM_BASE_URL。
        """
        with pytest.raises(IncompleteLLMConfigError):
            resolve_profile(
                purpose="judge",
                _env={"LLM_BASE_URL": "https://ark.example/v1", "LLM_API_KEY": "sk-llm"},
            )


class TestProviderPrefix:
    def test_unknown_prefix_rejected(self):
        """model 带未知 provider 前缀 → 显式报错（不从域名猜协议）。"""
        with pytest.raises(UnknownProviderPrefixError):
            resolve_profile(
                purpose="deep",
                _env={
                    "LLM_MODEL": "opencode/some-model",
                    "LLM_BASE_URL": "https://gw.example/v1",
                    "LLM_API_KEY": "k",
                },
            )

    def test_env_read_at_call_time(self):
        """配置必须调用时读环境（python -m 入口 import 早于 load_dotenv 的时序回归）。"""
        import os

        profile1 = resolve_profile(
            purpose="deep",
            _env={"LLM_MODEL": "glm-5.2", "LLM_BASE_URL": "https://a/v1", "LLM_API_KEY": "k1"},
        )
        assert profile1.model == "openai/glm-5.2"
        profile2 = resolve_profile(
            purpose="deep",
            _env={"LLM_MODEL": "glm-5.2", "LLM_BASE_URL": "https://b/v1", "LLM_API_KEY": "k1"},
        )
        assert profile2.base_url == "https://b/v1"
        del os


class TestApiFormPrefix:
    """add-llm-api-form：请求级 apiForm 驱动裸模型名前缀推导 + Profile 携带 api_form。"""

    def _resolve(self, api_form):
        return resolve_profile(
            purpose="deep",
            llm_config={
                "model": "gpt-4o",
                "baseUrl": "https://api.example.com/v1",
                "apiKey": "sk-x",
                "apiForm": api_form,
            },
            _env={},
        )

    def test_chat_completion_derives_openai_prefix(self):
        profile = self._resolve("chat_completion")
        assert profile.model == "openai/gpt-4o"
        assert profile.api_form == "chat_completion"

    def test_responses_derives_openai_prefix(self):
        profile = self._resolve("responses")
        assert profile.model == "openai/gpt-4o"
        assert profile.api_form == "responses"

    def test_messages_derives_anthropic_prefix(self):
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "claude-sonnet-4-20250514",
                "baseUrl": "https://api.anthropic.com/v1",
                "apiKey": "sk-x",
                "apiForm": "messages",
            },
            _env={},
        )
        assert profile.model == "anthropic/claude-sonnet-4-20250514"
        assert profile.api_form == "messages"

    def test_already_prefixed_model_kept_as_is(self):
        """模型名已含 / 前缀时原样使用，不做推导。"""
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "deepseek/deepseek-chat",
                "baseUrl": "https://api.deepseek.com/v1",
                "apiKey": "sk-x",
                "apiForm": "chat_completion",
            },
            _env={},
        )
        assert profile.model == "deepseek/deepseek-chat"

    def test_no_api_form_keeps_existing_openai_fallback(self):
        """未设 apiForm + 有端点 → 维持现状 openai/ 前缀。"""
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "gpt-4o",
                "baseUrl": "https://api.example.com/v1",
                "apiKey": "sk-x",
            },
            _env={},
        )
        assert profile.model == "openai/gpt-4o"
        assert profile.api_form is None

    def test_invalid_api_form_rejected(self):
        """非法 apiForm 显式报错，不静默忽略。"""
        with pytest.raises(IncompleteLLMConfigError):
            self._resolve("bogus")

    """llm-capability-probe delta：resolver 合并 probe 缓存事实。"""

    ENV_DEEPSEEK = {
        "LLM_MODEL": "deepseek/deepseek-chat",
        "LLM_BASE_URL": "https://api.deepseek.com/v1",
        "LLM_API_KEY": "sk-merge",
    }

    def test_cache_hit_overrides_capability_and_sets_warnings(self):
        """缓存命中（tool_call=false）→ tools=none + warning，其余字段不动。"""
        get_probe_cache().put(
            cache_key(
                model="deepseek/deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-merge",
            ),
            _report(tool_call=False, tool_followup=False),
        )
        profile = resolve_profile(purpose="deep", _env=self.ENV_DEEPSEEK)
        assert profile.capability.tools == "none"
        assert profile.probe_required is False
        assert any("tools" in w for w in profile.probe_warnings)
        # probe 不改连接与 provider 配置
        assert profile.api_key == "sk-merge"
        assert profile.model == "deepseek/deepseek-chat"

    def test_cache_miss_sets_probe_required(self):
        """缓存未命中 → probe_required=True，capability 保持静态表。"""
        profile = resolve_profile(purpose="deep", _env=self.ENV_DEEPSEEK)
        assert profile.probe_required is True
        assert profile.probe_warnings == ()
        assert profile.capability.tools != "none"  # deepseek 静态表支持工具

    def test_merge_preserves_max_output(self):
        """合并不回退 capability 特化 max_output（ark-glm 65536）。"""
        env = {
            "LLM_MODEL": "glm-5.2",
            "LLM_BASE_URL": "https://ark.example/api/v3",
            "LLM_API_KEY": "sk-glm",
        }
        get_probe_cache().put(
            cache_key(
                model="openai/glm-5.2",
                base_url="https://ark.example/api/v3",
                api_key="sk-glm",
            ),
            _report(),
        )
        profile = resolve_profile(purpose="deep", _env=env)
        assert profile.model == "openai/glm-5.2"
        assert profile.capability.max_output == 65536
        assert profile.probe_required is False


class TestContextLengthOverride:
    """请求级 contextLength / LLM_MAX_CONTEXT 覆盖 capability.max_context（add-context-length-config）。"""

    @staticmethod
    def _request(**kw) -> dict:
        cfg = {
            "model": "openai/gpt-4o",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-test",
            "apiForm": "chat_completion",
        }
        cfg.update(kw)
        return cfg

    def test_request_override_max_context(self):
        """请求级 contextLength 覆盖 capability.max_context。"""
        profile = resolve_profile(purpose="deep", llm_config=self._request(contextLength=200000))
        assert profile.capability.max_context == 200000
        # 其余能力字段不变
        assert profile.capability.tools != "none"

    def test_env_override_when_no_request_value(self):
        """无请求级值 + LLM_MAX_CONTEXT → env 覆盖默认。"""
        env = {"LLM_MAX_CONTEXT": "300000"}
        profile = resolve_profile(purpose="deep", llm_config=self._request(), _env=env)
        assert profile.capability.max_context == 300000

    def test_request_takes_priority_over_env(self):
        """请求级 contextLength 优先于 env LLM_MAX_CONTEXT。"""
        env = {"LLM_MAX_CONTEXT": "300000"}
        profile = resolve_profile(
            purpose="deep", llm_config=self._request(contextLength=200000), _env=env
        )
        assert profile.capability.max_context == 200000

    def test_invalid_request_value_raises(self):
        """contextLength 0/负数 → 显式配置错误，不静默忽略。"""
        for bad in (0, -5):
            with pytest.raises(IncompleteLLMConfigError):
                resolve_profile(purpose="deep", llm_config=self._request(contextLength=bad))

    def test_invalid_env_value_raises(self):
        """LLM_MAX_CONTEXT 非正整数 → 显式配置错误。"""
        with pytest.raises(IncompleteLLMConfigError):
            resolve_profile(
                purpose="deep", llm_config=self._request(), _env={"LLM_MAX_CONTEXT": "abc"}
            )

    def test_empty_follows_registry_default(self):
        """请求与 env 均未配置 → registry 静态 max_context（openai-compatible 128000）。"""
        # _env={} 隔离真实环境（宿主 .env 设 LLM_MAX_CONTEXT 时本测试不得被泄漏污染）
        profile = resolve_profile(purpose="deep", llm_config=self._request(), _env={})
        assert profile.capability.max_context == 128000


class TestRequestCapabilityInference:
    """#302：请求分支能力按模型名推断（与 env 分支同一实现）。

    能力语义 SHALL 随模型走而非随配置来源漂移（llm-capability-probe 哲学：
    能力=模型/端点事实）——同一 glm 模型走 env 解析能 tool_choice=required、
    走请求级 llm_config 被守卫拒，是能力推断的双标。
    """

    def test_request_glm_prefixed_gets_required(self):
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "openai/glm-5.3",
                "baseUrl": "https://ark.example/api/v3",
                "apiKey": "sk-req",
            },
            _env={},
        )
        assert profile.capability.tool_choice_required is True

    def test_request_glm_unprefixed_also_inferred(self):
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "glm-5.2",
                "baseUrl": "https://ark.example/api/v3",
                "apiKey": "sk-req",
            },
            _env={},
        )
        assert profile.capability.tool_choice_required is True

    def test_request_deepseek_keeps_generic(self):
        """非 glm 模型的工具档维持通用（不把 required/tools 能力扩给自定义端点）。"""
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "deepseek/deepseek-chat",
                "baseUrl": "https://api.deepseek.example/v1",
                "apiKey": "sk-req",
            },
            _env={},
        )
        assert profile.capability.tool_choice_required is False


class TestDeepSeekThinkingCapability:
    """DeepSeek 思考模型能力随模型走，不随端点前缀漂移。

    生产配置：``LLM_MODEL=openai/deepseek-v4.1-flash`` +
    ``LLM_BASE_URL=https://opencode.ai/zen/go/v1``。旧实现只对 glm 做
    模型特化，该组合落 openai-compatible 中性档
    （``reasoning_must_echo_on_tool=False``），gateway 清洗会剥掉工具轮的
    ``reasoning_content``，DeepSeek 端点返回 400："The reasoning_content in
    the thinking mode must be passed back to the API"。
    """

    ENV = {
        "LLM_MODEL": "openai/deepseek-v4.1-flash",
        "LLM_BASE_URL": "https://opencode.ai/zen/go/v1",
        "LLM_API_KEY": "oc_sk_test",
    }

    def test_env_openai_prefixed_deepseek_echoes_reasoning(self):
        profile = resolve_profile(purpose="deep", _env=self.ENV)
        assert profile.capability.reasoning_field == "reasoning_content"
        assert profile.capability.reasoning_must_echo_on_tool is True

    def test_echo_only_does_not_expand_other_capabilities(self):
        """opencode 端点实测不支持 tool_choice=required（#394），只扩 reasoning 契约。"""
        profile = resolve_profile(purpose="deep", _env=self.ENV)
        assert profile.capability.tools == "single"
        assert profile.capability.tool_choice_required is False

    def test_request_openai_prefixed_deepseek_echoes_reasoning(self):
        profile = resolve_profile(
            purpose="deep",
            llm_config={
                "model": "openai/deepseek-v4.1-flash",
                "baseUrl": "https://opencode.ai/zen/go/v1",
                "apiKey": "oc_sk_test",
            },
            _env={},
        )
        assert profile.capability.reasoning_must_echo_on_tool is True

    def test_tool_turn_survives_message_sanitize(self):
        """复现 400 前置条件：解析后的 capability 清洗工具轮消息必须保留 reasoning。"""
        from finance_agent.llm.adapters.litellm_adapter import sanitize_messages_for_profile

        profile = resolve_profile(purpose="deep", _env=self.ENV)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
                "reasoning_content": "思考",
            }
        ]
        out = sanitize_messages_for_profile(messages, profile.capability)
        assert out[0]["reasoning_content"] == "思考"


class TestDailyBriefPurpose:
    """add-auto-pool-analysis-daily-brief：日报 purpose 的逐字段环境覆盖。"""

    ENV = {
        "LLM_MODEL": "openai/deepseek-v4.1-flash",
        "LLM_BASE_URL": "https://opencode.ai/zen/go/v1",
        "LLM_API_KEY": "oc_sk_test",
    }

    def test_inherits_llm_env_when_no_override(self):
        profile = resolve_profile(purpose="daily_brief", _env=self.ENV)
        assert profile.provider == "openai"
        assert profile.model == "openai/deepseek-v4.1-flash"
        assert profile.base_url == "https://opencode.ai/zen/go/v1"
        assert profile.api_key == "oc_sk_test"

    def test_model_override_keeps_llm_endpoint_and_key(self):
        env = {**self.ENV, "DAILY_BRIEF_MODEL": "openai/another-model"}
        profile = resolve_profile(purpose="daily_brief", _env=env)
        assert profile.model == "openai/another-model"
        assert profile.base_url == "https://opencode.ai/zen/go/v1"
        assert profile.api_key == "oc_sk_test"

    def test_base_url_and_key_override_per_field(self):
        env = {
            **self.ENV,
            "DAILY_BRIEF_BASE_URL": "https://brief.example/v1",
            "DAILY_BRIEF_API_KEY": "brief-key",
        }
        profile = resolve_profile(purpose="daily_brief", _env=env)
        assert profile.model == "openai/deepseek-v4.1-flash"
        assert profile.base_url == "https://brief.example/v1"
        assert profile.api_key == "brief-key"
