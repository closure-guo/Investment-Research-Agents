"""共享热库护栏单测（harden-sqlite-availability task 1，issue #515）。

语义矩阵（#515 迁卷后）：宿主机 + 库在主检出 data/ → 一律拒绝（与容器是否在跑
无关——宿主 data/ 只是回滚副本）；容器内 / dry-run / 显式绕过 / 库在别处 → 放行。
"""

from __future__ import annotations

from pathlib import Path

from finance_agent.data import hotdb_guard


def _bind_db(tmp_path: Path) -> str:
    """形如主检出 data/ 下的库路径（以 tmp_path 伪作 repo root）。"""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    return str(d / "sessions.db")


def _outside_db(tmp_path: Path) -> str:
    return str(tmp_path / "elsewhere" / "t.db")


def test_host_repo_data_rejected(tmp_path):
    """#515 语义升级核心：宿主 + 主检出 data/ 库 → 拒绝；容器状态不再参与判定
    （旧三条件的「容器读方在跑」条件已删除，调用方无从传入）。"""
    reason = hotdb_guard.host_write_guard(
        db_path=_bind_db(tmp_path), in_container=False, repo_root=tmp_path
    )
    assert reason is not None
    assert "named volume" in reason
    assert "--allow-host-write" in reason


def test_container_execution_allowed(tmp_path):
    assert (
        hotdb_guard.host_write_guard(
            db_path=_bind_db(tmp_path), in_container=True, repo_root=tmp_path
        )
        is None
    )


def test_env_fallback_marks_container(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKFILL_IN_CONTAINER", "1")
    assert hotdb_guard.host_write_guard(db_path=_bind_db(tmp_path), repo_root=tmp_path) is None


def test_db_outside_repo_data_allowed(tmp_path):
    assert (
        hotdb_guard.host_write_guard(
            db_path=_outside_db(tmp_path), in_container=False, repo_root=tmp_path
        )
        is None
    )


def test_dry_run_and_allow_flag_bypass(tmp_path):
    db = _bind_db(tmp_path)
    assert (
        hotdb_guard.host_write_guard(
            db_path=db, in_container=False, dry_run=True, repo_root=tmp_path
        )
        is None
    )
    assert (
        hotdb_guard.host_write_guard(
            db_path=db, in_container=False, allow_host_write=True, repo_root=tmp_path
        )
        is None
    )


def test_hint_command_contains_script_and_args(tmp_path):
    reason = hotdb_guard.host_write_guard(
        db_path=_bind_db(tmp_path),
        in_container=False,
        repo_root=tmp_path,
        script_name="scripts/demo.py",
        argv=["--start", "2025-06-01", "--symbols", "920002"],
    )
    assert reason is not None
    assert (
        "docker compose exec -T backend python scripts/demo.py --start 2025-06-01 --symbols 920002"
        in reason
    )


def test_default_repo_root_uses_cwd(tmp_path, monkeypatch):
    """repo_root 缺省 = cwd：脚本不传时按当前目录的 data/ 判定（防呆兜底）。"""
    monkeypatch.chdir(tmp_path)
    reason = hotdb_guard.host_write_guard(db_path=_bind_db(tmp_path), in_container=False)
    assert reason is not None
