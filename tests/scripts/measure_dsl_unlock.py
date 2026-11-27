"""P1 离线解锁测量（extend-factor-library-coverage delta 13.1）。

对收割状态文件中**已记录**的 qlib 表达式做确定性重译 + DSL 复杂度复验——零 LLM
调用、零生产写入（只读状态文件）。回答两个问题：

1. DSL 扩表达力（去重计数 + 19 算子 + 15 翻译映射）后，存量不可译候选解锁多少条；
2. 仍被挡住的那部分，死因各占多少（缺失算子 / 复杂度三轴）。

为什么不直接跑生产重放：`scripts/replay_factor_harvest.py` 对每条候选都重新调用
LLM 转译（不复用已记录的 qlib 表达式），834 条即 834 次 LLM 调用，且 LLM 非确定性
会让「DSL 扩展」这个单变量与转译噪声混在一起。本脚本把「转译」固定在已记录产物上，
只让「翻译 + 复杂度」这一段是新的。

用法::

    uv run python tests/scripts/measure_dsl_unlock.py \\
        --state tmp/harvest_state.json

    # 机器可读输出
    uv run python tests/scripts/measure_dsl_unlock.py --state tmp/harvest_state.json --json

退出码：0 = 跑完；2 = 参数或文件错误（不静默）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from finance_agent.factors.dsl import DSLParseError, FactorComplexityError, parse
from finance_agent.factors.qlib_translate import translate_qlib_expression


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"状态文件读取失败: {path}：{exc}") from exc
    processed = payload.get("processed")
    if not isinstance(processed, dict):
        raise ValueError(f"状态文件缺少 processed 映射: {path}")
    return processed


def _complexity_bucket(message: str) -> str:
    """复杂度拒收按违规轴归类（三条上限一次列出时按首现轴计）。"""
    if "深度" in message:
        return "复杂度·深度"
    if "自由参数" in message:
        return "复杂度·自由参数"
    if "字段种类" in message:
        return "复杂度·字段种类"
    return "复杂度·其他"


def _classify_blockers(blockers: tuple[str, ...]) -> str:
    """翻译失败原因归类：复杂度（翻译器内部已跑 parse 复杂度校验）> 缺失算子 > 其他。

    注意 ``translate_qlib_expression`` 的成功条件是「可翻译 **且** 过 DSL 复杂度」——
    blocker 原文以「复杂度超限：…」出现，不能被当成算子缺失。
    """
    joined = "；".join(blockers)
    if "复杂度超限" in joined:
        return _complexity_bucket(joined)
    if any("本 DSL 无" in blocker or "未知字段" in blocker for blocker in blockers):
        return "缺失算子/字段"
    return "翻译其他"


def measure(state: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """逐条重译 + 复验，返回读数（纯函数，便于测试）。"""
    total_with_qlib = 0
    unlocked: list[dict[str, Any]] = []
    blocked: Counter[str] = Counter()
    blocked_samples: dict[str, list[str]] = {}
    for name, entry in sorted(state.items()):
        if entry.get("status") != "untranslatable":
            continue
        qlib = entry.get("qlib_expression")
        if not isinstance(qlib, str) or not qlib.strip():
            blocked["无 qlib 表达式（llm 阶段拒译）"] += 1
            continue
        total_with_qlib += 1
        result = translate_qlib_expression(qlib)
        if not result.translatable or not result.dsl_expression:
            reason = _classify_blockers(result.blockers)
            blocked[reason] += 1
            blocked_samples.setdefault(reason, []).append(f"{name}: {qlib[:100]}")
            continue
        try:
            parse(result.dsl_expression)  # 防御复验：翻译器已校验，此处失败即翻译器缺陷
        except (FactorComplexityError, DSLParseError) as exc:
            blocked["翻译产物复验失败"] += 1
            blocked_samples.setdefault("翻译产物复验失败", []).append(f"{name}: {exc}")
        else:
            unlocked.append({"name": name, "expression": result.dsl_expression})
    return {
        "untranslatable_total": sum(
            1 for entry in state.values() if entry.get("status") == "untranslatable"
        ),
        "with_qlib_expression": total_with_qlib,
        "unlocked": len(unlocked),
        "still_blocked": dict(sorted(blocked.items(), key=lambda kv: -kv[1])),
        "unlocked_samples": unlocked[:20],
        "blocked_samples": {k: v[:5] for k, v in blocked_samples.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 离线解锁测量（零 LLM、零写入）")
    parser.add_argument("--state", required=True, help="harvest_state.json 路径（只读）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    try:
        state = _load_state(Path(args.state))
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    report = measure(state)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"untranslatable 总数: {report['untranslatable_total']}")
    print(f"其中携带 qlib 表达式: {report['with_qlib_expression']}")
    print(f"解锁（可翻译且过复杂度）: {report['unlocked']}")
    print("仍被挡住：")
    for reason, count in report["still_blocked"].items():
        print(f"  {reason}: {count}")
    print("解锁样例：")
    for sample in report["unlocked_samples"][:10]:
        print(f"  {sample['name']}: {sample['expression']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
