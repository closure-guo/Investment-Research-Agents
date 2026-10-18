"""因子候选检验账本读数（issue #355 使能层：跨批次 FDR 观察面，只读）。

输出：
1. 库级汇总（总候选 / 已验证（闸③ p 值非空，= 跨批次 FDR 家族面）/ 已入库）；
2. 分批明细（batch_id / source / 批大小 / 已验证 / 入库）；
3. **全局 BH 反事实预览**：把全部已验证 p 值作为一个跨批次家族做一次 BH 校正
   （q 默认 0.1 = 闸③口径），给出「若执行全局账本，今日会有多少候选保持显著」。
   本读数不改库、不改任何准入结论——执行层（强制全局校正 / 批内更严 q' / 接受
   残余风险）属 issue #355 三方向，待 owner 裁决。

用法::

    uv run python scripts/factor_fdr_report.py                  # 默认库（env FACTOR_LIBRARY_DB_PATH 优先）
    uv run python scripts/factor_fdr_report.py --db <path>
    uv run python scripts/factor_fdr_report.py --q 0.05

退出码：0 正常（含空库）；2 参数/库不可读。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_agent.factors.library import ledger_summary, list_ledger_entries  # noqa: E402
from finance_agent.factors.validate import BH_FDR_Q, cumulative_fdr_preview  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="因子候选检验账本读数（只读）")
    parser.add_argument("--db", default=None, help="因子库路径（缺省 FACTOR_LIBRARY_DB_PATH/默认）")
    parser.add_argument("--q", type=float, default=BH_FDR_Q, help=f"BH 校正水平（默认 {BH_FDR_Q}）")
    args = parser.parse_args(argv)

    try:
        entries = list_ledger_entries(args.db)
        summary = ledger_summary(args.db)
    except Exception as exc:  # noqa: BLE001 - CLI 兜底：库不可读如实退 2
        print(f"错误：因子库不可读：{exc}", file=sys.stderr)
        return 2

    print(
        f"账本汇总: 候选 {summary['total']} · 已验证（p 非空）{summary['tested']} · "
        f"已入库 {summary['admitted']}"
    )
    if summary["batches"]:
        print("分批明细:")
        for batch in summary["batches"]:
            print(
                f"  {batch['batch_id']}  [{batch['source']}]  "
                f"size={batch['size']} tested={batch['tested']} admitted={batch['admitted']}"
            )
    else:
        print("分批明细: （空——尚无候选过闸记录）")

    preview = cumulative_fdr_preview(entries, q=args.q)
    print(
        f"全局 BH 反事实预览 (q={preview['q']}): 家族 {preview['family_size']} → "
        f"保持显著 {preview['significant_count']}"
    )
    for row in preview["significant"][:20]:
        print(
            f"  ✓ [{row['source']}] {row['expression']}  p={row['p_value']:.4g} → "
            f"p_adj={row['p_adj']:.4g}"
            + (f"  (factor_id={row['factor_id']})" if row["factor_id"] else "  (未入库)")
        )
    if preview["significant_count"] > 20:
        print(f"  …… 其余 {preview['significant_count'] - 20} 条略")
    print("注：反事实预览为只读读数，不改变任何准入结论（执行层待 owner 裁决，issue #355）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
