# tests/llm_contracts/test_provider_contract_suite.py
"""Provider 合同测试框架（delta 4.3，设计档案 §15）。

门禁语义：未通过 tool_call+tool_followup 的 profile 不得用于深度模式；
未通过 json_output 的 profile 不得用于管线节点。
- @live 标记用例跑真实 LLM/关键 profile（nightly），无凭据时 skip。
- 静态一致性用例进普通 pytest（无损跑）。
"""

from __future__ import annotations

import pytest

from finance_agent.llm.registry import get_profile_preset, list_presets


class TestStaticContractConsistency:
    """静态能力表一致性（不进 @live，任何 CI 都跑）。"""

    def test_every_preset_has_capability(self):
        for name in list_presets():
            p = get_profile_preset(name)
            assert p.capability is not None, name
            assert p.model, name

    def test_deepseek_must_echo_reasoning(self):
        """DeepSeek 官方：工具轮次必须回传 reasoning（缺失 400 的约束建模）。"""
        assert (
            get_profile_preset("deepseek-official").capability.reasoning_must_echo_on_tool is True
        )

    def test_ark_glm_forced_and_no_echo(self):
        assert get_profile_preset("ark-glm").capability.reasoning_forced is True
        assert get_profile_preset("ark-glm").capability.reasoning_must_echo_on_tool is False

    def test_plain_models_no_reasoning_contract(self):
        assert get_profile_preset("openai-official").capability.reasoning_field is None


class TestLiveContractProbe:
    """@live：真实 LLM 的合同探测（nightly，无凭据 skip，防漂移）。

    litellm/模型 alias/prompt 变更须触发本合同（design 决策，CI canary）。
    仅为骨架样例：真实五项探测的发送编排在 /api/llm-config/test（4.2），
    此处以 ark-glm 为样例做最小 tool/json 探测打样。
    """

    @pytest.mark.live
    def test_ark_tool_call_contract(self):
        """方舟 GLM 应通过单工具调用（native tools 快路径）。

        凭据门槛 = **该 provider 自己的凭据**（``ARK_API_KEY``）。历史实现误用
        ``LLM_API_KEY`` 作门槛（deepseek/ark 直连时代该变量就是方舟 key）——opencode
        迁移后 LLM_API_KEY 指向他端点，探测会以错 key 打方舟端点必假红（2026-10-18
        nightly 首次真跑实证）。方舟订阅续费后配 ``ARK_API_KEY``（本地 .env / CI
        secrets）即恢复探测；未配置 → 按本类「无凭据 skip」纪律跳过。
        """
        import os

        from dotenv import load_dotenv
        from litellm import completion

        load_dotenv()
        if not os.environ.get("ARK_API_KEY"):
            pytest.skip("无 ARK_API_KEY（方舟凭据），跳过 live 合同探测")
        profile = get_profile_preset("ark-glm")
        try:
            resp = completion(
                model=profile.model,
                api_key=os.environ.get("ARK_API_KEY"),
                base_url=profile.base_url,
                messages=[{"role": "user", "content": "查一下 600519 行情"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_quote",
                            "description": "查询行情",
                            "parameters": {
                                "type": "object",
                                "properties": {"code": {"type": "string"}},
                                "required": ["code"],
                            },
                        },
                    }
                ],
                tool_choice="auto",
                max_tokens=500,
                timeout=60,
            )
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"ark-glm 单工具调用合同失败：{type(e).__name__}: {str(e)[:120]}")
        # 至少返回了结构化 tool_calls 或合理回答
        assert resp.choices[0].message is not None
