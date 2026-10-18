"""``check_secrets.py`` 密钥扫描器单测（防泄露 CI 门禁的守门测试）。

钉三件事：
1. **新 opencode key 格式（``oc_sk_`` 前缀）必须被覆盖**——2026-10-18 轮换后网关
   改用该格式，而 GitHub push protection 不认 opencode 提供商（实测免费计划下
   非提供方模式/自定义模式不可用），本扫描器是唯一兜底 → 盲点即泄露通道；
2. 既有格式（sk- / pk-lf- / sk-lf- / tvly- / AKIA）保持覆盖；
3. 普通文本不误报（门禁可持续的前提）。

样本一律运行时拼接（字面量会命中扫描器自身 → 门禁自伤）。
"""

from __future__ import annotations

import check_secrets


def _matches_any(sample: str) -> bool:
    return any(p.search(sample) for p in check_secrets.KEY_PATTERNS)


def test_new_opencode_key_format_is_covered():
    """新格式 oc_sk_（轮换后唯一在用的网关 key 形态）必须在扫描面内。"""
    sample = "oc_sk_91765cadc2d3_" + "A" * 24
    assert _matches_any(sample), "oc_sk_ 格式未被任何模式覆盖——新 key 泄露将无人拦截"


def test_existing_provider_formats_still_covered():
    samples = {
        "openai-style": "sk-" + "a" * 32,
        "langfuse-public": "pk-lf-" + "b" * 16,
        "langfuse-secret": "sk-lf-" + "c" * 16,
        "tavily": "tvly-dev-" + "d" * 16,
        "aws-akia": "AKIA" + "ABCDEFGHIJKLMNOP",
    }
    for name, sample in samples.items():
        assert _matches_any(sample), f"{name} 模式回归丢失"


def test_plain_text_not_flagged():
    """普通配置/文档文本不得误报（占位符、变量名、短 token）。"""
    for text in (
        "LLM_API_KEY=your-key-here",
        "# LANGFUSE_SECRET_KEY=sk-lf-xxx",
        "OPENCODE_API_KEY_DEEPSEEK_V4_FLASH",
        "key = os.environ['LLM_API_KEY']",
    ):
        assert not _matches_any(text), f"误报：{text!r}"


def test_repo_scan_passes():
    """当前仓库树整体扫描必须通过（扫描器自身的端到端回归）。"""
    assert check_secrets.main() == 0
