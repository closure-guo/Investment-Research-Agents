"""构建 cohort universe 登记文件（delta add-forward-paper-trading-cohort）。

沪深300 成分 → 逐只富化行业/总市值 → 按市值配额 + 桶内行业去重无放回抽 n 只 →
落 `data/cohort/universe-<version>.json`，并把**富化后的整池**落旁车快照
`data/cohort/universe-<version>.pool.json`（登记文件记 `pool_ref` / `pool_hash`）。

复现路径：
- 同 version + 同 seed + **同一池快照** → 逐字相同的成分清单（`--pool-from` 离线重建）；
- 上游快照波动只影响**新版本**生成的池，不影响既有版本的复现。

用法：
    # 联网生成（首次 / 换 version）
    uv run python scripts/build_cohort_universe.py --version v1 --n 10 --seed 42
    # 离线重建（用旁车快照复现同一成分清单，需配 --force 覆盖同版本）
    uv run python scripts/build_cohort_universe.py --version v1 --seed 42 \
        --pool-from data/cohort/universe-v1.pool.json --force

注意：首次生成需网络（akshare 成分 + 逐股行业/行情）；`--pool-from` 不联网。
须用 `uv run`（脚本 import 包）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from finance_agent.outcome.cohort.universe import build_universe, pool_ref, write_universe

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_pool(path: str) -> list[dict[str, Any]]:
    pool = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(pool, list):
        raise ValueError(f"池快照必须是 JSON 数组（每行 ticker/name/industry/market_cap）: {path}")
    return pool


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 cohort universe 登记文件")
    parser.add_argument("--version", required=True, help="universe 版本号，如 v1")
    parser.add_argument("--n", type=int, default=10, help="抽样成分数（默认 10）")
    parser.add_argument("--seed", type=int, default=42, help="抽样种子（默认 42）")
    parser.add_argument(
        "--out", default=None, help="输出路径（默认 <仓库根>/data/cohort/universe-<version>.json）"
    )
    parser.add_argument(
        "--pool-from",
        default=None,
        help="从池快照离线重建（不联网）；默认联网重新富化整池",
    )
    parser.add_argument(
        "--force", action="store_true", help="同版本重生成（覆盖既有登记文件；换池须换 version）"
    )
    args = parser.parse_args()

    # M7：默认按**仓库根**解析，避免在任意 CWD 下写错位置
    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "cohort" / f"universe-{args.version}.json"
    )
    pool_path = out.parent / pool_ref(args.version)

    if args.pool_from:
        print(f"离线重建：池快照 {args.pool_from}")
        data = build_universe(
            args.version, n=args.n, seed=args.seed, pool=_load_pool(args.pool_from)
        )
    else:
        data = build_universe(args.version, n=args.n, seed=args.seed)

    rows = data.pop("pool")  # 瞬态键：整池行落旁车快照，不写进登记文件
    # 先写登记文件（版本守卫在此拦截）；通过后再落快照，失败不产生孤儿旁车文件
    write_universe(data, out, force=args.force)
    pool_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    n_const = len(data["constituents"])
    print(
        f"已写入 {out}（version={data['version']} n={n_const} seed={data['seed']}"
        f" pool_size={data['pool_size']} pool_hash={data['pool_hash']}）"
    )
    print(f"已写入池快照 {pool_path}（{len(rows)} 行）")
    for c in data["constituents"]:
        print(f"  {c['ticker']}  {c['name']:<10}  {c['industry']}  {c['market_cap_bucket']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
