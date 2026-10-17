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
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
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
DEFAULT_INTERVAL_S = 15
LAUNCH_SCRIPT = "/root/launch-fin-factor.sh"
PIDFILE_NAME = "factor_research.pid"
AGENT_PIDFILE_NAME = "factor_research_agent.pid"
LOCK_NAME = "factor_research_agent.lock"
SUPERVISOR_SCRIPT_NAME = ".factor_research_supervisor.sh"

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
  if [ "$DUR" -lt 120 ]; then
    sleep 600   # 短命会话（配额/网络故障）→ 退避 10 分钟
  else
    sleep 60
  fi
done
"""


def render_supervisor_script(workdir: str | Path) -> str:
    """看护脚本渲染（占位符替换；bash 花括号原样保留）。"""
    return SUPERVISOR_SCRIPT.replace("{{WORKDIR}}", str(workdir)).replace(
        "{{LAUNCH}}", LAUNCH_SCRIPT
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


def agent_pid(workdir: str | Path) -> int | None:
    """代理守护 pid（pidfile 存在且进程存活）；无 → None。"""
    try:
        pid = int((Path(workdir) / AGENT_PIDFILE_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def start_supervisor(workdir: str | Path) -> bool:
    """拉起看护：写看护脚本 → 新会话拉起 → pidfile 存活校验（~2s 窗口，同旧脚本）。"""
    workdir = Path(workdir)
    (workdir / "factor_research_logs").mkdir(parents=True, exist_ok=True)
    script = workdir / SUPERVISOR_SCRIPT_NAME
    script.write_text(render_supervisor_script(workdir), encoding="utf-8")
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


def stop_supervisor(workdir: str | Path) -> bool:
    """停止看护（含当前会话）：kill 序列 + pidfile 清理 + 兜底校验。

    ``True`` = 收尾干净（无残留 fin_factor 进程）；``False`` = 有残留（记录 WARN，
    与旧脚本「WARN: fin_factor process still present」同语义）。MUST NOT 触碰
    workspace / 候选文件 / 会话日志。
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
    return not _fin_factor_present()


# ── 调和与周期 ────────────────────────────────────────────────────────────


def reconcile_once(workdir: str | Path, control_path: str | Path) -> str:
    """调和一次：期望态 vs 实态 → 动作。返回动作标签（日志/测试用）。

    ``no_intent``（无有效期望态，不干预）/ ``noop``（一致，幂等）/
    ``started`` / ``stopped`` / ``start-failed`` / ``stop-failed``。
    """
    desired, requested_at, request_id = read_desired(control_path)
    if desired is None:
        logger.info("控制文件缺失/不可解析 → 不干预（fail-safe）")
        return "no_intent"
    running = supervisor_pid(workdir) is not None
    if desired == running:
        return "noop"
    logger.info(
        "调和：desired_running=%s 实态=%s（request_id=%s requested_at=%s）",
        desired,
        running,
        request_id,
        requested_at,
    )
    if desired:
        return "started" if start_supervisor(workdir) else "start-failed"
    return "stopped" if stop_supervisor(workdir) else "stop-failed"


@contextmanager
def tick_lock(workdir: str | Path) -> Iterator[None]:
    """进程锁：守护轮询与手动 reconcile-once 串行（无 fcntl 环境降级放行，仅测试用）。"""
    if fcntl is None:  # pragma: no cover - 仅测试环境（distro 恒有 fcntl）
        yield
        return
    lock_path = Path(workdir) / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run_tick(
    workdir: str | Path, control_path: str | Path, out_path: str | Path, state_path: str | Path
) -> tuple[str, dict[str, Any]]:
    """一次完整周期：锁内「先调和后采集」（快照即刻反映动作结果）。"""
    with tick_lock(workdir):
        action = reconcile_once(workdir, control_path)
        snapshot = cfs.collect_once(Path(workdir), Path(out_path), Path(state_path))
    return action, snapshot


def ensure_daemon(
    workdir: str | Path,
    control_path: str | Path,
    out_path: str | Path,
    state_path: str | Path,
    interval: int,
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
    with open(logdir / "agent.log", "ab") as log:
        subprocess.Popen(  # noqa: S603 - 固定命令（本文件自身 + 常量参数），无外部输入
            args,  # noqa: S607 - sys.executable 为绝对路径
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    time.sleep(1.5)
    return agent_pid(workdir)


# ── 入口 ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="因子研发代理（观测 + 控制；delta add-factor-research-control）"
    )
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--control", default=DEFAULT_CONTROL)
    parser.add_argument("--out", default=cfs.DEFAULT_OUT)
    parser.add_argument(
        "--state",
        default=None,
        help="增量状态文件（默认 <workdir>/factor_research_logs/.collector_state.json）",
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--pidfile",
        default=None,
        help="守护模式写入自身 pid（默认 <workdir>/factor_research_agent.pid）",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--loop", action="store_true", help="守护循环（默认）")
    modes.add_argument("--reconcile-once", action="store_true", help="立即调和一次（脚本用）")
    modes.add_argument("--ensure-daemon", action="store_true", help="确保守护存活（脚本用）")
    modes.add_argument("--set-intent", choices=("running", "stopped"), help="写期望态（脚本用）")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workdir = Path(args.workdir)
    control_path = Path(args.control)
    out_path = Path(args.out)
    state_path = (
        Path(args.state)
        if args.state
        else workdir / "factor_research_logs" / ".collector_state.json"
    )

    if args.set_intent is not None:
        payload = set_intent(control_path, args.set_intent == "running")
        print(f"intent set: desired_running={payload['desired_running']} ({control_path})")
        return 0

    if args.ensure_daemon:
        pid = ensure_daemon(workdir, control_path, out_path, state_path, args.interval)
        if pid is None:
            print("FAILED to start agent daemon", file=sys.stderr)
            return 1
        print(f"agent daemon running pid {pid}")
        return 0

    if args.reconcile_once:
        with tick_lock(workdir):
            action = reconcile_once(workdir, control_path)
        print(f"reconcile: {action}")
        return 1 if action in ("start-failed", "stop-failed") else 0

    # 守护循环（--loop 或缺省）
    pidfile = Path(args.pidfile) if args.pidfile else workdir / AGENT_PIDFILE_NAME
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    logger.info("agent daemon started pid %s（interval=%ss）", os.getpid(), args.interval)
    while True:
        try:
            action, snapshot = run_tick(workdir, control_path, out_path, state_path)
            print(
                f"[agent] {snapshot['collected_at']} "
                f"running={snapshot['supervisor']['running']} "
                f"action={action} candidates={snapshot['totals']['candidates']}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - 守护循环永不因单轮异常退出（采集器同先例）
            print(f"[agent] tick failed: {exc!r}", file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
