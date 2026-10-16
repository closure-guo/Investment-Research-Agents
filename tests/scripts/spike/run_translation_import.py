"""spike ⑤：翻译产出经导入通道过五道闸（unify-quant-research-architecture）。

用法::

    uv run python tests/scripts/spike/run_translation_import.py \
        --candidates tests/scripts/spike/candidates_2026-10-16.json \
        --out reports/spike-rdagent-translation-20261016.json [--dry-run]

流程：

1. 候选清单（name / qlib_expression / formulation / workspace）→ 逐条翻译裁决
   （``qlib_translate.translate_qlib_expression``，二值裁决）；
2. 组通道载荷：可翻译 → DSL 表达式；不可翻译 → 原始 Qlib 表达式
   （通道预检失败 → ``errors`` 桶 gate=None；裁决清单在 ``hypothesis`` 留档）；
3. ``run_factor_external_import``（生产通道与默认库路径）→ 摘要 + 逐条拒绝原因；
4. 打印裁决表与摘要；``--out`` 落 JSON 产物（裁决表 + 全量映射表 + 通道摘要）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# spike 工具（与 qlib_translate 同目录）——显式入 path 以支持任意 CWD 运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qlib_translate  # noqa: E402

from finance_agent.factors.external import run_factor_external_import  # noqa: E402


def build_payload(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """逐条翻译 → （通道载荷, 裁决表）。"""
    payload: list[dict[str, Any]] = []
    verdicts: list[dict[str, Any]] = []
    for cand in candidates:
        result = qlib_translate.translate_qlib_expression(cand["qlib_expression"])
        verdicts.append(
            {
                "name": cand["name"],
                "qlib_expression": cand["qlib_expression"],
                "translatable": result.translatable,
                "dsl_expression": result.dsl_expression,
                "blockers": list(result.blockers),
                "used_mappings": list(result.used_mappings),
            }
        )
        if result.translatable:
            hypothesis = (
                f"[RD-Agent/{cand['name']}] {cand['formulation']}；"
                f"翻译 {cand['qlib_expression']} → {result.dsl_expression}"
            )
            expression = result.dsl_expression
        else:
            hypothesis = f"[RD-Agent/{cand['name']}] 不可翻译：{'；'.join(result.blockers)}"
            expression = cand["qlib_expression"]  # 预检失败 → errors 桶（gate=None）
        payload.append(
            {
                "expression": expression,
                "hypothesis": hypothesis,
                "category": cand.get("category") or "RD-Agent",
            }
        )
    return payload, verdicts


def _print_verdicts(verdicts: list[dict[str, Any]]) -> None:
    for verdict in verdicts:
        mark = "可翻译" if verdict["translatable"] else "不可翻译"
        target = verdict["dsl_expression"] or "-"
        print(f"[{mark}] {verdict['name']}: {verdict['qlib_expression']} -> {target}")
        for blocker in verdict["blockers"]:
            print(f"    blocker: {blocker[:160]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="spike ⑤：翻译 + 外部导入过五道闸")
    parser.add_argument("--candidates", required=True, help="候选 JSON 文件")
    parser.add_argument("--out", default=None, help="产物 JSON 输出路径（可选）")
    parser.add_argument("--dry-run", action="store_true", help="只翻译不过闸")
    args = parser.parse_args(argv)

    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    if not isinstance(candidates, list) or not all(isinstance(c, dict) for c in candidates):
        print("错误：候选文件须为 JSON 数组（元素为对象）", file=sys.stderr)
        return 2

    payload, verdicts = build_payload(candidates)
    _print_verdicts(verdicts)
    translated = sum(1 for v in verdicts if v["translatable"])
    print(f"翻译裁决：{translated}/{len(verdicts)} 可翻译")

    summary: dict[str, Any] | None = None
    if args.dry_run:
        print("dry-run：跳过导入通道")
    else:
        summary = run_factor_external_import(payload)
        print("通道摘要：" + json.dumps(summary, ensure_ascii=False, indent=1))

    artifact = {
        "candidates_file": str(args.candidates),
        "mapping_table": qlib_translate.dump_mapping_table(),
        "verdicts": verdicts,
        "payload": payload,
        "summary": summary,
    }
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"产物：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
