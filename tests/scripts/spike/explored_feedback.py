"""RD-Agent 生成侧：跨会话因子记忆读取端。

数据源：本仓库 `data/factor_research/explored_factors.json`
（由 `scripts/export_factor_feedback.py` 从收割状态导出）。
部署到 `/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/`，
由补丁（`tests/scripts/spike/patch-explored-feedback.py`）在
`QlibFactorHypothesisGen.prepare_context` 的 `hypothesis_and_feedback` 末尾追加本块；
`render_contract_block()` 由 `patch-prompt-contract.py` 注入 prompt 模板本体的
任务指令层（`hypothesis_specification` 前置，每轮生效）。

契约：文件缺失/损坏/字段异常 → 返回空串（fail-safe，绝不阻断生成）；
渲染结果超过 MAX_CHARS → 截断并标注；不修改任何全局状态。
feed-dsl-contract-to-factor-generator：载荷含 `contract` 块（复杂度上限/算子与
字段白名单/表达规则）与 `gate_rejections` 时，先渲染契约段再渲染已探索清单
（数据驱动，约束文本不硬编码在本端）；契约缺失 → 仅渲染已探索清单（旧行为）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/explored_factors.json"
MAX_CHARS = 9000
_DESC_CHARS = 110
_EXPR_CHARS = 90
# 任务指令层契约段预算：模板本体占位须小（全量白名单已随记忆块给出，本段做摘要）。
TEMPLATE_CONTRACT_MAX_CHARS = 1600
# 算子词表 ≤ 此数时全量列出（当前 16 个）：第 2 轮起本段是唯一算子指引，样例形式会让
# 模型把未列出的 Ref/Rank/Sub 等主力算子误当不可表达；扩容后再回退为摘要。
OPERATOR_LIST_LIMIT = 32


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


def _render_contract(contract: dict, gate_rejections: dict | None) -> list[str]:
    """契约段（数据驱动：全部取自载荷，不硬编码约束）。字段异常 → 跳过该行（fail-safe）。"""
    lines = [
        "# Hard contract for factor expressions (downstream DSL)",
        "Proposals violating any rule below are rejected BEFORE evaluation:",
    ]
    rules = contract.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, str) and rule.strip():
                lines.append(f"- {rule.strip()}")
    complexity = contract.get("complexity")
    if isinstance(complexity, dict) and complexity:
        parts = [f"{k} <= {v}" for k, v in sorted(complexity.items())]
        lines.append(f"- Complexity limits: {', '.join(parts)}.")
    operators = contract.get("operators")
    if isinstance(operators, list) and operators:
        lines.append(
            f"- Expressible operators (Qlib form): {', '.join(str(o) for o in operators)}."
        )
    fields = contract.get("fields")
    if isinstance(fields, list) and fields:
        lines.append(f"- Expressible fields: {', '.join(str(f) for f in fields)}.")
    if isinstance(gate_rejections, dict) and gate_rejections:
        pairs = ", ".join(f"gate{k}={v}" for k, v in sorted(gate_rejections.items()))
        lines.append(f"- Historical gate rejections to date: {pairs} (advisory).")
    return lines


def render_contract_block(path: str | None = None) -> str:
    """任务指令层契约段（prompt 模板本体注入用）：同源读载荷 ``contract`` 块。

    与 ``render_explored_block`` 的契约段同源（同一 ``contract`` 块派生，数值一致
    由同源结构性保证）。算子词表 ≤ ``OPERATOR_LIST_LIMIT`` 时**全量列出**——第 2 轮
    起本段是唯一算子指引，样例形式会把未列出的主力算子误导为不可表达；词表扩容后
    回退为「总数 + 样例」摘要。
    fail-safe：源缺失/损坏/无契约、或全部条款字段都不可用（只剩抬头）→ 空串
    （不阻断生成、不虚构契约）；复杂度损坏值（非数值）逐项跳过；超
    ``TEMPLATE_CONTRACT_MAX_CHARS`` 按行截断并标注（不按字符切片丢整行白名单）。
    """
    resolved = Path(path or os.environ.get("FACTOR_FEEDBACK_PATH", DEFAULT_PATH))
    try:
        payload: Any = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    contract = payload.get("contract")
    if not isinstance(contract, dict) or not contract:
        return ""
    lines = ["# Hard contract for factor expressions (BINDING, checked BEFORE evaluation):"]
    rules = contract.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, str) and rule.strip():
                lines.append(f"- {rule.strip()}")
    complexity = contract.get("complexity")
    if isinstance(complexity, dict) and complexity:
        parts = [
            f"{k} <= {v}"
            for k, v in sorted(complexity.items())
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if parts:
            lines.append(f"- Complexity limits: {', '.join(parts)}.")
    operators = contract.get("operators")
    if isinstance(operators, list) and operators:
        ops = [str(op) for op in operators]
        if len(ops) <= OPERATOR_LIST_LIMIT:
            lines.append(
                f"- Expressible operators: {len(ops)} in total: {', '.join(ops)}; "
                "any operator outside this set is inexpressible — do not use it."
            )
        else:
            sample = ", ".join(ops[:8])
            lines.append(
                f"- Expressible operators: {len(ops)} kinds in total (examples: {sample}); "
                "any operator outside this expressible set is inexpressible — do not use it."
            )
    fields = contract.get("fields")
    if isinstance(fields, list) and fields:
        lines.append(f"- Expressible fields: {', '.join(str(f) for f in fields)}.")
    if len(lines) == 1:  # 只有抬头：规则/复杂度/算子/字段全部缺失或损坏 → 视为无契约
        return ""
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > TEMPLATE_CONTRACT_MAX_CHARS:
            kept.append("- ... (truncated)")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def render_explored_block(path: str | None = None) -> str:
    resolved = Path(path or os.environ.get("FACTOR_FEEDBACK_PATH", DEFAULT_PATH))
    try:
        payload: Any = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    items = payload.get("explored")
    contract = payload.get("contract")
    gate_rejections = payload.get("gate_rejections")
    has_contract = isinstance(contract, dict)
    has_items = isinstance(items, list) and bool(items)
    if not has_contract and not has_items:
        return ""

    lines: list[str] = []
    used = 0
    if has_contract:
        for line in _render_contract(
            contract, gate_rejections if isinstance(gate_rejections, dict) else None
        ):
            lines.append(line)
            used += len(line) + 1
        lines.append("")
        used += 1
    if has_items:
        explored_header = [
            "=========================================================",
            "# Cross-session memory: already explored factors",
            "The factors below were already proposed and evaluated by the downstream 5-gate library.",
            "Do NOT propose the same hypotheses or trivial variants (renamed windows, epsilon guards, "
            "rescalings). Prefer genuinely different mechanisms and data transformations.",
            "",
        ]
        lines += explored_header
        used += sum(len(line) + 1 for line in explored_header)
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
