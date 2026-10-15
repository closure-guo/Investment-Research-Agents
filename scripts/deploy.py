"""部署脚本：主检出推进 + 容器 reload + 健康探针（OpenSpec add-deploy-script）。

三段式：
  1. **前置检查（只读，全过才继续）**；
  2. **拉取** —— `git pull --ff-only`（禁 merge 提交）；
  3. **探针** —— 后端 `/api/sessions` 200 + 前端 `/` 200；不健康 → 重启应用容器一次并复探。

四类实测坑的处置法（2026-10-14/15 四次手动部署）：

1. **WIP 重叠**：本地未提交改动与 incoming diff 有文件交集时 `git pull` 被拒
   （"Your local changes would be overwritten"）。默认中止并逐文件列出；
   `--stash` 时先 `git stash create`（只造对象、不动工作树/refs/stash）+ `git merge-tree
   --write-tree <stash> origin/main` 预判，无冲突才 stash → pull → pop（有冲突仍中止）。
2. **untracked 同名**：incoming 新增的文件本地已存在为 untracked → 挪为
   `<name>.deploy-bak-<ts>` 保留证据（对被 .gitignore 忽略的运行时库尤其重要——
   git 对 ignored 同名文件是**静默覆盖**，2026-10-14 实测）；报告含挪开清单。
3. **运行时库误跟踪**：应用写入的运行时文件（`data/` 前缀）被 pull 触碰时按类处置——
   untracked → 挪开留证；已跟踪+已修改 → `git checkout HEAD -- <path>` 丢弃（可再生成）并报告。
   **白名单式**：非 `data/` 前缀 + untracked 同名 → 中止人工裁决；非 `data/` 前缀 +
   已跟踪已修改 → 归 ③（默认中止，`--stash` 预判无冲突才放行）。
4. **reload 无验证**：pull 之后新代码是否生效过去靠人工 curl → 探针两端点，不健康时
   `docker compose restart backend frontend`（在仓库目录执行，幂等）一次后复探；
   仍不健康非零退出（MUST NOT 以「已 pull」冒充部署完成）。

用法（**推荐先跑 `--dry-run` 看计划**，全检查零变更）：

    uv run python scripts/deploy.py --dry-run     # 只检查 + 打印处置计划，零变更
    uv run python scripts/deploy.py               # 真实部署：前置检查 → pull → 探针
    uv run python scripts/deploy.py --stash       # 允许自动 stash→pull→pop（预判无冲突才做）
    uv run python scripts/deploy.py --repo PATH   # 缺省 = 当前工作目录的 git 根

退出码：0 成功；1 前置检查拒绝（含原因与人工指令）；2 执行阶段失败（拉取/文件处置/探针）。
运行时文件处置与 stash 不改变「可再生成」之外的内容：挪开留证、丢弃留报告。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAILED = 2

BACKEND_BASE_URL = "http://127.0.0.1:8000"
SESSIONS_URL = f"{BACKEND_BASE_URL}/api/sessions"
FRONTEND_URL = "http://localhost:5173/"
RUNTIME_PREFIX = "data/"

HTTP_TIMEOUT_S = 5.0
RESTART_WAIT_S = 5.0
RESTART_TIMEOUT_S = 300.0
BACKUP_SUFFIX = ".deploy-bak-"


# ── 纯函数：前置检查的分类与计划（便于单测） ──


@dataclass(frozen=True)
class RunningSessionsCheck:
    """① running 门禁结论。reachable=False 表示后端不可达（视为栈已停）。"""

    reachable: bool
    running: tuple[str, ...] = ()


def check_running_sessions(payload: object | None) -> RunningSessionsCheck:
    """判定 ① running 门禁（纯函数；payload=None 表示后端不可达 = 视为栈已停）。

    payload 形状兼容 `{"sessions": [...]}` 与裸列表；`running` 返回会话标识
    （session_id，附股票代码/名称便于人工辨认）。
    """
    if payload is None:
        return RunningSessionsCheck(reachable=False)
    sessions = payload.get("sessions") if isinstance(payload, dict) else payload
    if not isinstance(sessions, list):
        return RunningSessionsCheck(reachable=False)
    running: list[str] = []
    for index, session in enumerate(sessions):
        if not isinstance(session, dict):
            continue
        if str(session.get("status", "")).strip().lower() != "running":
            continue
        identifier = str(session.get("session_id") or session.get("id") or f"<session-{index}>")
        detail = " ".join(
            str(session[key]) for key in ("stock_code", "stock_name") if session.get(key)
        )
        running.append(f"{identifier} ({detail})" if detail else identifier)
    return RunningSessionsCheck(reachable=True, running=tuple(running))


def classify_wip_overlap(
    local_changed: Collection[str], incoming_changed: Collection[str]
) -> tuple[str, ...]:
    """③ WIP 交集分类（纯函数）：本地未提交改动 ∩ incoming diff，排序返回。

    空 = 可直拉；非空 = `git pull` 会被拒（默认中止，`--stash` 才走预判）。
    """
    return tuple(sorted(set(local_changed) & set(incoming_changed)))


@dataclass(frozen=True)
class RuntimeFilePlan:
    """④ 运行时文件处置计划（`data/` 白名单；非 data/ 的 untracked 同名 → blocked 中止）。"""

    move_aside: tuple[str, ...] = ()
    discard: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


def plan_runtime_file_actions(
    *,
    incoming: Collection[str],
    untracked: Collection[str],
    modified: Collection[str],
    runtime_prefix: str = RUNTIME_PREFIX,
) -> RuntimeFilePlan:
    """④ incoming 触碰且本地有状态的路径按类处置（纯函数）。

    - `data/` 前缀 + untracked → 挪开留证（`<name>.deploy-bak-<ts>`，执行时生成 ts）；
    - `data/` 前缀 + 已跟踪且已修改 → 丢弃（`git checkout HEAD -- <path>`）；
    - 非 `data/` 前缀 + untracked 同名 → blocked（中止人工裁决，白名单式默认拒绝）。
      非 `data/` 前缀 + 已跟踪且已修改不在此列：那是普通 WIP 交集，归 ③
      （默认中止、`--stash` 预判无冲突才放行）——否则 --stash 永无可用场景。
    运行时白名单收窄为 **`data/` 下的数据库文件**（名含 `.db`，覆盖 `-wal`/`-shm` 伴生）：
    `data/` 下仍可能存在不可再生的非库文件（如 `data/cohort/*.json`）——它们被误丢弃的
    代价高于部署失败，故按普通 WIP 处理（③/blocked），不进本白名单。
    """
    incoming_set = set(incoming)

    def _is_runtime_db(path: str) -> bool:
        return path.startswith(runtime_prefix) and ".db" in path.rsplit("/", 1)[-1]

    move_aside = tuple(sorted(p for p in set(untracked) & incoming_set if _is_runtime_db(p)))
    discard = tuple(sorted(p for p in set(modified) & incoming_set if _is_runtime_db(p)))
    blocked = tuple(sorted(p for p in set(untracked) & incoming_set if not _is_runtime_db(p)))
    return RuntimeFilePlan(move_aside=move_aside, discard=discard, blocked=blocked)


@dataclass(frozen=True)
class ProbeResult:
    backend: int | None
    frontend: int | None

    @property
    def healthy(self) -> bool:
        return self.backend == 200 and self.frontend == 200


def probe_endpoints(
    backend_probe: Callable[[], int | None], frontend_probe: Callable[[], int | None]
) -> ProbeResult:
    """探针编排（纯函数）：两侧探针返回 HTTP 状态码（None = 不可达），两 200 才健康。"""
    return ProbeResult(backend=backend_probe(), frontend=frontend_probe())


@dataclass(frozen=True)
class StashPrediction:
    clean: bool
    detail: str = ""


# ── git / 网络 / docker：进程与 IO 边界 ──


def _run_git(args: Sequence[str], repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - 固定 git 参数,无外部输入
        # core.quotepath=false: 非 ASCII 路径按 UTF-8 原样输出（默认会 C 转义加引号，
        # 直接拿去做文件/checkout 操作会找不到文件）
        ["git", "-c", "core.quotepath=false", *args],  # noqa: S607 - git 走 PATH 解析,无外部输入
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _rev_parse(repo: Path, rev: str) -> str | None:
    result = _run_git(["rev-parse", rev], repo)
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _split_lines(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "?"


def collect_incoming_untracked(repo: Path, incoming: Collection[str]) -> tuple[str, ...]:
    """incoming 命中且本地已存在、未被跟踪的路径（含被 .gitignore 忽略的运行时库）。

    逐路径查询而非全仓库 `ls-files --others`（主检出含 node_modules 等巨表）。
    被忽略的同名文件 git 会静默覆盖（实测），所以一并计入、挪开留证。
    """
    hits: list[str] = []
    for rel in sorted(incoming):
        if not (repo / rel).is_file():
            continue
        tracked = _run_git(["ls-files", "--cached", "--", rel], repo).stdout.strip()
        if tracked:
            continue
        hits.append(rel)
    return tuple(hits)


def predict_stash_merge(repo: Path, target_ref: str = "origin/main") -> StashPrediction:
    """③ `--stash` 预判：`git stash create` 造临时 commit（不写 refs/stash、不动工作树）
    + `git merge-tree --write-tree <stash> <target_ref>`；rc 0 = 无冲突。

    预判失败/工具不支持 → 不 clean（默认拒绝，宁可中止不赌）。
    """
    created = _run_git(["stash", "create"], repo)
    if created.returncode != 0:
        return StashPrediction(False, f"git stash create 失败: {_first_line(created.stderr)}")
    stash_sha = created.stdout.strip()
    if not stash_sha:
        return StashPrediction(True, "工作树无改动，无需 stash")
    merged = _run_git(["merge-tree", "--write-tree", stash_sha, target_ref], repo)
    if merged.returncode == 0:
        return StashPrediction(True)
    detail = (merged.stdout + "\n" + merged.stderr).strip()
    return StashPrediction(False, detail or "git merge-tree 报告冲突")


def fetch_sessions(url: str = SESSIONS_URL, timeout: float = HTTP_TIMEOUT_S) -> object | None:
    """GET /api/sessions → JSON；不可达 / 非 200 / 形状非法 → None（视为栈已停）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 固定本机回环地址
            if int(response.status) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):  # URLError / 超时 / JSON 解析失败
        return None
    if isinstance(payload, dict) and isinstance(payload.get("sessions"), list):
        return payload
    return payload if isinstance(payload, list) else None


def _http_status(url: str, timeout: float = HTTP_TIMEOUT_S) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 固定本机回环地址
            return int(response.status)
    except (OSError, ValueError):
        return None


def probe_backend() -> int | None:
    return _http_status(SESSIONS_URL)


def probe_frontend() -> int | None:
    return _http_status(FRONTEND_URL)


def restart_app_containers(repo: Path, timeout: float = RESTART_TIMEOUT_S) -> int:
    """`docker compose restart backend frontend`（在仓库目录执行；幂等，不动数据卷）。"""
    try:
        result = subprocess.run(  # noqa: S603 - 固定 docker 参数,无外部输入
            ["docker", "compose", "restart", "backend", "frontend"],  # noqa: S607 - docker 走 PATH
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return result.returncode


def timestamp_now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup_path(path: Path, stamp: str) -> Path:
    """`<name>.deploy-bak-<ts>`；同名已存在则追加 `-<n>`（不覆盖既有证据）。"""
    candidate = path.with_name(f"{path.name}{BACKUP_SUFFIX}{stamp}")
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}{BACKUP_SUFFIX}{stamp}-{index}")
        index += 1
    return candidate


def resolve_repo_root(path: Path | None) -> Path | None:
    """仓库根：显式路径（或其子目录）→ git 根；缺省 → 当前工作目录的 git 根。"""
    cwd = path if path is not None else Path.cwd()
    if not cwd.is_dir():
        return None
    result = _run_git(["rev-parse", "--show-toplevel"], cwd)
    root = result.stdout.strip()
    return Path(root) if result.returncode == 0 and root else None


# ── 三段式编排 ──


def deploy(
    repo: Path,
    *,
    dry_run: bool = False,
    allow_stash: bool = False,
    fetch_sessions: Callable[[], object | None] = fetch_sessions,
    backend_probe: Callable[[], int | None] = probe_backend,
    frontend_probe: Callable[[], int | None] = probe_frontend,
    restart: Callable[[Path], int] = restart_app_containers,
    post_restart_wait_s: float = RESTART_WAIT_S,
    now_stamp: Callable[[], str] = timestamp_now,
    out: Callable[[str], None] = print,
) -> int:
    """执行部署（网络与 docker 可注入 fake 便于测试）；返回退出码 0/1/2。"""

    def log(message: str) -> None:
        out(message)

    log(f"[deploy] 仓库: {repo}")

    # ① running 会话门禁（硬前置）：后端可达且存在 running → 中止，绝不推进/重启
    gate = check_running_sessions(fetch_sessions())
    if gate.reachable and gate.running:
        log("[deploy] ✗ 前置拒绝（① running 门禁）：存在运行中分析，仓库红线禁止推进/重启")
        for session in gate.running:
            log(f"[deploy]   - 会话 {session}")
        log("[deploy] 处置：等分析结束（或经用户确认）后再部署")
        return EXIT_REFUSED
    if not gate.reachable:
        log(
            "[deploy] ! 后端不可达（GET /api/sessions）：视为栈已停（无运行中会话），继续；探针阶段会复验"
        )
    else:
        log("[deploy] ① running 门禁: 通过（后端可达，无 running 会话）")

    # ② 版本比对：先 fetch；HEAD == origin/main → 已最新，跳过拉取只跑探针（幂等）
    fetched = _run_git(["fetch", "origin"], repo)
    if fetched.returncode != 0:
        log(f"[deploy] ✗ 前置拒绝（git fetch origin 失败）：{_first_line(fetched.stderr)}")
        return EXIT_REFUSED
    head = _rev_parse(repo, "HEAD")
    origin_main = _rev_parse(repo, "origin/main")
    if head is None or origin_main is None:
        log("[deploy] ✗ 前置拒绝（版本比对）：无法解析 HEAD / origin/main（远端缺 main？）")
        return EXIT_REFUSED
    up_to_date = head == origin_main

    incoming_diff = _run_git(["diff", "--name-only", "HEAD", "origin/main"], repo)
    local_diff = _run_git(["diff", "--name-only", "HEAD"], repo)
    if incoming_diff.returncode != 0 or local_diff.returncode != 0:
        log("[deploy] ✗ 前置拒绝（diff 快照失败）：无法计算 incoming / 本地改动文件清单")
        return EXIT_REFUSED
    incoming = _split_lines(incoming_diff.stdout)
    local_modified = _split_lines(local_diff.stdout)

    # ③ WIP 交集 + ④ 运行时文件处置（默认拒绝、白名单放行 data/）
    untracked_hits = collect_incoming_untracked(repo, incoming)
    plan = plan_runtime_file_actions(
        incoming=incoming, untracked=untracked_hits, modified=local_modified
    )
    wip = tuple(
        rel
        for rel in classify_wip_overlap(local_modified, incoming)
        if not rel.startswith(RUNTIME_PREFIX)  # data/ 前缀已由 ④ 处置
    )
    prediction: StashPrediction | None = None
    if wip and allow_stash:
        prediction = predict_stash_merge(repo, "origin/main")

    # 检查结论（dry-run 与真实执行都先完整报告）
    version_state = "已是最新" if up_to_date else f"incoming 文件 {len(incoming)} 个"
    wip_state = f"{len(wip)} 个文件" if wip else "无（可直拉）"
    log(
        f"[deploy] ② 版本: HEAD={_short(head)} origin/main={_short(origin_main)}（{version_state}）"
    )
    log(f"[deploy] ③ WIP 交集: {wip_state}（本地未提交改动 {len(local_modified)} 个文件）")
    log(
        f"[deploy] ④ 运行时文件: 挪开 {len(plan.move_aside)}"
        f" / 丢弃 {len(plan.discard)} / 阻断 {len(plan.blocked)}"
    )

    if plan.blocked:
        log(
            "[deploy] ✗ 前置拒绝（④ untracked 同名）：incoming 新增的文件本地已存在且不在 data/ 白名单"
        )
        for rel in plan.blocked:
            log(f"[deploy]   - {rel}")
        log("[deploy] 处置：人工裁决（挪开/删除/收编）这些文件后再部署")
        return EXIT_REFUSED
    if wip and not allow_stash:
        log("[deploy] ✗ 前置拒绝（③ WIP 交集）：以下未提交改动会被 git pull 覆盖")
        for rel in wip:
            log(f"[deploy]   - {rel}")
        log("[deploy] 处置：先提交/撤销这些改动，或加 --stash（预判无冲突才 stash→pull→pop）")
        return EXIT_REFUSED
    if prediction is not None and not prediction.clean:
        log("[deploy] ✗ 前置拒绝（③ --stash 预判）：merge-tree 判定 stash→pull→pop 有冲突")
        for line in prediction.detail.splitlines():
            log(f"[deploy]   {line}")
        return EXIT_REFUSED

    if dry_run:
        log("[deploy] ── dry-run：以下为处置计划（零变更，未执行任何动作）──")
        if up_to_date:
            log(f"[deploy] 拉取: 跳过（HEAD == origin/main {_short(head)}）")
        else:
            log(f"[deploy] 拉取: git pull --ff-only（{_short(head)} → {_short(origin_main)}）")
        for rel in plan.discard:
            log(f"[deploy] 丢弃运行时文件: {rel}（git checkout HEAD -- 丢弃，可再生成）")
        for rel in plan.move_aside:
            log(f"[deploy] 挪开运行时文件: {rel} → {rel}{BACKUP_SUFFIX}<ts>")
        if wip:
            log("[deploy] stash: 预判无冲突 → git stash push → pull → git stash pop")
        log(
            "[deploy] 探针: GET /api/sessions + GET / ；不健康则 "
            "docker compose restart backend frontend 后复探一次，仍不健康 → 退出码 2"
        )
        log("[deploy] dry-run 结束：零变更。确认计划后去掉 --dry-run 执行")
        return EXIT_OK

    # ④ 执行运行时文件处置（先于 stash：运行时库不入 stash、不参与 pop）
    stamp = now_stamp()
    for rel in plan.discard:
        discarded = _run_git(["checkout", "HEAD", "--", rel], repo)
        if discarded.returncode != 0:
            log(f"[deploy] ✗ 丢弃运行时文件失败: {rel}：{_first_line(discarded.stderr)}")
            return EXIT_FAILED
        log(f"[deploy] 丢弃运行时文件（可再生成，已报告留痕）: {rel}")
    for rel in plan.move_aside:
        source = repo / rel
        target = _backup_path(source, stamp)
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            log(f"[deploy] ✗ 挪开失败: {rel} → {target.name}：{exc}")
            return EXIT_FAILED
        log(f"[deploy] 挪开: {rel} → {target.name}（保留证据，pull 后人工处置）")

    # ③ 执行 stash（预判已确认无冲突）
    stashed = False
    if wip:
        pushed = _run_git(["stash", "push", "-m", f"deploy.py {stamp}"], repo)
        if pushed.returncode != 0:
            log(f"[deploy] ✗ stash 失败: {_first_line(pushed.stderr)}")
            return EXIT_FAILED
        stashed = True
        log(f"[deploy] stash: {len(wip)} 个文件的未提交改动已入 stash（预判无冲突）")

    # 拉取：git pull --ff-only（禁 merge 提交）
    if not up_to_date:
        pulled = _run_git(["pull", "--ff-only"], repo)
        if pulled.returncode != 0:
            log(f"[deploy] ✗ 拉取失败（git pull --ff-only）：{_first_line(pulled.stderr)}")
            if stashed:
                restored = _run_git(["stash", "pop"], repo)
                if restored.returncode == 0:
                    log("[deploy] 已回退 stash（本地改动已恢复）")
                else:
                    log("[deploy] ! stash 未 pop：改动仍在 git stash list 中，请手动恢复")
            return EXIT_FAILED
        log("[deploy] 拉取成功: git pull --ff-only")
    if stashed:
        popped = _run_git(["stash", "pop"], repo)
        if popped.returncode != 0:
            log(
                "[deploy] ✗ stash pop 失败：stash 未丢弃、改动仍在 git stash list 中，请手动处理冲突"
            )
            return EXIT_FAILED

    new_head = _rev_parse(repo, "HEAD") or head
    if up_to_date:
        log(f"[deploy] 提交: {_short(head)} → {_short(head)}（已是最新 origin/main，跳过拉取）")
    else:
        log(f"[deploy] 提交: {_short(head)} → {_short(new_head)}")

    # 探针：后端 /api/sessions 200 + 前端 / 200；不健康重启一次复探
    first = probe_endpoints(backend_probe, frontend_probe)
    log(
        f"[deploy] 探针第 1 轮: 后端 /api/sessions={_fmt_status(first.backend)}；"
        f"前端 /={_fmt_status(first.frontend)}"
    )
    if first.healthy:
        log("[deploy] ✓ 部署完成：两探针均 200")
        return EXIT_OK
    log("[deploy] 不健康 → docker compose restart backend frontend（在仓库目录执行）")
    restart_rc = restart(repo)
    log(f"[deploy] 重启返回码: {restart_rc}")
    if post_restart_wait_s > 0:
        time.sleep(post_restart_wait_s)
    second = probe_endpoints(backend_probe, frontend_probe)
    log(
        f"[deploy] 探针第 2 轮: 后端 /api/sessions={_fmt_status(second.backend)}；"
        f"前端 /={_fmt_status(second.frontend)}"
    )
    if second.healthy:
        log("[deploy] ✓ 部署完成（重启后恢复）")
        return EXIT_OK
    log("[deploy] ✗ 部署失败：重启后探针仍不健康（不以「已 pull」冒充部署完成），需人工排查")
    return EXIT_FAILED


def _fmt_status(code: int | None) -> str:
    return str(code) if code is not None else "不可达"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy.py",
        description="主检出推进 + 容器 reload + 健康探针（三段式；推荐先 --dry-run 看计划）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只跑前置检查并打印处置计划，零变更（含不 stash/不挪开/不 pull/不重启）——推荐先跑",
    )
    parser.add_argument(
        "--stash",
        action="store_true",
        help="WIP 与 incoming 有交集时，merge-tree 预判无冲突才 stash→pull→pop（有冲突仍中止）",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="目标仓库（缺省 = 当前工作目录的 git 根）",
    )
    args = parser.parse_args(argv)

    repo = resolve_repo_root(args.repo)
    if repo is None:
        print("✗ 无法确定 git 仓库根：--repo 不存在或不是 git 仓库", file=sys.stderr)
        return EXIT_REFUSED
    return deploy(repo, dry_run=args.dry_run, allow_stash=args.stash)


if __name__ == "__main__":
    sys.exit(main())
