"""因子风格暴露批诊断（add-factor-style-exposure，只读运维入口）。

用法::

    # 容器内推荐（快照库跨挂载 WAL 纪律，incident 043）
    docker compose exec -T backend python scripts/factor_style_exposure.py

    # 本地（临时快照库/库路径，避免碰生产数据）
    uv run python scripts/factor_style_exposure.py --snapshot-db tmp/x.db --db tmp/y.db --out reports/style.json

逐因子输出「与 builtin 风格代理的 IC 相关」矩阵与主导风格；|主导 ρ| ≥ 阈值（0.7）
给出「与标准风格高度重合」标注。**只读诊断，不改变任何准入状态**——风格是合法收益源，
重复由闸④池内冗余拦截。
退出码 0 = 跑完（含空库）；2 = 参数/库不可读。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import evaluate, forward_returns, rank_ic_series
from finance_agent.factors.library import list_factors
from finance_agent.factors.style_exposure import (
    STYLE_CORRELATION_THRESHOLD,
    compute_style_exposure,
    style_exposure_payload,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="因子风格暴露批诊断（只读）")
    parser.add_argument("--db", default=None, help="因子库路径（缺省 env/默认）")
    parser.add_argument("--snapshot-db", default=None, help="快照库路径（缺省 env/默认）")
    parser.add_argument("--out", default=None, help="产物 JSON 输出路径（可选）")
    args = parser.parse_args(argv)

    try:
        from finance_agent.factors.monthly import _panel_from_snapshot

        panel = _panel_from_snapshot(args.snapshot_db)
    except (OSError, ValueError) as exc:
        print(f"错误：准入面板不可得：{exc}", file=sys.stderr)
        return 2

    fwd = forward_returns(panel.fields["close"])
    rows: list[dict[str, Any]] = []
    for row in list_factors(db_path=args.db):
        try:
            factor_ic = rank_ic_series(evaluate(parse(str(row["expression"])), panel), fwd)
        except (OSError, ValueError) as exc:
            print(f"警告：因子 {row['id']} 不可求值：{exc}", file=sys.stderr)
            continue
        payload = style_exposure_payload(compute_style_exposure(factor_ic, panel, fwd=fwd))
        payload["factor_id"] = int(row["id"])
        payload["expression"] = str(row["expression"])
        rows.append(payload)
        flag = " ⚠与标准风格高度重合" if payload["flagged"] else ""
        dominant = payload["dominant"] or "—"
        rho = payload["dominant_correlation"]
        print(
            f"因子 {payload['factor_id']}: 主导={dominant} ρ={'—' if rho is None else f'{rho:+.2f}'}"
            f"（{payload['n_days']} 日）{flag}  {payload['expression'][:48]}"
        )
    print(f"阈值 |ρ| ≥ {STYLE_CORRELATION_THRESHOLD}；共 {len(rows)} 条")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"产物：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
