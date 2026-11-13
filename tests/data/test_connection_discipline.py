"""SQLite 连接纪律（harden-sqlite-connection-discipline，issue #515 残项①②）。

① WAL 收敛到 init：AST 静态扫描——``PRAGMA journal_mode=WAL`` 只允许出现在
   ``init_*`` / ``_ensure*`` / ``ensure_*`` 函数体或 ``data/cache.py``（单例
   ``__init__`` 天然一次）；每连接 helper 只设 ``busy_timeout``。
② 读路径去 DDL：``latest_snapshot_date`` / ``read_universe`` 缺库缺表按既有
   空语义返回，MUST NOT 建库建表（``?mode=ro`` 只读连接）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from finance_agent.data import snapshot

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "finance_agent"
_ALLOWED_FN_PREFIX = ("init_", "_ensure", "ensure_")
_ALLOWED_FILES = {"cache.py"}  # 单例 __init__，天然一次


def _wal_call_sites() -> list[tuple[Path, str]]:
    """返回 (文件, 外层函数名) 形式的全部 journal_mode=WAL 调用点。"""
    sites: list[tuple[Path, str]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "execute"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "journal_mode=WAL" in node.args[0].value
            ):
                continue
            # 向上找最近的 FunctionDef（ast 无 parent 指针，二次扫描定位）
            owner = _enclosing_function(tree, node)
            sites.append((path, owner or "<module>"))
    return sites


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if child is target:
                return node.name
    return None


class TestWalOnlyInInit:
    def test_wal_pragma_only_in_init_family(self):
        offenders = [
            (str(path.relative_to(SRC_ROOT)), fn)
            for path, fn in _wal_call_sites()
            if not fn.startswith(_ALLOWED_FN_PREFIX) and path.name not in _ALLOWED_FILES
        ]
        assert offenders == [], (
            "PRAGMA journal_mode=WAL 只允许出现在 init 家函数（连接纪律 "
            "harden-sqlite-connection-discipline）：每连接设置会让每次连接都 attach "
            "一次 -shm（incident 053 的放大器）。违规点："
            + ", ".join(f"{f}:{fn}" for f, fn in offenders)
        )


class TestSnapshotReadNoDDL:
    def test_latest_snapshot_date_missing_db_returns_none_without_creating(
        self, tmp_path: Path
    ):
        db = tmp_path / "absent.db"
        assert snapshot.latest_snapshot_date(db) is None
        assert not db.exists(), "读路径 MUST NOT 建库"

    def test_read_universe_missing_db_raises_empty_without_creating(self, tmp_path: Path):
        db = tmp_path / "absent.db"
        with pytest.raises(snapshot.SnapshotEmptyError):
            snapshot.read_universe("2026-01-01", db_path=db)
        assert not db.exists(), "读路径 MUST NOT 建库"

    def test_read_universe_missing_table_raises_empty(self, tmp_path: Path):
        """库文件存在但无表（如他模块建的同名库）→ 既有空语义，不建表。"""
        db = tmp_path / "empty.db"
        db.write_bytes(b"")
        with pytest.raises(snapshot.SnapshotEmptyError):
            snapshot.read_universe("2026-01-01", db_path=db)
