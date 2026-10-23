"""系统操作工具的能力门禁。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


def operator_tools_enabled(
    capability: Mapping[str, Any] | None,
    *,
    env_value: str | None = None,
) -> bool:
    """判断是否注册系统操作工具。

    - 环境总开关 ``QUICK_OPERATOR_TOOLS``（默认开）优先；
    - probe 事实 ``tool_call=False`` → 关闭；
    - capability 缺失/None → 放行（probe 未完成不误伤）。
    """
    raw = os.getenv("QUICK_OPERATOR_TOOLS", "1") if env_value is None else env_value
    if str(raw).strip().lower() in {"0", "false", "off", "no"}:
        return False
    return not (isinstance(capability, Mapping) and capability.get("tool_call") is False)
