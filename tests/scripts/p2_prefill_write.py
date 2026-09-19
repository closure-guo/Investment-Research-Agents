"""预填稿安全写入器：机器列可再生成，用户列永不被覆盖（零 LLM）。

**为什么存在**：2026-09-18 事故——预填稿为加证据列被整体重建，人工在「你已标」列里
已写的答案被冲掉。本脚本把「用户列只读」从口头承诺变成代码约束：

1. 用户列（--user-cols，默认「你已标(吸收/新增)」）在写入时**逐行原样保留**；
2. 任何 update 试图改动用户列 → 直接报错退出（不是跳过，是拒绝整个写入）；
3. Excel 占用（PermissionError）→ [拒绝写入] 退出 3，不做任何部分写入；
4. --dry-run 只打印将要变更的机器列单元格 + 用户列保留行数，不动文件。

用法：
    uv run python tests/scripts/p2_prefill_write.py --updates updates.json [--dry-run]
updates.json 格式：{"<unit_id>": {"<机器列名>": "<新值>", ...}, ...}
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DEFAULT_USER_COLS = ("你已标(吸收/新增)",)


def merge_write(
    path: Path,
    updates: dict[str, dict[str, str]],
    user_columns: tuple[str, ...] = DEFAULT_USER_COLS,
    *,
    dry_run: bool = False,
    init_user_col: str | None = None,
) -> int:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    if init_user_col:
        if init_user_col in fields:
            print(f"[拒绝] 用户列 {init_user_col} 已存在，无需初始化")
            return 4
        fields.append(init_user_col)
        for r in rows:
            r[init_user_col] = ""
        user_columns = tuple(user_columns) + (init_user_col,)

    user_set = set(user_columns)
    # 约束 1：更新内容碰用户列 → 拒绝
    for uid, cols in updates.items():
        clash = user_set & set(cols)
        if clash:
            print(f"[拒绝] 单元 {uid} 的更新试图改用户列 {sorted(clash)}，整个写入取消")
            return 4

    by_id = {str(r.get("unit_id")): r for r in rows}
    changes: list[tuple[str, str, str, str]] = []  # (unit_id, col, old, new)
    preserved = 0
    for uid, cols in updates.items():
        row = by_id.get(uid)
        if row is None:
            print(f"[拒绝] 单元 {uid} 不在表中，整个写入取消")
            return 4
        for col, new in cols.items():
            if col not in fields:
                fields.append(col)
            old = row.get(col, "")
            if str(new) != old:
                changes.append((uid, col, old, str(new)))

    for r in rows:
        if any(str(r.get(c) or "").strip() for c in user_columns):
            preserved += 1

    print(f"用户列 {list(user_columns)}：保留 {preserved}/{len(rows)} 行（永不写入）")
    if not changes:
        print("机器列：无变更")
        return 0
    for uid, col, old, new in changes:
        print(f"  变更 {uid} [{col}]: {old[:30]!r} → {new[:30]!r}")

    if dry_run:
        print("(dry-run，未写入)")
        return 0
    # 用户列原样回写：新行 = 原行复制，再套用机器列变更
    for uid, cols in updates.items():
        by_id[uid].update({c: str(v) for c, v in cols.items()})
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError:
        print(f"[拒绝写入] {path}（Excel 占用？关掉再跑）——文件未被部分写入")
        return 3
    print(f"已写入 {path}（{len(changes)} 处机器列变更）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="tests/validation/2026-09-17-p2-calibration-b1-预填.csv")
    ap.add_argument("--updates", required=True, help="JSON 文件：{unit_id: {机器列: 新值}}")
    ap.add_argument("--user-cols", nargs="*", default=list(DEFAULT_USER_COLS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--init-user-col", default=None, help="初始化一个新的空用户列（并纳入保护）")
    args = ap.parse_args()
    updates = json.loads(Path(args.updates).read_text(encoding="utf-8"))
    return merge_write(
        Path(args.file),
        updates,
        tuple(args.user_cols),
        dry_run=args.dry_run,
        init_user_col=args.init_user_col,
    )


if __name__ == "__main__":
    raise SystemExit(main())
