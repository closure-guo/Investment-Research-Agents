#!/usr/bin/env python3
"""因子研发状态采集器（delta add-factor-research-monitor T1）。

只读观测 WSL `rdagent` distro 上的持续因子研发看护（RD-Agent fin_factor 无界循环），
周期把状态写为 JSON 快照至宿主可见路径（经 /mnt/d 桥，后端容器挂载 ./data 读取）。

契约（openspec delta factor-research-monitor「状态快照采集契约」）：
- 周期 ≤60s；写入原子（tmp + os.replace）；只读观测（不改研发进程状态）；
- 快照含 collected_at / collected_at_epoch / supervisor / current_session /
  sessions / totals；未知字段如实置空，MUST NOT 编造进度数字；
- 看护停止后由 stop 流程以 ``--once`` 写末次快照（running=false）后退出。

运行（distro 内，root）：
    python3 collect_factor_status.py --loop            # 守护（30s 周期）
    python3 collect_factor_status.py --once            # 单次（停止流程末次快照）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_WORKDIR = "/root/rdagent-work"
DEFAULT_OUT = "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/status.json"
DEFAULT_INTERVAL_S = 30
# 日志尾/进度窗读取上限（长会话成本有界：全文件不重扫，增量区另有状态文件）
TAIL_BYTES = 64 * 1024
PROGRESS_WINDOW_BYTES = 1024 * 1024
INCREMENT_CHUNK_BYTES = 16 * 1024 * 1024
LOG_TAIL_LINES = 25
LOG_TAIL_LINE_CHARS = 220
SESSIONS_HISTORY_LIMIT = 50

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - 无 tzdata 的极端环境回退本地时区
    _TZ = datetime.now().astimezone().tzinfo

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PROGRESS_RE = re.compile(r"loop_index=(\d+),\s*step_index=(\d+),\s*step_name=(\w+)")
_TQDM_RE = re.compile(r"(\d+)/(\d+)\s*\[(?:(\d+):)?(\d+):(\d+)<(?:(\d+):)?(\d+):(\d+)")
_FACTOR_NAME_RE = re.compile(r"factor_name:\s*([A-Za-z][A-Za-z0-9_]{0,63})")
# 因子块（harvest 桥输入面）：name/description/formulation 三元组连续行
_FACTOR_BLOCK_RE = re.compile(
    r"factor_name:\s*([A-Za-z][A-Za-z0-9_]{0,63})[^\n]*\n"
    r"factor_description:\s*([^\n]*)\n"
    r"factor_formulation:\s*([^\n]*)\n"
    r"variables:"
)
_SUPERVISOR_START_RE = re.compile(r"^\[supervisor\] started at (\S+) pid (\d+)")
_SUPERVISOR_END_RE = re.compile(
    r"^\[supervisor\] session ended after (\d+)s \(log: (.+?)\) at (\S+)"
)
_SESSION_FILE_RE = re.compile(r"^session-(\d{8})-(\d{6})\.log$")
# 增量解析重叠窗（防因子块跨 chunk 边界被截断；去重使重复解析无害）
_INCREMENT_OVERLAP_BYTES = 4096


# ── 纯解析函数（单测面）────────────────────────────────────────────────────


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_progress(text: str) -> dict | None:
    """末次 ``loop_index=…, step_index=…, step_name=…`` 标记 + 同行 tqdm 数字。

    无标记 → None（如实未知）；tqdm 数字缺失 → 对应字段 None（不编造）。
    """
    matches = list(_PROGRESS_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    result: dict = {
        "loop_index": int(match.group(1)),
        "step_index": int(match.group(2)),
        "step_name": match.group(3),
        "steps_total": None,
        "step_elapsed_s": None,
        "step_eta_s": None,
    }
    # tqdm 数字在标记同一行且位于其前（窗口向前取最近一次）
    window = text[max(0, match.start() - 400) : match.start()]
    tqdm_matches = list(_TQDM_RE.finditer(window))
    if tqdm_matches:
        tqdm = tqdm_matches[-1]
        result["steps_total"] = int(tqdm.group(2))
        result["step_elapsed_s"] = (
            int(tqdm.group(3) or 0) * 3600 + int(tqdm.group(4)) * 60 + int(tqdm.group(5))
        )
        result["step_eta_s"] = (
            int(tqdm.group(6) or 0) * 3600 + int(tqdm.group(7)) * 60 + int(tqdm.group(8))
        )
    return result


def extract_factor_names(text: str) -> list[str]:
    """``factor_name: X`` 按出现顺序去重（仅 ASCII 词字符，防 ANSI/标点粘连）。"""
    names: list[str] = []
    seen: set[str] = set()
    for match in _FACTOR_NAME_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def extract_factor_blocks(text: str) -> list[dict]:
    """因子块（name/description/formulation 三元组）按出现顺序去重抽取。

    harvest 桥的输入面（``candidates.json``）：半块（缺 description/formulation）
    不产出——MUST NOT 以残缺元数据冒充；ANSI 转义先清理再匹配。
    """
    blocks: list[dict] = []
    seen: set[str] = set()
    for match in _FACTOR_BLOCK_RE.finditer(strip_ansi(text)):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        blocks.append(
            {
                "name": name,
                "description": match.group(2).strip(),
                "formulation": match.group(3).strip(),
            }
        )
    return blocks


def parse_supervisor_log(text: str) -> dict:
    """看护事件：末次启动行 + 全部会话结束事件（log_file 取 basename 便于对账）。"""
    started_at: str | None = None
    pid: int | None = None
    sessions: list[dict] = []
    for line in text.splitlines():
        start = _SUPERVISOR_START_RE.match(line)
        if start:
            started_at, pid = start.group(1), int(start.group(2))
        end = _SUPERVISOR_END_RE.match(line)
        if end:
            sessions.append(
                {
                    "log_file": os.path.basename(end.group(2)),
                    "duration_s": int(end.group(1)),
                    "ended_at": end.group(3),
                }
            )
    return {"started_at": started_at, "pid": pid, "sessions": sessions}


def session_started_at(filename: str) -> str | None:
    """``session-YYYYmmdd-HHMMSS.log`` → ISO8601（+08:00，distro 本地时区）。"""
    match = _SESSION_FILE_RE.match(filename)
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=_TZ).isoformat()


# ── IO 辅助 ────────────────────────────────────────────────────────────────


def _read_text(path: Path, *, errors: str = "replace") -> str:
    try:
        return path.read_text(encoding="utf-8", errors=errors)
    except OSError:
        return ""


def _read_tail(path: Path, nbytes: int) -> str:
    """读文件尾部至多 nbytes 字节（越界时丢弃首个不完整行）。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > nbytes:
                fh.seek(size - nbytes)
                fh.readline()  # 丢弃截断行
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _rdagent_session_alive() -> bool:
    """distro 内扫描 /proc：存在 ``rdagent fin_factor`` 进程即为会话存活。"""
    proc = Path("/proc")
    if not proc.is_dir():  # pragma: no cover - 非 Linux 环境（测试注入 session_alive）
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"fin_factor" in cmdline and b"rdagent" in cmdline:
            return True
    return False


def atomic_write_json(path: Path, payload: dict) -> None:
    """原子写：同目录唯一临时文件 + os.replace（读取方任一时刻只见完整快照）。

    唯一 tmp 名（pid + 随机后缀）：代理守护与手动兜底 `--once` 可能并存写同一状态文件，
    固定名会在交叉时 replace 出残缺 JSON。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _normalized_tail_lines(text: str) -> list[str]:
    """日志尾：去 ANSI、按 \\r/\\n 切分、去空行、逐行截断，取最近 N 行。"""
    lines = [line.strip() for line in strip_ansi(text).replace("\r", "\n").splitlines()]
    lines = [line for line in lines if line]
    return [line[:LOG_TAIL_LINE_CHARS] for line in lines[-LOG_TAIL_LINES:]]


# ── 快照组装 ───────────────────────────────────────────────────────────────


def _workspace_totals(workdir: Path) -> tuple[int, int]:
    """(候选实现数, 任务目录数)：workspace 下含 factor.py 的任务数与总任务数。"""
    ws = workdir / "git_ignore_folder" / "RD-Agent_workspace"
    if not ws.is_dir():
        return 0, 0
    tasks = 0
    candidates = 0
    for child in ws.iterdir():
        if not child.is_dir():
            continue
        tasks += 1
        if (child / "factor.py").is_file():
            candidates += 1
    return candidates, tasks


def _load_state(state_path: Path) -> dict:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def _incremental_factors(log_path: Path, state: dict) -> tuple[list[str], list[dict], dict]:
    """增量提取因子名（会话内）+ 因子块（全局候选）：仅解析新增字节，会话切换重置。

    重叠窗（``_INCREMENT_OVERLAP_BYTES``）防因子块跨 chunk 边界截断；重复解析由
    去重兜底。状态文件损坏/缺失 → 从当前文件头重建（如实，不虚构）。
    """
    name = log_path.name
    if state.get("log_file") != name:
        state = {"log_file": name, "offset": 0, "factors": []}
    factors: list[str] = [f for f in state.get("factors", []) if isinstance(f, str)]
    offset = int(state.get("offset") or 0)
    try:
        size = log_path.stat().st_size
    except OSError:
        size = 0
    if offset > size:  # 文件被截断/替换 → 从头重建
        offset = 0
        factors = []
    blocks: list[dict] = []
    if offset < size:
        start = max(0, offset - _INCREMENT_OVERLAP_BYTES)
        with log_path.open("rb") as fh:
            fh.seek(start)
            chunk = fh.read(INCREMENT_CHUNK_BYTES)
        offset = start + len(chunk)
        text = chunk.decode("utf-8", errors="replace")
        seen = set(factors)
        for found in extract_factor_names(text):
            if found not in seen:
                seen.add(found)
                factors.append(found)
        blocks = extract_factor_blocks(text)
    return factors, blocks, {"log_file": name, "offset": offset, "factors": factors}


def _merge_candidates(
    path: Path, blocks: list[dict], *, session_name: str, now_epoch: float
) -> None:
    """因子块并入候选文件（全局去重按 name；跨会话/跨周期不丢不重）。"""
    existing = _load_state(path).get("candidates")
    candidates: list[dict] = (
        [c for c in existing if isinstance(c, dict)] if isinstance(existing, list) else []
    )
    known = {str(c.get("name")) for c in candidates}
    changed = False
    for block in blocks:
        if block["name"] in known:
            continue
        known.add(block["name"])
        candidates.append(
            {
                "name": block["name"],
                "description": block["description"],
                "formulation": block["formulation"],
                "session": session_name,
                "first_seen_at": datetime.fromtimestamp(now_epoch, tz=_TZ).isoformat(),
            }
        )
        changed = True
    if not changed:
        return
    atomic_write_json(
        path,
        {
            "generated_at": datetime.fromtimestamp(now_epoch, tz=_TZ).isoformat(),
            "candidates": candidates,
        },
    )


def collect_once(
    workdir: Path,
    out_path: Path,
    state_path: Path,
    *,
    candidates_out: Path | None = None,
    now_epoch: float | None = None,
    session_alive=None,
    agent_meta: dict | None = None,
) -> dict:
    """采集一次并原子落盘；返回快照（单字段失败不阻断整体，如实置空）。

    ``candidates_out``（缺省 = 状态文件同目录 ``candidates.json``）：harvest 桥的
    候选文件——因子块（name/description/formulation）全局去重追加。
    ``agent_meta``（delta add-factor-research-selfheal）：代理调和动作块（由代理传入；
    独立采集器不传 → 写 ``agent: null``，如实表示「无调和留痕」）。
    """
    now_epoch = time.time() if now_epoch is None else now_epoch
    alive_probe = session_alive or _rdagent_session_alive
    logdir = workdir / "factor_research_logs"

    supervisor = parse_supervisor_log(_read_text(logdir / "supervisor.log"))
    pid = None
    pidfile = workdir / "factor_research.pid"
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = supervisor["pid"]
    running = _process_alive(pid)

    session_files = sorted(logdir.glob("session-*.log"))
    ended_by_file = {event["log_file"]: event for event in supervisor["sessions"]}

    sessions: list[dict] = []
    for path in reversed(session_files[-SESSIONS_HISTORY_LIMIT:]):
        started = session_started_at(path.name)
        event = ended_by_file.get(path.name)
        duration = event["duration_s"] if event else None
        if duration is None and started is not None and path == session_files[-1]:
            started_epoch = datetime.fromisoformat(started).timestamp()
            duration = max(0, int(now_epoch - started_epoch))
        sessions.append(
            {
                "log_file": path.name,
                "started_at": started,
                "ended_at": event["ended_at"] if event else None,
                "duration_s": duration,
            }
        )

    current_session: dict | None = None
    new_state = _load_state(state_path)
    if session_files:
        latest = session_files[-1]
        factors, blocks, new_state = _incremental_factors(latest, new_state)
        if blocks:
            candidates_path = (
                candidates_out
                if candidates_out is not None
                else out_path.with_name("candidates.json")
            )
            _merge_candidates(
                candidates_path, blocks, session_name=latest.name, now_epoch=now_epoch
            )
        started = session_started_at(latest.name)
        duration = None
        if started is not None:
            duration = max(0, int(now_epoch - datetime.fromisoformat(started).timestamp()))
        progress = parse_progress(_read_tail(latest, PROGRESS_WINDOW_BYTES)) or {}
        current_session = {
            "log_file": latest.name,
            "started_at": started,
            "duration_s": duration,
            "alive": bool(running and alive_probe()),
            "loop_index": progress.get("loop_index"),
            "step_index": progress.get("step_index"),
            "step_name": progress.get("step_name"),
            "steps_total": progress.get("steps_total"),
            "step_elapsed_s": progress.get("step_elapsed_s"),
            "step_eta_s": progress.get("step_eta_s"),
            "factors": factors,
            "log_tail": _normalized_tail_lines(_read_tail(latest, TAIL_BYTES)),
        }

    candidates, tasks = _workspace_totals(workdir)
    snapshot = {
        "collected_at": datetime.fromtimestamp(now_epoch, tz=_TZ).isoformat(),
        "collected_at_epoch": int(now_epoch),
        "supervisor": {
            "running": running,
            "pid": pid,
            "started_at": supervisor["started_at"],
        },
        "current_session": current_session,
        "sessions": sessions,
        "totals": {
            "sessions": len(session_files),
            "candidates": candidates,
            "workspace_tasks": tasks,
        },
        "agent": agent_meta,
    }
    atomic_write_json(out_path, snapshot)
    with contextlib.suppress(OSError):
        # 原子写（delta add-factor-research-control：代理守护与手动兜底并存，防半读重建）；
        # 状态文件写失败不阻断快照（下轮重建，代价=重扫本会话）
        atomic_write_json(state_path, new_state)
    return snapshot


# ── 入口 ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="因子研发状态采集器（只读观测）")
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--state",
        default=None,
        help="增量状态文件（默认 <workdir>/factor_research_logs/.collector_state.json）",
    )
    parser.add_argument("--once", action="store_true", help="单次采集（停止流程写末次快照）")
    parser.add_argument("--loop", action="store_true", help="守护循环（默认）")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--pidfile", default=None, help="守护模式写入自身 pid（供 stop 脚本收尾）")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    out_path = Path(args.out)
    state_path = (
        Path(args.state)
        if args.state
        else workdir / "factor_research_logs" / ".collector_state.json"
    )

    if args.pidfile:
        Path(args.pidfile).write_text(str(os.getpid()), encoding="utf-8")

    if args.once:
        collect_once(workdir, out_path, state_path)
        return 0

    while True:
        try:
            snapshot = collect_once(workdir, out_path, state_path)
            print(
                f"[collector] {snapshot['collected_at']} "
                f"running={snapshot['supervisor']['running']} "
                f"candidates={snapshot['totals']['candidates']}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - 采集循环永不因单轮异常退出
            print(f"[collector] cycle failed: {exc!r}", file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
