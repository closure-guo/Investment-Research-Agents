"""按状态重放收割候选（recover-factor-supply-losses，运维入口）。

用法::

    # 只读预演：列出将被重放的候选（不调用 LLM、不写状态）
    uv run python scripts/replay_factor_harvest.py --statuses untranslatable --dry-run

    # 生产重放（写生产状态 + 因子库；LLM 网关须可用）
    uv run python scripts/replay_factor_harvest.py --statuses untranslatable,transcription_error

    # 测量性复跑：临时状态/库，不碰生产（纪律同「复跑务必临时库」）
    uv run python scripts/replay_factor_harvest.py --statuses untranslatable \\
        --state tmp/replay_state.json --db tmp/replay_factor_library.db

语义（spec factor-proposal「外部研发供给接入」）：状态命中 ``--statuses`` 的已处理
候选重新执行「转译 → 翻译 → 过闸」，新裁决覆盖旧记录并保留 ``retried_from``；
重放候选优先于未处理候选进入单批上限截断。单批上限默认 100（闸③ BH-FDR 分辨率
覆盖 K ≤ 100）——**只想重放、不想带上未处理候选时，把 ``--limit`` 设为重放条数**
（重放先占满批次）。退出码 0 = 跑完（含零重放）；2 = 参数或文件错误（不静默）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from finance_agent.factors.harvest import (  # noqa: E402 - load_dotenv 须先于 llm 模块导入
    DEFAULT_CANDIDATES_PATH,
    DEFAULT_STATE_PATH,
    run_factor_harvest,
)

# 单批上限（闸③ BH-FDR 分辨率覆盖 K ≤ 100；重放优先于未处理候选）
DEFAULT_REPLAY_LIMIT = 100
# 可重放状态白名单：`admitted` 等**不可**重放——入库候选重放会命中结构查重
# （validate._dedup_gate）把状态改写成「已拒」而因子仍在库内，审计账本失真；
# `pending_gate` 是运行中中间态，不该出现在落盘状态里。
REPLAYABLE_STATUSES: frozenset[str] = frozenset(
    {"untranslatable", "transcription_error", "rejected"}
)
DEFAULT_STATUSES: tuple[str, ...] = ("untranslatable", "transcription_error", "rejected")


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    """严格读取状态文件：缺失/不可读/结构非法 → ``ValueError``（调用方退 2）。

    与 harvest 内部 ``_load_json``（静默空）刻意不同——重放脚本若把坏状态读成 {},
    随后整文件改写会把历史裁决**整体替换**，故此处 fail loud。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"状态文件读取失败（重放需已知裁决）：{path}：{exc}") from exc
    processed = payload.get("processed") if isinstance(payload, dict) else None
    if not isinstance(processed, dict):
        raise ValueError(f"状态文件结构非法（缺 processed 对象）：{path}")
    return dict(processed)


def _load_candidate_names(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"候选文件读取失败：{exc}") from exc
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        raise ValueError("候选文件须含 candidates 数组")
    return [
        str(candidate.get("name"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("name")
    ]


def _parse_statuses(raw: str) -> tuple[str, ...]:
    statuses = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not statuses:
        raise ValueError("--statuses 不能为空（例：untranslatable,transcription_error）")
    unknown = sorted(set(statuses) - REPLAYABLE_STATUSES)
    if unknown:
        raise ValueError(
            f"不可重放状态 {','.join(unknown)}（可用：{','.join(sorted(REPLAYABLE_STATUSES))}）"
        )
    return statuses


def _replay_selection(
    state: dict[str, dict[str, Any]], names: list[str], statuses: tuple[str, ...]
) -> list[tuple[str, str]]:
    """按候选文件次序选出命中状态的已处理候选（去重）。"""
    wanted = set(statuses)
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        status = str(state.get(name, {}).get("status"))
        if status in wanted:
            selected.append((name, status))
            seen.add(name)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按状态重放收割候选（重放 → 转译 → 翻译 → 五道闸）"
    )
    parser.add_argument(
        "--statuses",
        required=True,
        help=f"逗号分隔的重放状态（例：{','.join(DEFAULT_STATUSES)}）",
    )
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES_PATH, help="候选 JSON 文件")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="收割状态文件")
    parser.add_argument("--db", default=None, help="因子库路径（缺省 env/默认；测量复跑传临时库）")
    parser.add_argument("--snapshot-db", default=None, help="快照库路径（缺省 env/默认）")
    parser.add_argument("--limit", type=int, default=DEFAULT_REPLAY_LIMIT, help="单批上限（1-100）")
    parser.add_argument("--dry-run", action="store_true", help="只列重放候选，不执行")
    parser.add_argument("--out", default=None, help="产物 JSON 输出路径（可选）")
    args = parser.parse_args(argv)

    try:
        statuses = _parse_statuses(args.statuses)
        names = _load_candidate_names(Path(args.candidates))
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if not 1 <= args.limit <= 100:
        # 闸③ BH-FDR 分辨率约束：K > 100 时置换检验 p 值下界不可能满足 BH_FDR_Q/K
        print("错误：--limit 须在 1-100 之间（闸③ BH-FDR 分辨率）", file=sys.stderr)
        return 2
    state_path = Path(args.state)
    try:
        before = _load_state(state_path)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print("重放目标（缺省即生产路径，测量复跑请显式传 --state/--db 临时路径）：")
    print(f"  candidates = {args.candidates}")
    print(f"  state      = {state_path}（将被改写）")
    print(f"  factor_db  = {args.db or '（env/默认生产库）'}")
    print(f"  snapshot   = {args.snapshot_db or '（env/默认生产快照库）'}")
    selection = _replay_selection(before, names, statuses)
    print(f"重放状态：{','.join(statuses)}｜选中 {len(selection)} 条（单批上限 {args.limit}）")
    for name, status in selection:
        print(f"  - {name}: {status}")

    if args.dry_run:
        print("dry-run：未执行转译/过闸，未写状态")
        return 0

    summary = run_factor_harvest(
        candidates_path=args.candidates,
        state_path=state_path,
        db_path=args.db,
        snapshot_db_path=args.snapshot_db,
        limit=args.limit,
        retry_statuses=statuses,
    )
    try:
        after = _load_state(state_path)
    except ValueError as exc:  # 运行后状态不可读=异常信号，显式退 2 而非 traceback
        print(f"错误：重放已执行但状态复核失败：{exc}", file=sys.stderr)
        return 2
    print("\n状态迁移：")
    for name, old_status in selection:
        new = after.get(name, {})
        print(
            f"  - {name}: {old_status} -> {new.get('status')}"
            f"（{new.get('stage') or new.get('reason') or new.get('gate') or ''}）"
        )
    print("\n摘要：" + json.dumps(summary, ensure_ascii=False, indent=1))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "statuses": list(statuses),
            "candidates_file": str(args.candidates),
            "state_file": str(args.state),
            "selected": [{"name": n, "from": s} for n, s in selection],
            "after": {n: after.get(n) for n, _ in selection},
            "summary": summary,
        }
        out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"产物：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
