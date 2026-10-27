#!/usr/bin/env python3
"""因子研发代理（delta add-factor-research-control）：观测 + 控制合一，常驻。

职责（spec factor-research-monitor「看护控制通道」+「状态快照采集契约」改编点）：

- **观测**：周期调用 ``collect_factor_status.collect_once`` 写快照（采集路径只读，
  语义与旧采集器一致）；
- **控制**：周期读宿主控制文件（默认 ``data/factor_research/control.json``，经 /mnt/d 桥），
  按**期望状态调和**：``desired_running=true`` 且看护未运行 → 拉起（与旧 start 脚本同一实现）；
  ``false`` 且运行中 → 停止（与旧 stop 脚本逐字同语义：终止当前会话）；期望=实态 → 零动作；
  文件缺失/损坏/字段非法 → **不干预**（fail-safe，不猜期望态）；
- **独立存活**：看护停止后代理不退出——继续周期写 ``running=false`` 快照并受理控制
  （否则停机后快照陈旧、控制通道失联，开关无法再开启）；
- **串行**：flock 串行化「守护轮询」与「手动 reconcile-once」（脚本触发），不并发启停；
- 停止动作 MUST NOT 删除研发产物（workspace/候选/日志保持）。

运行（distro 内，root）：
    python3 factor_research_agent.py --loop            # 守护（默认；15s 周期）
    python3 factor_research_agent.py --reconcile-once  # 立即调和一次（脚本用）
    python3 factor_research_agent.py --ensure-daemon   # 确保守护存活（脚本用）
    python3 factor_research_agent.py --set-intent running|stopped   # 写期望态（脚本用）
    python3 factor_research_agent.py --prune           # 保留窗清理一次（看护会话后调用）
    python3 factor_research_agent.py --disk-check      # 磁盘水位检查并写暂停文件（脚本用）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import collect_factor_status as cfs

try:  # Linux（distro）；Windows 测试环境无 fcntl → 降级无锁（守护与手动不会并发）
    import fcntl
except ImportError:  # pragma: no cover - 仅测试环境路径
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger("factor_research_agent")

DEFAULT_WORKDIR = "/root/rdagent-work"
DEFAULT_CONTROL = "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json"
DEFAULT_LAUNCHER = "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/launcher.json"
DEFAULT_CONFIG_PATH = "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/pipeline.json"
DEFAULT_INTERVAL_S = 15
LAUNCH_SCRIPT = "/root/launch-fin-factor.sh"
PIDFILE_NAME = "factor_research.pid"
AGENT_PIDFILE_NAME = "factor_research_agent.pid"
LOCK_NAME = "factor_research_agent.lock"
SINGLETON_LOCK_NAME = "factor_research_agent.singleton.lock"
RESIDUAL_MARKER_NAME = "factor_research_stop_residual"
SUPERVISOR_SCRIPT_NAME = ".factor_research_supervisor.sh"
RESTART_STATE_NAME = ".agent_restart_state.json"
# 手动 reconcile-once 等锁上限（应急通道不被守护的长采集无限阻塞；超时降级继续并告警）
RECONCILE_LOCK_TIMEOUT_S = 8.0

# 保留窗与磁盘水位默认值（incident 051 防复发；与 api.py 同名常量字面同步——plan 全局约束）
DEFAULT_RETENTION_H = 48
DEFAULT_DISK_MIN_FREE_GB_GUEST = 30
DEFAULT_DISK_MIN_FREE_GB_HOST = 20
DEFAULT_HOST_MOUNT = "/mnt/d"
DISK_CHECK_BACKOFF_S = 300
AGENT_SCRIPT_PATH = "/root/factor_research_agent.py"  # distro 部署位（plan 部署面）
DISK_PAUSE_NAME = ".disk_pause.json"
RDAGENT_WORKSPACE_REL = Path("git_ignore_folder") / "RD-Agent_workspace"

# 研发节奏配置（delta add-factor-research-settings）：与后端
# FACTOR_RESEARCH_CONFIG_DEFAULTS / BOUNDS 字面同步。
DEFAULT_CONFIG_VALUES: dict[str, int] = {
    "snapshot_interval_s": DEFAULT_INTERVAL_S,
    "session_gap_s": 60,
    "short_session_backoff_s": 600,
    "short_session_threshold_s": 120,
}
CONFIG_BOUNDS: dict[str, tuple[int, int]] = {
    "snapshot_interval_s": (5, 120),
    "session_gap_s": (0, 3600),
    "short_session_backoff_s": (0, 7200),
    "short_session_threshold_s": (30, 3600),
}


class SingletonBusy(RuntimeError):  # noqa: N818 - 「单例被占」判定，与既有命名风格一致
    """已有代理守护持单例锁（不得再起第二个守护）。"""


# 看护脚本（旧 start 脚本 heredoc 逐字迁移；单来源在代理，脚本不再另写一份）。
# 占位符用 {{...}} 显式替换（bash 的 ${DUR} 等花括号与 str.format 冲突）。
SUPERVISOR_SCRIPT = """#!/usr/bin/env bash
# 研发看护：无界 fin_factor 循环 + 崩溃自动重启（由 factor_research_agent 写/管）。
set -u
WORKDIR={{WORKDIR}}
LOGDIR="$WORKDIR/factor_research_logs"
echo $$ > "$WORKDIR/factor_research.pid"
echo "[supervisor] started at $(date -Is) pid $$" >> "$LOGDIR/supervisor.log"
while true; do
  LOG="$LOGDIR/session-$(date +%Y%m%d-%H%M%S).log"
  START=$(date +%s)
  bash {{LAUNCH}} > "$LOG" 2>&1
  END=$(date +%s)
  DUR=$((END - START))
  echo "[supervisor] session ended after ${DUR}s (log: $LOG) at $(date -Is)" >> "$LOGDIR/supervisor.log"
  if [ "$DUR" -lt {{SHORT_THRESHOLD}} ]; then
    sleep {{SHORT_BACKOFF}}   # 短命会话（配额/网络故障）→ 退避
  else
    sleep {{SESSION_GAP}}
  fi
done
"""


def render_supervisor_script(workdir: str | Path, config: dict[str, int] | None = None) -> str:
    """看护脚本渲染（占位符替换；bash 花括号原样保留）。

    ``config`` 取节奏配置的生效值（缺省用 DEFAULT_CONFIG_VALUES）；会话间隔与退避
    在下次启动看护时生效（页面已明示）。
    """
    values = {**DEFAULT_CONFIG_VALUES, **(config or {})}
    return (
        SUPERVISOR_SCRIPT.replace("{{WORKDIR}}", str(workdir))
        .replace("{{LAUNCH}}", LAUNCH_SCRIPT)
        .replace("{{SHORT_THRESHOLD}}", str(values["short_session_threshold_s"]))
        .replace("{{SHORT_BACKOFF}}", str(values["short_session_backoff_s"]))
        .replace("{{SESSION_GAP}}", str(values["session_gap_s"]))
    )


# ── 期望态与控制文件 ──────────────────────────────────────────────────────


def read_desired(control_path: str | Path) -> tuple[bool | None, str | None, str | None]:
    """读控制文件期望态 → ``(desired_running, requested_at, request_id)``。

    缺失 / 不可解析 / ``desired_running`` 缺失或非布尔 → ``(None, None, None)``
    （fail-safe：调用方 MUST NOT 据 None 启停任何进程）。
    """
    try:
        data = json.loads(Path(control_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    desired = data.get("desired_running")
    if not isinstance(desired, bool):
        return None, None, None
    requested_at = data.get("requested_at")
    request_id = data.get("request_id")
    return (
        desired,
        requested_at if isinstance(requested_at, str) else None,
        request_id if isinstance(request_id, str) else None,
    )


def set_intent(control_path: str | Path, desired: bool) -> dict[str, Any]:
    """写期望态（原子；手动脚本与界面共用同一条意图源）。"""
    now = datetime.now().astimezone()
    payload = {
        "desired_running": desired,
        "requested_at": now.isoformat(),
        "requested_at_epoch": int(now.timestamp()),
        "request_id": f"manual-{int(now.timestamp())}",
    }
    cfs.atomic_write_json(Path(control_path), payload)
    return payload


# ── 研发节奏配置（add-factor-research-settings）──────────────────────────────


def read_config(config_path: str | Path, defaults: dict[str, int] | None = None) -> dict[str, Any]:
    """读节奏配置并 fail-safe 归一化：缺失/损坏/越界 → 默认值（不崩、不猜）。

    返回 ``{"values": {...}, "source": file|defaults|corrupt, "invalid_fields": [...],
    "updated_at": str|None}``。``defaults`` 可覆盖默认值（CLI ``--interval`` 用于
    snapshot_interval_s 的缺省）。
    """
    base = {**DEFAULT_CONFIG_VALUES, **(defaults or {})}
    path = Path(config_path)
    if not path.is_file():
        return {"values": base, "source": "defaults", "invalid_fields": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"values": base, "source": "corrupt", "invalid_fields": [], "updated_at": None}
    if not isinstance(data, dict):
        return {"values": base, "source": "corrupt", "invalid_fields": [], "updated_at": None}
    values = dict(base)
    invalid: list[str] = []
    for key, (low, high) in CONFIG_BOUNDS.items():
        if key not in data:
            continue
        raw = data.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and low <= raw <= high:
            values[key] = raw
        else:
            invalid.append(key)
    updated_at = data.get("updated_at")
    return {
        "values": values,
        "source": "file",
        "invalid_fields": invalid,
        "updated_at": updated_at if isinstance(updated_at, str) else None,
    }


# ── 重启请求（add-factor-research-settings）────────────────────────────────


def read_restart_request(control_path: str | Path) -> tuple[int | None, str | None]:
    """读控制文件的重启请求 → ``(restart_requested_at_epoch, restart_request_id)``。

    缺失/不可解析/字段非法 → ``(None, None)``（fail-safe：不猜、不执行）。
    """
    try:
        data = json.loads(Path(control_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    epoch = data.get("restart_requested_at_epoch")
    request_id = data.get("restart_request_id")
    valid_epoch = isinstance(epoch, int) and not isinstance(epoch, bool) and epoch > 0
    valid_id = isinstance(request_id, str) and bool(request_id)
    if not valid_epoch and not valid_id:
        return None, None
    return (epoch if valid_epoch else None, request_id if valid_id else None)


def restart_key(epoch: int | None, request_id: str | None) -> str | None:
    """去重键：优先 request_id；仅 epoch 时用 ``epoch:<n>``；全无 → None。"""
    if request_id:
        return request_id
    if epoch is not None:
        return f"epoch:{epoch}"
    return None


def _restart_state_file(workdir: str | Path) -> Path:
    return Path(workdir) / "factor_research_logs" / RESTART_STATE_NAME


def read_restart_state(workdir: str | Path) -> dict[str, Any] | None:
    """读已处理重启请求（代理重启后不重复执行）；缺失/损坏 → None。"""
    try:
        data = json.loads(_restart_state_file(workdir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def write_restart_state(
    workdir: str | Path,
    *,
    request_id: str | None,
    requested_at_epoch: int | None,
    result: str,
) -> dict[str, Any]:
    """持久化最近一次重启处理结果（原子写；供快照 agent.restart 回显）。"""
    now = datetime.now().astimezone()
    payload = {
        "last_restart_request_id": request_id,
        "last_restart_requested_at_epoch": requested_at_epoch,
        "last_restart_result": result,
        "handled_at": now.isoformat(),
        "handled_at_epoch": int(now.timestamp()),
    }
    _restart_state_file(workdir).parent.mkdir(parents=True, exist_ok=True)
    cfs.atomic_write_json(_restart_state_file(workdir), payload)
    return payload


def restart_summary(workdir: str | Path) -> dict[str, Any] | None:
    """快照 ``agent.restart`` 块（最近一次重启请求的终态结果）；无 → None。"""
    state = read_restart_state(workdir)
    if state is None:
        return None
    return {
        "request_id": state.get("last_restart_request_id"),
        "requested_at_epoch": state.get("last_restart_requested_at_epoch"),
        "result": state.get("last_restart_result"),
        "handled_at": state.get("handled_at"),
        "handled_at_epoch": state.get("handled_at_epoch"),
    }


# ── 保留窗清理（incident 051 防复发 1/4：workspace 快照磁盘撑爆）─────────────


def _int_env(name: str, default: int) -> int:
    """env 整数读取：缺失/非整数 → 默认值（fail-safe，不因配置崩）。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _file_blocks_bytes(st: os.stat_result) -> int:
    """单文件占用字节：``st_blocks*512``；无 st_blocks 的平台按 apparent size 512 对齐。"""
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:  # Windows 测试环境无 st_blocks → 以文件大小按 512 块对齐近似
        return ((st.st_size + 511) // 512) * 512
    return blocks * 512


def _latest_mtime_under(entry: Path) -> float | None:
    """目录树内最新文件 mtime（含子目录）；单文件 stat 失败 → 跳过该文件（容忍）。"""
    latest: float | None = None
    for root, _dirs, files in os.walk(entry):
        for name in files:
            try:
                mtime = (Path(root) / name).stat().st_mtime
            except OSError:
                continue
            if latest is None or mtime > latest:
                latest = mtime
    return latest


def _tree_files_blocks_bytes(entry: Path) -> int:
    """目录树内文件的 ``st_blocks*512`` 之和（目录本身不计）。"""
    total = 0
    for root, _dirs, files in os.walk(entry):
        for name in files:
            try:
                total += _file_blocks_bytes((Path(root) / name).stat())
            except OSError:
                continue
    return total


def prune_workspace(
    workdir: str | Path, retention_h: int, *, now_epoch: float | None = None
) -> dict[str, Any]:
    """会话产物保留窗清理（spec「会话产物保留窗清理」）。

    删除 ``<workdir>/git_ignore_folder/RD-Agent_workspace`` 下「目录内最新文件 mtime」
    **早于** ``now - retention_h*3600`` 的任务目录（恰好等于阈值不删；运行中会话的
    目录 mtime 必在保留窗内，天然不受影响）。整树遍历失败 → 记入 ``failed`` 不中断
    其余目录（spec「清理失败不致崩」）；workspace 根缺失 → 空结果（不视为错误）。

    返回 ``{"deleted": [目录名...], "failed": [{"path","error"}...], "kept": int,
    "freed_bytes": int}``；``freed_bytes`` 为已删目录内文件 st_blocks*512 之和
    （目录本身不计），``kept`` 为保留的任务目录数。
    """
    now = time.time() if now_epoch is None else now_epoch
    cutoff = now - retention_h * 3600
    workspace = Path(workdir) / RDAGENT_WORKSPACE_REL
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    kept = 0
    freed = 0
    if not workspace.is_dir():
        return {"deleted": deleted, "failed": failed, "kept": kept, "freed_bytes": freed}
    try:
        entries = sorted(workspace.iterdir())
    except OSError as exc:
        failed.append({"path": str(workspace), "error": str(exc)})
        return {"deleted": deleted, "failed": failed, "kept": kept, "freed_bytes": freed}
    for entry in entries:
        if not entry.is_dir():
            continue  # workspace 根下的散文件不属任务目录，不删
        try:
            latest = _latest_mtime_under(entry)
        except OSError as exc:
            failed.append({"path": str(entry), "error": str(exc)})
            continue
        if latest is None or latest >= cutoff:
            kept += 1  # 空目录无 mtime 证据 → 保留（不猜）
            continue
        try:
            freed_here = _tree_files_blocks_bytes(entry)
            shutil.rmtree(entry)
        except OSError as exc:
            failed.append({"path": str(entry), "error": str(exc)})
            continue
        freed += freed_here
        deleted.append(entry.name)
    return {"deleted": deleted, "failed": failed, "kept": kept, "freed_bytes": freed}


# ── 实态与启停动作（与旧脚本逐字同语义）────────────────────────────────────


def _alive(pid: int) -> bool:
    return cfs._process_alive(pid)  # noqa: PLC2701 - 进程存活判定单一来源（同 monitor 先例）


def supervisor_pid(workdir: str | Path) -> int | None:
    """看护 pid（pidfile 存在且进程存活）；无 pidfile / 进程已死 → None。"""
    try:
        pid = int((Path(workdir) / PIDFILE_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def _pid_is_agent(pid: int) -> bool:
    """pid 是否确为本代理进程（``/proc/<pid>/cmdline`` 校验，防 PID 复用假命中）。

    非 Linux（测试环境）无 /proc → 退回存活判定（信任 pidfile）。
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return True
    return "factor_research_agent" in cmdline


def agent_pid(workdir: str | Path) -> int | None:
    """代理守护 pid（pidfile 存在、进程存活且身份吻合）；无 → None。"""
    try:
        pid = int((Path(workdir) / AGENT_PIDFILE_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if (_alive(pid) and _pid_is_agent(pid)) else None


def start_supervisor(workdir: str | Path, config: dict[str, int] | None = None) -> bool:
    """拉起看护：写看护脚本 → 新会话拉起 → pidfile 存活校验（~2s 窗口，同旧脚本）。"""
    workdir = Path(workdir)
    (workdir / "factor_research_logs").mkdir(parents=True, exist_ok=True)
    script = workdir / SUPERVISOR_SCRIPT_NAME
    script.write_text(render_supervisor_script(workdir, config), encoding="utf-8")
    script.chmod(0o755)
    subprocess.Popen(  # noqa: S603 - 固定命令（本模块渲染的脚本），无外部输入
        ["bash", str(script)],  # noqa: S607 - bash 走 PATH 解析（distro 内），无外部输入
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(2)
    return supervisor_pid(workdir) is not None


def stop_commands(pid: int) -> list[list[str]]:
    """停止序列（旧 stop 脚本逐字同序）：子进程（当前会话）→ 看护 → fin_factor 兜底。"""
    return [
        ["pkill", "-P", str(pid)],
        ["kill", str(pid)],
        ["pkill", "-f", "fin_factor"],
    ]


def _run_quiet(cmd: list[str]) -> None:
    """执行辅助命令（失败容忍——旧脚本 ``|| true`` 语义）。"""
    with contextlib.suppress(OSError):  # 命令缺失等极端环境不阻断调和
        subprocess.run(  # noqa: S603 - 固定命令列表（stop_commands），无外部输入
            cmd,  # noqa: S607 - pkill/kill 走 PATH 解析（distro 内），无外部输入
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _fin_factor_present() -> bool:
    """distro 内是否仍有 fin_factor 进程（``pgrep -f``，旧脚本校验同款）。"""
    try:
        result = subprocess.run(  # noqa: S603 - 固定命令，无外部输入
            ["pgrep", "-f", "fin_factor"],  # noqa: S607 - pgrep 走 PATH 解析（distro 内）
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:  # pragma: no cover
        return False
    return result.returncode == 0


def _residual_marker(workdir: str | Path) -> Path:
    """停止残留标记文件（上次停止未清干净时落盘，驱动后续周期继续清扫）。"""
    return Path(workdir) / RESIDUAL_MARKER_NAME


def stop_supervisor(workdir: str | Path) -> bool:
    """停止看护（含当前会话）：kill 序列 + pidfile 清理 + 兜底校验。

    ``True`` = 收尾干净；``False`` = 有残留（记 WARN + 落残留标记——下次调和会继续
    清扫，避免「页面显示已关闭、进程还在烧 key」的假象；与旧脚本「WARN: fin_factor
    process still present」同语义并补上收敛路径）。MUST NOT 触碰 workspace / 候选
    文件 / 会话日志。
    """
    workdir = Path(workdir)
    pid = supervisor_pid(workdir)
    if pid is not None:
        for cmd in stop_commands(pid):
            _run_quiet(cmd)
    else:
        _run_quiet(["pkill", "-f", "fin_factor"])  # 兜底：残留会话（旧脚本同款）
    (workdir / PIDFILE_NAME).unlink(missing_ok=True)
    time.sleep(1)
    if _fin_factor_present():
        _residual_marker(workdir).write_text(str(int(time.time())), encoding="utf-8")
        logger.warning("停止后仍有 fin_factor 残留进程 → 落残留标记，后续周期继续清扫")
        return False
    _residual_marker(workdir).unlink(missing_ok=True)
    return True


# ── 调和与周期 ────────────────────────────────────────────────────────────


def _start_supervisor(workdir: str | Path, config: dict[str, int] | None) -> bool:
    """启动适配：无配置时保持单参调用（既有测试桩/旧调用面兼容）。"""
    if config is None:
        return start_supervisor(workdir)
    return start_supervisor(workdir, config)


def restart_supervisor(
    workdir: str | Path, config: dict[str, int] | None = None
) -> tuple[bool, str]:
    """重启看护：运行中先停止（失败即中止，MUST NOT 再启动第二个看护）→ 启动。

    返回 ``(ok, action)``；``action`` 取 ``started`` / ``start-failed`` / ``stop-failed``
    （与快照 ``agent.last_action`` 六值对齐）。
    """
    if supervisor_pid(workdir) is not None and not stop_supervisor(workdir):
        return False, "stop-failed"
    if _start_supervisor(workdir, config):
        return True, "started"
    return False, "start-failed"


def _handle_restart(
    workdir: str | Path,
    control_path: str | Path,
    config: dict[str, int] | None,
) -> str | None:
    """执行未处理的重启请求；无待处理请求 → None。

    去重键持久化在研发工作区（代理重启后不重复执行）；结果同样落盘供快照回显。
    """
    epoch, request_id = read_restart_request(control_path)
    key = restart_key(epoch, request_id)
    if key is None:
        return None
    state = read_restart_state(workdir)
    if state is not None and state.get("last_restart_request_id") == key:
        return None
    ok, action = restart_supervisor(workdir, config)
    write_restart_state(
        workdir,
        request_id=key,
        requested_at_epoch=epoch,
        result="restarted" if ok else "restart-failed",
    )
    return action


def reconcile_once(
    workdir: str | Path,
    control_path: str | Path,
    config_path: str | Path | None = None,
    config: dict[str, int] | None = None,
) -> str:
    """调和一次：期望态 vs 实态 → 动作。返回动作标签（日志/测试用）。

    ``no_intent``（无有效期望态，不干预）/ ``noop``（一致，幂等）/
    ``started`` / ``stopped`` / ``start-failed`` / ``stop-failed``。

    ``config_path`` 非空时先读节奏配置（fail-safe 归一化）；``config`` 可直接给生效值
    （``run_tick`` 复用同一次读取）。
    """
    desired, requested_at, request_id = read_desired(control_path)
    if desired is None:
        logger.info("控制文件缺失/不可解析 → 不干预（fail-safe）")
        return "no_intent"
    if config is None and config_path is not None:
        config = read_config(config_path)["values"]
    values = config
    running = supervisor_pid(workdir) is not None
    if desired is True:
        restart_action = _handle_restart(workdir, control_path, values)
        if restart_action is not None:
            logger.info("执行重启请求：%s", restart_action)
            return restart_action
    if desired == running:
        # 残留自愈：既往停止未清干净（有标记）→ 继续清扫。标记只在「停止动作验证失败」
        # 时落盘，故不会对操作者手动起的 fin_factor 会话盲杀。
        if desired is False and _residual_marker(workdir).exists():
            logger.info("检测到停止残留标记 → 继续清扫")
            return "stopped" if stop_supervisor(workdir) else "stop-failed"
        return "noop"
    logger.info(
        "调和：desired_running=%s 实态=%s（request_id=%s requested_at=%s）",
        desired,
        running,
        request_id,
        requested_at,
    )
    if desired:
        return "started" if _start_supervisor(workdir, values) else "start-failed"
    return "stopped" if stop_supervisor(workdir) else "stop-failed"


@contextmanager
def tick_lock(workdir: str | Path, *, timeout_s: float | None = None) -> Iterator[None]:
    """进程锁：守护轮询与手动 reconcile-once 串行（无 fcntl 环境降级放行，仅测试用）。

    ``timeout_s`` 非空 → 限时抢锁，超时记告警后**继续执行**（应急停止通道不被守护的
    长采集无限阻塞；守护自身轮询用默认无限等）。
    """
    if fcntl is None:  # pragma: no cover - 仅测试环境（distro 恒有 fcntl）
        yield
        return
    lock_path = Path(workdir) / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        if timeout_s is None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        logger.warning(
                            "等锁超时 %.0fs（守护可能卡在采集）→ 继续执行以保应急通道", timeout_s
                        )
                        break
                    time.sleep(0.5)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def singleton_lock(workdir: str | Path) -> Iterator[None]:
    """守护单例锁：进程生命周期持有（重复拉起 → ``SingletonBusy``）。

    与 ``tick_lock`` 分属不同锁文件（同文件二次 flock 会与自身互斥）；无 fcntl
    （测试环境）降级放行。
    """
    if fcntl is None:  # pragma: no cover - 仅测试环境
        yield
        return
    lock_path = Path(workdir) / SINGLETON_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SingletonBusy(f"单例锁被占：{lock_path}") from exc
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fh, fcntl.LOCK_UN)


_ACTION_ERRORS = {
    "start-failed": "看护未在启动窗口内就位（检查 launch 脚本与环境）",
    "stop-failed": "看护进程未在停止窗口内退出（已落残留标记，后续周期继续清扫）",
}


def _agent_meta(
    action: str,
    config: dict[str, Any] | None = None,
    restart: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """快照 agent 块：最近一次调和动作 + 失败摘要 + 生效配置 + 最近重启结果。"""
    at = now or datetime.now().astimezone()
    meta: dict[str, Any] = {
        "last_action": action,
        "last_action_at": at.isoformat(),
        "last_action_epoch": int(at.timestamp()),
        "last_error": _ACTION_ERRORS.get(action),
    }
    if config is not None:
        meta["config"] = config
    # 无重启记录时如实为 null（快照契约 agent.restart: {...} | null）
    meta["restart"] = restart
    return meta


def run_tick(
    workdir: str | Path,
    control_path: str | Path,
    out_path: str | Path,
    state_path: str | Path,
    config_path: str | Path | None = None,
    config_defaults: dict[str, int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """一次完整周期：锁内「先调和后采集」（快照即刻反映动作结果）。

    ``config_path`` 非空 → 本周期读一次节奏配置，调和与快照上报复用同一份生效值
    （``agent.config``）；缺省 → 默认值（测试/旧调用面）。``config_defaults`` 覆盖
    配置文件缺省（CLI ``--interval``）。
    """
    config = (
        read_config(config_path, defaults=config_defaults)
        if config_path is not None
        else {
            "values": {**DEFAULT_CONFIG_VALUES, **(config_defaults or {})},
            "source": "defaults",
            "invalid_fields": [],
            "updated_at": None,
        }
    )
    with tick_lock(workdir):
        if config_path is None:
            action = reconcile_once(workdir, control_path)
        else:
            action = reconcile_once(
                workdir, control_path, config_path=config_path, config=config["values"]
            )
        snapshot = cfs.collect_once(
            Path(workdir),
            Path(out_path),
            Path(state_path),
            agent_meta=_agent_meta(action, config=config, restart=restart_summary(workdir)),
        )
    return action, snapshot


def ensure_daemon(
    workdir: str | Path,
    control_path: str | Path,
    out_path: str | Path,
    state_path: str | Path,
    interval: int,
    config_path: str | Path | None = None,
) -> int | None:
    """确保代理守护存活：已在跑 → 返回 pid；否则新会话拉起并校验。失败 → None。"""
    workdir = Path(workdir)
    pid = agent_pid(workdir)
    if pid is not None:
        return pid
    logdir = workdir / "factor_research_logs"
    logdir.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--loop",
        "--workdir",
        str(workdir),
        "--control",
        str(control_path),
        "--out",
        str(out_path),
        "--state",
        str(state_path),
        "--interval",
        str(interval),
    ]
    if config_path is not None:
        args.extend(["--config", str(config_path)])
    with open(logdir / "agent.log", "ab") as log:
        subprocess.Popen(  # noqa: S603 - 固定命令（本文件自身 + 常量参数），无外部输入
            args,  # noqa: S607 - sys.executable 为绝对路径
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    time.sleep(1.5)
    return agent_pid(workdir)


def launcher_tick(
    workdir: str | Path,
    control_path: str | Path,
    out_path: str | Path,
    state_path: str | Path,
    launcher_path: str | Path,
    interval: int = DEFAULT_INTERVAL_S,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """启动器节拍：确保代理守护存活 + 原子写心跳。

    MUST NOT 写控制文件 / MUST NOT 直接启停看护（启动器只证明「手在场」，不表达意图）。
    心跳写失败不影响已完成的代理存活保证（调用方按返回码如实上报）。
    """
    pid = ensure_daemon(workdir, control_path, out_path, state_path, interval, config_path)
    snapshot_age_s: int | None = None
    try:
        snap = json.loads(Path(out_path).read_text(encoding="utf-8"))
        epoch = snap.get("collected_at_epoch")
        if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
            snapshot_age_s = max(0, int(time.time() - float(epoch)))
    except (OSError, ValueError, TypeError):
        snapshot_age_s = None
    now = datetime.now().astimezone()
    payload = {
        "checked_at": now.isoformat(),
        "checked_at_epoch": int(now.timestamp()),
        "agent_pid": pid,
        "agent_ok": pid is not None,
        "snapshot_age_s": snapshot_age_s,
        "detail": None
        if pid is not None
        else "代理守护无法拉起（见 factor_research_logs/agent.log）",
    }
    cfs.atomic_write_json(Path(launcher_path), payload)
    return payload


# ── 入口 ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="因子研发代理（观测 + 控制；delta add-factor-research-control）"
    )
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument(
        "--control",
        default=os.environ.get("FACTOR_RESEARCH_CONTROL_PATH") or DEFAULT_CONTROL,
        help="控制文件路径（默认取 env FACTOR_RESEARCH_CONTROL_PATH 或标准宿主桥路径）",
    )
    parser.add_argument(
        "--out",
        default=os.environ.get("FACTOR_RESEARCH_STATUS_PATH") or cfs.DEFAULT_OUT,
        help="状态快照路径（默认取 env FACTOR_RESEARCH_STATUS_PATH 或标准宿主桥路径）",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="增量状态文件（默认 <workdir>/factor_research_logs/.collector_state.json）",
    )
    parser.add_argument(
        "--launcher",
        default=os.environ.get("FACTOR_RESEARCH_LAUNCHER_PATH") or DEFAULT_LAUNCHER,
        help="启动器心跳路径（默认取 env FACTOR_RESEARCH_LAUNCHER_PATH 或标准宿主桥路径）",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("FACTOR_RESEARCH_CONFIG_PATH") or DEFAULT_CONFIG_PATH,
        help="节奏配置文件路径（默认取 env FACTOR_RESEARCH_CONFIG_PATH 或标准宿主桥路径）",
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--retention-h",
        type=int,
        default=_int_env("FACTOR_WORKSPACE_RETENTION_H", DEFAULT_RETENTION_H),
        help="保留窗小时数（默认取 env FACTOR_WORKSPACE_RETENTION_H，默认 48）",
    )
    parser.add_argument(
        "--pidfile",
        default=None,
        help="守护模式写入自身 pid（默认 <workdir>/factor_research_agent.pid）",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--loop", action="store_true", help="守护循环（默认）")
    modes.add_argument("--reconcile-once", action="store_true", help="立即调和一次（脚本用）")
    modes.add_argument("--ensure-daemon", action="store_true", help="确保守护存活（脚本用）")
    modes.add_argument(
        "--launcher-tick", action="store_true", help="启动器节拍：确保守护 + 写心跳（脚本用）"
    )
    modes.add_argument("--set-intent", choices=("running", "stopped"), help="写期望态（脚本用）")
    modes.add_argument(
        "--prune", action="store_true", help="保留窗清理一次（看护在会话结束后调用）"
    )
    modes.add_argument(
        "--disk-check", action="store_true", help="磁盘水位检查并写暂停文件（看护在拉起会话前调用）"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workdir = Path(args.workdir)
    control_path = Path(args.control)
    out_path = Path(args.out)
    config_path = Path(args.config)
    state_path = (
        Path(args.state)
        if args.state
        else workdir / "factor_research_logs" / ".collector_state.json"
    )

    if args.launcher_tick:
        try:
            payload = launcher_tick(
                workdir,
                control_path,
                out_path,
                state_path,
                Path(args.launcher),
                args.interval,
                config_path,
            )
        except OSError as exc:
            print(f"FAILED to write launcher heartbeat: {exc}", file=sys.stderr)
            return 1
        print(f"launcher tick: agent_ok={payload['agent_ok']} pid={payload['agent_pid']}")
        return 0 if payload["agent_ok"] else 1

    if args.prune:
        print(json.dumps(prune_workspace(workdir, args.retention_h), ensure_ascii=False))
        return 0

    if args.set_intent is not None:
        payload = set_intent(control_path, args.set_intent == "running")
        print(f"intent set: desired_running={payload['desired_running']} ({control_path})")
        return 0

    if args.ensure_daemon:
        pid = ensure_daemon(workdir, control_path, out_path, state_path, args.interval, config_path)
        if pid is None:
            print("FAILED to start agent daemon", file=sys.stderr)
            return 1
        print(f"agent daemon running pid {pid}")
        return 0

    if args.reconcile_once:
        cfg = read_config(
            config_path,
            defaults={**DEFAULT_CONFIG_VALUES, "snapshot_interval_s": args.interval},
        )
        with tick_lock(workdir, timeout_s=RECONCILE_LOCK_TIMEOUT_S):
            action = reconcile_once(workdir, control_path, config=cfg["values"])
        print(f"reconcile: {action}")
        return 1 if action in ("start-failed", "stop-failed") else 0

    # 守护循环（--loop 或缺省）：单例锁须持到进程结束（重复拉起 → SingletonBusy 拒绝）
    pidfile = Path(args.pidfile) if args.pidfile else workdir / AGENT_PIDFILE_NAME
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        with singleton_lock(workdir):
            pidfile.write_text(str(os.getpid()), encoding="utf-8")
            logger.info(
                "agent daemon started pid %s（interval 由 %s 决定）", os.getpid(), config_path
            )
            while True:
                try:
                    action, snapshot = run_tick(
                        workdir,
                        control_path,
                        out_path,
                        state_path,
                        config_path,
                        {**DEFAULT_CONFIG_VALUES, "snapshot_interval_s": args.interval},
                    )
                    print(
                        f"[agent] {snapshot['collected_at']} "
                        f"running={snapshot['supervisor']['running']} "
                        f"action={action} candidates={snapshot['totals']['candidates']}",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001 - 守护循环永不因单轮异常退出（采集器同先例）
                    print(f"[agent] tick failed: {exc!r}", file=sys.stderr, flush=True)
                # 采集间隔下一周期生效（配置缺失/损坏 → CLI --interval 缺省）
                interval = read_config(
                    config_path,
                    defaults={**DEFAULT_CONFIG_VALUES, "snapshot_interval_s": args.interval},
                )["values"]["snapshot_interval_s"]
                time.sleep(interval)
    except SingletonBusy as exc:
        print(f"FAILED: 已有代理守护在跑（{exc}）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
