"""构建 cohort universe 登记文件（delta add-forward-paper-trading-cohort）。

沪深300 成分 → 逐只富化行业/总市值 → 按 (industry, market_cap_bucket) 分层无放回
抽 n 只 → 落 `data/cohort/universe-<version>.json`。同 seed 可复现同一成分清单；
版本生效期内默认拒绝覆盖既有登记文件（换池请另起 version 或显式 --force）。

用法：
    uv run python scripts/build_cohort_universe.py --version v1 --n 10 --seed 42 \
        --out data/cohort/universe-v1.json

注意：需网络（akshare 成分 + 逐股行业/行情）；须用 `uv run`（脚本 import 包）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from finance_agent.outcome.cohort.universe import build_universe, write_universe


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 cohort universe 登记文件")
    parser.add_argument("--version", required=True, help="universe 版本号，如 v1")
    parser.add_argument("--n", type=int, default=10, help="抽样成分数（默认 10）")
    parser.add_argument("--seed", type=int, default=42, help="抽样种子（默认 42）")
    parser.add_argument(
        "--out", default=None, help="输出路径（默认 data/cohort/universe-<version>.json）"
    )
    parser.add_argument(
        "--force", action="store_true", help="覆盖既有登记文件（版本期内换池才用，慎用）"
    )
    args = parser.parse_args()

    out = Path(args.out) if args.out else Path("data/cohort") / f"universe-{args.version}.json"

    data = build_universe(args.version, n=args.n, seed=args.seed)
    write_universe(data, out, force=args.force)

    n_const = len(data["constituents"])
    print(f"已写入 {out}（version={data['version']} n={n_const} seed={data['seed']}）")
    for c in data["constituents"]:
        print(f"  {c['ticker']}  {c['name']:<10}  {c['industry']}  {c['market_cap_bucket']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
