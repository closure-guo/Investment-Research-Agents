"""P1 人工终裁材料生成（薄壳）：证据回填 + 案例去重 + 机器预读。

背景：round-2 交付的终裁清单四列逐态证据全空（`6f3fcc9`），人工无从判读。本脚本从
跑批台账 `resume.json`（单元级完整载荷）重建两份材料：

1. 单元级清单 `--worklist`：`status=ok` 且关态逃逸的单元 + 四列证据 + 拦截原因桶
   （真拦 vs 伪拦截可分辨）；已填的人工裁定按 `unit_id` 继承，重生成不覆盖。
2. 案例级清单 `--cases`：同标的同型的确定性实例完全相同 → 折叠成案例（判 50 次而非
   180 次），附机器**预读**列（证据整理，非终裁；终裁仍由 `human_verdict` 列人工填写）。

用法：
    uv run python tests/scripts/p1_adjudication_material.py
    uv run python tests/scripts/p1_adjudication_material.py --resume reports/ablation/p1/resume.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation.adjudication import (  # noqa: E402
    adjudicable_units,
    case_groups,
    worklist_rows,
    write_cases_csv,
    write_worklist_csv,
)

DEFAULT_RESUME = Path("reports/ablation/p1-round2/resume.json")
DEFAULT_WORKLIST = Path("tests/validation/2026-09-16-p1-adjudication-worklist.csv")
DEFAULT_CASES = Path("tests/validation/2026-09-16-p1-adjudication-cases.csv")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P1 人工终裁材料生成（证据回填 + 案例去重）")
    parser.add_argument(
        "--resume", type=Path, default=DEFAULT_RESUME, help="跑批台账（单元级载荷）"
    )
    parser.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST, help="单元级清单输出")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="案例级清单输出")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    resume_path = Path(args.resume)
    if not resume_path.exists():
        print(
            f"[拒绝] 台账不存在：{resume_path.as_posix()}（先跑批或指定 --resume）", file=sys.stderr
        )
        return 2
    resume = json.loads(resume_path.read_text(encoding="utf-8"))
    units = list(resume.get("units") or [])
    population = adjudicable_units(units)
    rows = worklist_rows(population)
    cases = case_groups(population)
    try:
        write_worklist_csv(Path(args.worklist), rows)
        write_cases_csv(Path(args.cases), cases)
    except PermissionError as exc:
        # 终裁材料常被 Excel/WPS 打开着：给可执行的提示，不甩 traceback
        print(
            f"[拒绝写入] 文件被占用（可能正在 Excel/WPS 中打开）：{exc.filename}——关闭后重试",
            file=sys.stderr,
        )
        return 3
    print(f"台账: {resume_path.as_posix()}（单元 {len(units)}）")
    print(f"单元级清单: {len(rows)} 行 → {Path(args.worklist).as_posix()}（四列证据已回填）")
    print(f"案例级清单: {len(cases)} 行 → {Path(args.cases).as_posix()}（同标的同型实例已折叠）")
    readings = Counter(str(c["machine_reading"]) for c in cases)
    print("机器预读分布（证据整理，非终裁）:")
    for reading, n in sorted(readings.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:>3}  {reading}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
