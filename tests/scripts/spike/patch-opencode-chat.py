"""spike-patch：RD-Agent LiteLLM 后端支持 chat 专用凭据/端点 + opencode 会话头注入。

背景：opencode zen/go 网关强制要求 `x-opencode-session` 头（缺头 400 MissingSessionID），
且 chat 与 embedding 需要分离端点（chat=opencode deepseek；embedding 留 bigmodel）。
LLMSettings 已有 `chat_openai_api_key` / `chat_openai_base_url` 字段但后端未消费——
本补丁让 completion 调用消费之，并在 opencode 端点注入会话头。
备份：同目录 .bak-opencode-patch。
"""

from __future__ import annotations

import pathlib
import shutil

TARGET = pathlib.Path("/opt/rdagent/lib/python3.11/site-packages/rdagent/oai/backend/litellm.py")

OLD = """        response = completion(
            messages=messages,
            stream=LITELLM_SETTINGS.chat_stream,
            max_retries=0,
            **complete_kwargs,
            **kwargs,
        )"""

NEW = """        # [spike-patch] chat 专用凭据/端点（如 opencode 网关）+ 会话头注入（缺头 400）
        if LITELLM_SETTINGS.chat_openai_api_key:
            kwargs.setdefault("api_key", LITELLM_SETTINGS.chat_openai_api_key)
        if LITELLM_SETTINGS.chat_openai_base_url:
            kwargs.setdefault("api_base", LITELLM_SETTINGS.chat_openai_base_url)
        if "opencode" in str(
            LITELLM_SETTINGS.chat_openai_base_url or LITELLM_SETTINGS.openai_api_base or ""
        ):
            kwargs.setdefault("extra_headers", {"x-opencode-session": "rdagent-spike"})
        response = completion(
            messages=messages,
            stream=LITELLM_SETTINGS.chat_stream,
            max_retries=0,
            **complete_kwargs,
            **kwargs,
        )"""


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    if "[spike-patch] chat 专用凭据" in text:
        print("already patched")
        return
    assert OLD in text, "anchor not found"
    backup = TARGET.with_suffix(".py.bak-opencode-patch")
    if not backup.exists():
        shutil.copy2(TARGET, backup)
    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("patched:", TARGET)
    print("backup:", backup)


if __name__ == "__main__":
    main()
