"""RD-Agent 生成侧：跨会话因子记忆读取端。

数据源：本仓库 `data/factor_research/explored_factors.json`
（由 `scripts/export_factor_feedback.py` 从收割状态导出）。
部署到 `/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/`，
由补丁（`tests/scripts/spike/patch-explored-feedback.py`）在
`QlibFactorHypothesisGen.prepare_context` 的 `hypothesis_and_feedback` 末尾追加本块。

契约：文件缺失/损坏/字段异常 → 返回空串（fail-safe，绝不阻断生成）；
渲染结果超过 MAX_CHARS → 截断并标注；不修改任何全局状态。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/explored_factors.json"
MAX_CHARS = 9000
_DESC_CHARS = 110
_EXPR_CHARS = 90


def _format_item(item: dict) -> str:
    name = str(item.get("name") or "?")
    desc = " ".join(str(item.get("desc") or "").split())[:_DESC_CHARS]
    status = str(item.get("status") or "?")
    gate = item.get("gate")
    reason = str(item.get("reason") or "")
    tail = status
    if isinstance(gate, int):
        tail += f" gate{gate}"
    if reason:
        tail += f" {reason}"
    line = f"- {name}: {desc} [{tail}]"
    expression = item.get("expression")
    if expression:
        line += f" expr={str(expression)[:_EXPR_CHARS]}"
    return line


def render_explored_block(path: str | None = None) -> str:
    resolved = Path(path or os.environ.get("FACTOR_FEEDBACK_PATH", DEFAULT_PATH))
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    items = payload.get("explored") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return ""

    lines = [
        "=========================================================",
        "# Cross-session memory: already explored factors",
        "The factors below were already proposed and evaluated by the downstream 5-gate library.",
        "Do NOT propose the same hypotheses or trivial variants (renamed windows, epsilon guards, "
        "rescalings). Prefer genuinely different mechanisms and data transformations.",
        "",
    ]
    used = sum(len(line) + 1 for line in lines)
    for item in items:
        if not isinstance(item, dict):
            continue
        line = _format_item(item)
        if used + len(line) + 1 > MAX_CHARS:
            lines.append("- ... (truncated)")
            break
        lines.append(line)
        used += len(line) + 1
    lines.append("=========================================================")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_explored_block()[:2000])
