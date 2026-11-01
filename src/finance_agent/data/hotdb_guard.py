"""热库宿主直跑护栏（issue #344 → #515）。

历史与语义升级：

- **issue #344 时代**（bind mount）：宿主机写 WAL 与容器读跨挂载争用 → 容器侧读库
  间歇全不可用（incident 043 同族）。护栏拒绝条件 = 非容器 + 库在主检出 ``data/`` +
  容器读方在跑。
- **#515 迁卷后**（named volume ``finance-agent_data``）：热库 SQLite 族整体在卷内，
  宿主 ``data/`` 从此只是**迁移时点的回滚副本**——宿主直读读到的是旧数据、宿主直写
  生产的永远是过期副本（写「成功」而生产不可见，静默失败家族）。容器是否在跑不再
  是放行条件：**凡宿主机进程解析到主检出 ``data/`` 下的库路径即拒绝**，与栈状态无关。
  ``--allow-host-write`` 显式绕过的含义相应变为「我知道这是过期副本，故意读写它」。

放行路径：容器内执行（``/.dockerenv`` / ``BACKFILL_IN_CONTAINER=1``）、``--dry-run``
零写入、``--allow-host-write`` 显式绕过、库路径在别处（如测试 tmp_path / 显式 env
覆写到非主检出目录）。

运维纪律不变：热库操作一律 ``docker compose exec -T backend python scripts/...``
（读写同侧，issue #344 实证配方）。
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Sequence
from pathlib import Path

_DOCKERENV = Path("/.dockerenv")


def running_in_container() -> bool:
    """容器内检测：Docker 建 ``/.dockerenv``；``BACKFILL_IN_CONTAINER=1`` 兜底。"""
    return _DOCKERENV.exists() or os.getenv("BACKFILL_IN_CONTAINER") == "1"


def host_write_guard(
    *,
    db_path: str | Path,
    allow_host_write: bool = False,
    dry_run: bool = False,
    in_container: bool | None = None,
    repo_root: Path | None = None,
    argv: Sequence[str] = (),
    script_name: str = "scripts/<script>.py",
) -> str | None:
    """宿主机直跑护栏：返回拒绝原因；None = 放行。

    拒绝条件（同时成立）：①非容器内直跑；②目标库落在主检出 ``data/`` 下（named
    volume 迁移后该目录只是回滚副本）。``--dry-run`` / ``--allow-host-write`` /
    容器内 / 库在别处 → 一律放行。``db_path`` 必填（调用脚本先解析自己的库路径再
    过护栏）；``repo_root`` 由调用脚本传自身 ``Path(__file__).resolve().parents[1]``
    （脚本可定位自己，不依赖包安装形态）；``script_name``/``argv`` 用于拼出可直接
    复制的容器内执行命令。
    """
    if dry_run or allow_host_write:
        return None
    if in_container is None:
        in_container = running_in_container()
    if in_container:
        return None
    resolved = Path(db_path).resolve()
    root = repo_root if repo_root is not None else Path.cwd()
    try:
        resolved.relative_to((root / "data").resolve())
    except ValueError:
        return None  # 库不在主检出 data/（非回滚副本面）→ 放行
    in_cmd = (
        f"docker compose exec -T backend python {script_name} "
        + " ".join(shlex.quote(str(a)) for a in argv)
    ).strip()
    return (
        "错误：拒绝在宿主机直跑——热库已迁 named volume（issue #515），宿主 data/ 只是"
        f"迁移时点的回滚副本：直读 = 读旧数据、直写 = 写进永远不可见的过期副本\n"
        f"（{resolved}）。\n"
        "请改用容器内执行（读写同侧）：\n"
        f"    {in_cmd}\n"
        "如确知在操作回滚副本（如重建宿主副本），加 --allow-host-write 显式绕过。"
    )
