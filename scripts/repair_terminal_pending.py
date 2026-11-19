"""terminal_pending 标记修复工具（harden-sqlite-connection-discipline ④）。

终态落库重试耗尽时 ``stream_registry.publish_terminal`` 会在 SQLite 旁路写
``terminal_pending/<session_id>.json`` 标记；本工具在库恢复后逐个补落终态：

1. 读标记 → 会话 journal 已有终态（后续兜底发布成功过）→ 只修 status；
2. journal 无终态 → ``append_session_event`` 补写事件 + 按
   done→completed / interrupted→interrupted / error→failed 修 status；
3. 成功即删除标记；失败保留标记待下次（退出码 1）。

用法::

    uv run python scripts/repair_terminal_pending.py          # 全量修复
    uv run python scripts/repair_terminal_pending.py --dry-run # 只读报告
"""

from __future__ import annotations

import argparse
import json
import sys

from finance_agent import session_store

# 终态事件类型 → sessions.status 映射（与 api.py 状态流转同语义）
_STATUS_BY_EVENT = {"done": "completed", "interrupted": "interrupted", "error": "failed"}


def repair_one(session_id: str, event: dict, *, dry_run: bool = False) -> str:
    """修复单个标记；返回动作摘要（``repaired``/``status_only``/``dry_run``）。

    store 调用经模块属性（晚绑定）：测试可 monkeypatch，运行期行为不变。
    """
    existing = session_store.get_terminal_event(session_id)
    action = "repaired"
    if dry_run:
        action = "dry_run"
    else:
        if existing is None:
            payload = {k: v for k, v in event.items() if k != "seq"}
            session_store.append_session_event(session_id, payload)
        status = _STATUS_BY_EVENT.get(str(event.get("type")))
        if status is not None:
            session_store.update_session_status(session_id, status)
    return action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复 terminal_pending 旁路标记（补落终态）")
    parser.add_argument("--dry-run", action="store_true", help="只读报告，不写库不删标记")
    args = parser.parse_args(argv)

    from finance_agent.session_store import write_terminal_pending_marker

    markers = session_store.list_terminal_pending_markers()
    if not markers:
        print("no terminal_pending markers")
        return 0
    failures = 0
    for path in markers:
        session_id = path.stem
        if args.dry_run:
            # dry-run 只读：不 pop（不删标记）、不写库
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print(f"{session_id}: unreadable marker (dry-run)")
                failures += 1
                continue
            event = payload.get("event") or {}
            print(
                f"{session_id}: dry_run type={event.get('type')} marked_at={payload.get('marked_at')}"
            )
            continue
        payload = session_store.pop_terminal_pending_marker(session_id)
        if payload is None:
            print(f"{session_id}: unreadable marker, removed")
            continue
        event = payload.get("event") or {}
        try:
            action = repair_one(session_id, event, dry_run=False)
        except Exception as exc:  # noqa: BLE001 - 修复失败保留标记待下次
            failures += 1
            print(f"{session_id}: FAILED {type(exc).__name__}: {exc} (marker retained)")
            # 修复失败：把标记写回（pop 已删除），保留待下次
            write_terminal_pending_marker(session_id, event)
            continue
        print(
            f"{session_id}: {action} type={event.get('type')} marked_at={payload.get('marked_at')}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
