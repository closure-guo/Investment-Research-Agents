# 研发看护自愈常驻与离线可开 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/factor-research` 的看护开关在 WSL 虚拟机重启、代理被杀、distro 被终止后仍能自愈并被操作，且离线时如实披露自动拉起时限。

**Architecture:** Windows 计划任务（登录时 + 每 2 分钟）调用 distro 入口 `ensure-factor-research.sh` → 代理 `--launcher-tick` 确保代理守护存活并写 `launcher.json` 心跳；代理快照新增 `agent` 块记录调和结果；后端以「快照新鲜 OR 心跳新鲜且 agent_ok」判定控制通道可用性（`control.honor`）；前端离线可开、位置取持久化意图、失败可读。

**Tech Stack:** Python 3.14（宿主测试）/ 3.11（distro）、FastAPI、pytest、React 18 + TypeScript、vitest、Playwright、bash、PowerShell 5.1+。

## Global Constraints

- **意图唯一写入面**：启动器 / `ensure-factor-research.sh` / `--launcher-tick` MUST NOT 写 `control.json`，也 MUST NOT 直接启停看护（启停只由代理按期望态调和）。
- **零命令面**：`PUT /api/factor-research/control` 仅接受 `{"desired_running": bool}`；MUST NOT 增加字符串/命令/任意参数。
- **阈值单源**：快照陈旧 180s；心跳新鲜 300s；离线受理 ETA 180s；启动器节拍 2 分钟。
- **原子写**：所有 JSON 写入用临时文件 + `os.replace`（复用 `collect_factor_status.atomic_write_json`）。
- **路径调用期读 env**：`FACTOR_RESEARCH_STATUS_PATH` / `FACTOR_RESEARCH_CONTROL_PATH` / `FACTOR_RESEARCH_LAUNCHER_PATH`。
- **失实即失败**：心跳/快照缺失或损坏 → 后端 `available=false` + reason，MUST NOT 以 500 或伪数据冒充。
- **distro 部署去 CRLF**：任何 `.sh`/`.py` 推入 distro 前必须 `tr -d '\r'`（Windows 检出为 CRLF）。
- **测试优先**：每个 Task 先写失败测试并运行确认失败，再实现，再跑绿，再提交。
- **工作区纪律**：在 `.worktrees/add-factor-research-selfheal` 的隔离分支实施；主检出可能被并发会话使用（`docs/project-workflow.md` §3 全管线）。
- **交付门禁**：交互类变更 → E2E 门禁全绿 + 真栈人工验证报告落 `tests/validation/` 后才能 sync/archive。

## File Structure Map

| 文件 | 职责 |
|---|---|
| `tests/scripts/spike/collect_factor_status.py` | 快照构建；新增可选 `agent_meta` 参数，快照新增 `agent` 块 |
| `tests/scripts/spike/factor_research_agent.py` | 代理；新增 `_agent_meta` / `run_tick` 传参 / `launcher_tick` / `--launcher-tick` |
| `tests/scripts/spike/ensure-factor-research.sh` | distro 启动器入口（薄包装，仅 `--launcher-tick`） |
| `scripts/register-factor-research-task.ps1` | Windows 计划任务幂等注册/卸载 |
| `src/finance_agent/api.py` | `launcher` 块 + `control.honor` + PUT 受理门 |
| `frontend/src/types.ts` | `FactorResearchAgent` / `FactorResearchLauncher` / control 扩展 |
| `frontend/src/pages/factors/FactorResearchPage.tsx` | 离线可开 / 位置取意图 / 失败可读 / 短命会话告警 |
| `tests/e2e/playwright/playwright.config.ts` | 注入 `FACTOR_RESEARCH_LAUNCHER_PATH` |
| `tests/e2e/playwright/tests/factor-research-monitor.spec.ts` | 新增离线可开 / 双离线 / 失败披露 / 短命告警用例 |
| `tests/scripts/spike/README.md` | 计划任务注册、心跳排障、回滚步骤 |

---

### Task 1: 快照 `agent` 块（采集器 + 代理）

**Files:**
- Modify: `tests/scripts/spike/collect_factor_status.py`（`collect_once` 签名 + 快照字典）
- Modify: `tests/scripts/spike/factor_research_agent.py`（`run_tick` + 新增 `_agent_meta`）
- Test: `tests/scripts/spike/test_collect_factor_status.py`
- Test: `tests/scripts/spike/test_factor_research_agent.py`

**Interfaces:**
- Produces: `collect_once(..., agent_meta: dict | None = None)`；快照字段 `agent`（`None` 或
  `{"last_action": str, "last_action_at": str, "last_error": str | None}`）。
- Produces: `factor_research_agent._agent_meta(action: str, now: datetime | None = None) -> dict`。

- [ ] **Step 1: 写失败测试（采集器）**

在 `tests/scripts/spike/test_collect_factor_status.py` 末尾追加：

```python
class TestAgentMeta:
    def test_agent_meta_passthrough(self, tmp_path):
        wd = _make_workdir(tmp_path, pid=os.getpid())
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        meta = {
            "last_action": "started",
            "last_action_at": "2026-10-19T10:00:00+08:00",
            "last_error": None,
        }
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0,
            session_alive=lambda: True, agent_meta=meta,
        )
        assert snap["agent"] == meta
        assert json.loads(out.read_text(encoding="utf-8"))["agent"] == meta

    def test_agent_null_for_standalone_collector(self, tmp_path):
        wd = _make_workdir(tmp_path, pid=os.getpid())
        out = tmp_path / "status.json"
        state = wd / "factor_research_logs" / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        assert snap["agent"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/scripts/spike/test_collect_factor_status.py -q -k AgentMeta`
Expected: FAIL（`TypeError: collect_once() got an unexpected keyword argument 'agent_meta'`）

- [ ] **Step 3: 实现采集器参数**

`collect_factor_status.py` 的 `collect_once` 签名加关键字参数：

```python
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
```

快照字典（`"totals"` 之后）加入：

```python
        "agent": agent_meta,
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/scripts/spike/test_collect_factor_status.py -q -k AgentMeta`
Expected: PASS

- [ ] **Step 5: 写失败测试（代理 run_tick）**

在 `tests/scripts/spike/test_factor_research_agent.py` 末尾追加：

```python
# ── agent 块：调和动作留痕（delta add-factor-research-selfheal）──


def test_run_tick_reports_action_in_snapshot(tmp_path, monkeypatch):
    workdir = _seed_workdir(tmp_path)
    control = _write_control(tmp_path, True)
    out = tmp_path / "status.json"
    state = workdir / "factor_research_logs" / ".collector_state.json"
    monkeypatch.setattr(agent, "reconcile_once", lambda w, c: "started")

    action, snap = agent.run_tick(workdir, control, out, state)

    assert action == "started"
    assert snap["agent"]["last_action"] == "started"
    assert isinstance(snap["agent"]["last_action_at"], str)
    assert snap["agent"]["last_error"] is None


def test_run_tick_start_failed_carries_error(tmp_path, monkeypatch):
    workdir = _seed_workdir(tmp_path)
    control = _write_control(tmp_path, True)
    out = tmp_path / "status.json"
    state = workdir / "factor_research_logs" / ".collector_state.json"
    monkeypatch.setattr(agent, "reconcile_once", lambda w, c: "start-failed")

    _, snap = agent.run_tick(workdir, control, out, state)

    assert snap["agent"]["last_action"] == "start-failed"
    assert snap["agent"]["last_error"]
```

- [ ] **Step 6: 运行确认失败**

Run: `uv run pytest tests/scripts/spike/test_factor_research_agent.py -q -k "reports_action or start_failed_carries"`
Expected: FAIL（`KeyError: 'agent'`）

- [ ] **Step 7: 实现代理元数据**

在 `factor_research_agent.py` 的 `run_tick` 之前加入：

```python
_ACTION_ERRORS = {
    "start-failed": "看护未在启动窗口内就位（检查 launch 脚本与环境）",
    "stop-failed": "看护进程未在停止窗口内退出（已落残留标记，后续周期继续清扫）",
}


def _agent_meta(action: str, now: datetime | None = None) -> dict[str, Any]:
    """快照 agent 块：最近一次调和动作 + 可判读失败摘要（失败原因由动作标签派生）。"""
    at = (now or datetime.now().astimezone()).isoformat()
    return {
        "last_action": action,
        "last_action_at": at,
        "last_error": _ACTION_ERRORS.get(action),
    }
```

`run_tick` 内改为：

```python
def run_tick(
    workdir: str | Path, control_path: str | Path, out_path: str | Path, state_path: str | Path
) -> tuple[str, dict[str, Any]]:
    """一次完整周期：锁内「先调和后采集」（快照即刻反映动作结果）。"""
    with tick_lock(workdir):
        action = reconcile_once(workdir, control_path)
        snapshot = cfs.collect_once(
            Path(workdir), Path(out_path), Path(state_path), agent_meta=_agent_meta(action)
        )
    return action, snapshot
```

- [ ] **Step 8: 运行确认通过**

Run: `uv run pytest tests/scripts/spike/test_factor_research_agent.py tests/scripts/spike/test_collect_factor_status.py -q`
Expected: PASS（全绿）

- [ ] **Step 9: 提交**

```bash
git add tests/scripts/spike/collect_factor_status.py tests/scripts/spike/factor_research_agent.py \
  tests/scripts/spike/test_collect_factor_status.py tests/scripts/spike/test_factor_research_agent.py
git commit -m "feat(factors): 快照新增 agent 调和动作块（失败可读）"
```

---

### Task 2: `--launcher-tick` 与心跳

**Files:**
- Modify: `tests/scripts/spike/factor_research_agent.py`
- Test: `tests/scripts/spike/test_factor_research_agent.py`

**Interfaces:**
- Consumes: `ensure_daemon(workdir, control_path, out_path, state_path, interval)`、`agent_pid(workdir)`。
- Produces: `launcher_tick(workdir, control_path, out_path, state_path, launcher_path, interval=DEFAULT_INTERVAL_S) -> dict`；
  CLI `--launcher-tick` / `--launcher <path>`；常量 `DEFAULT_LAUNCHER`。

- [ ] **Step 1: 写失败测试**

在 `tests/scripts/spike/test_factor_research_agent.py` 末尾追加：

```python
# ── launcher_tick：心跳 + 不动意图（delta add-factor-research-selfheal）──


def test_launcher_tick_writes_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ensure_daemon", lambda *a, **k: 4321)
    out = tmp_path / "status.json"
    out.write_text(json.dumps({"collected_at_epoch": int(time.time()) - 90}), encoding="utf-8")
    launcher = tmp_path / "launcher.json"

    payload = agent.launcher_tick(
        tmp_path, tmp_path / "control.json", out, tmp_path / "state.json", launcher, 15
    )

    assert payload["agent_pid"] == 4321
    assert payload["agent_ok"] is True
    assert 89 <= payload["snapshot_age_s"] <= 95
    on_disk = json.loads(launcher.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert isinstance(on_disk["checked_at_epoch"], int)
    assert not list(tmp_path.glob("*.tmp"))


def test_launcher_tick_never_writes_control_file(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ensure_daemon", lambda *a, **k: 1)
    control = tmp_path / "control.json"
    control.write_text('{"desired_running": false}', encoding="utf-8")
    before = control.read_bytes()

    agent.launcher_tick(
        tmp_path, control, tmp_path / "status.json", tmp_path / "state.json",
        tmp_path / "launcher.json", 15,
    )

    assert control.read_bytes() == before


def test_launcher_tick_reports_agent_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ensure_daemon", lambda *a, **k: None)

    payload = agent.launcher_tick(
        tmp_path, tmp_path / "control.json", tmp_path / "status.json",
        tmp_path / "state.json", tmp_path / "launcher.json", 15,
    )

    assert payload["agent_ok"] is False
    assert payload["agent_pid"] is None
    assert payload["detail"]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/scripts/spike/test_factor_research_agent.py -q -k launcher_tick`
Expected: FAIL（`AttributeError: module 'factor_research_agent' has no attribute 'launcher_tick'`）

- [ ] **Step 3: 实现 launcher_tick**

在 `factor_research_agent.py` 常量区加入：

```python
DEFAULT_LAUNCHER = (
    "/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/launcher.json"
)
```

在 `ensure_daemon` 之后加入：

```python
def launcher_tick(
    workdir: str | Path,
    control_path: str | Path,
    out_path: str | Path,
    state_path: str | Path,
    launcher_path: str | Path,
    interval: int = DEFAULT_INTERVAL_S,
) -> dict[str, Any]:
    """启动器节拍：确保代理守护存活 + 原子写心跳。

    MUST NOT 写控制文件 / MUST NOT 直接启停看护（启动器只证明「手在场」，不表达意图）。
    心跳写失败不影响已完成的代理存活保证（调用方按返回码如实上报）。
    """
    pid = ensure_daemon(workdir, control_path, out_path, state_path, interval)
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
        "detail": None if pid is not None else "代理守护无法拉起（见 factor_research_logs/agent.log）",
    }
    cfs.atomic_write_json(Path(launcher_path), payload)
    return payload
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/scripts/spike/test_factor_research_agent.py -q -k launcher_tick`
Expected: PASS

- [ ] **Step 5: 接入 CLI**

`main()` 的参数区加入（`--state` 之后）：

```python
    parser.add_argument(
        "--launcher",
        default=os.environ.get("FACTOR_RESEARCH_LAUNCHER_PATH") or DEFAULT_LAUNCHER,
        help="启动器心跳路径（默认取 env FACTOR_RESEARCH_LAUNCHER_PATH 或标准宿主桥路径）",
    )
```

互斥模式组加入：

```python
    modes.add_argument(
        "--launcher-tick", action="store_true", help="启动器节拍：确保守护 + 写心跳（脚本用）"
    )
```

模式分派（`if args.set_intent is not None:` 之前）加入：

```python
    if args.launcher_tick:
        try:
            payload = launcher_tick(
                workdir, control_path, out_path, state_path, Path(args.launcher), args.interval
            )
        except OSError as exc:
            print(f"FAILED to write launcher heartbeat: {exc}", file=sys.stderr)
            return 1
        print(f"launcher tick: agent_ok={payload['agent_ok']} pid={payload['agent_pid']}")
        return 0 if payload["agent_ok"] else 1
```

- [ ] **Step 6: 验证 CLI 帮助**

Run: `wsl.exe -d rdagent -u root -e python3 /root/factor_research_agent.py --help`（部署后）
或本地：`uv run python tests/scripts/spike/factor_research_agent.py --help`
Expected: 帮助含 `--launcher-tick` 与 `--launcher`

- [ ] **Step 7: 提交**

```bash
git add tests/scripts/spike/factor_research_agent.py tests/scripts/spike/test_factor_research_agent.py
git commit -m "feat(factors): 代理新增 --launcher-tick 启动器节拍与心跳"
```

---

### Task 3: distro 入口脚本 + Windows 计划任务注册脚本

**Files:**
- Create: `tests/scripts/spike/ensure-factor-research.sh`
- Create: `scripts/register-factor-research-task.ps1`
- Test: `tests/scripts/spike/test_launcher_scripts.py`

**Interfaces:**
- Produces: `ensure-factor-research.sh`（只调用 `--launcher-tick`，绝不 `--set-intent`）。
- Produces: `register-factor-research-task.ps1`（幂等注册/`-Unregister` 卸载）。

- [ ] **Step 1: 写失败测试（守护两条不变式）**

新建 `tests/scripts/spike/test_launcher_scripts.py`：

```python
"""启动器脚本守护测试（delta add-factor-research-selfheal）。

不变式：
- distro 入口只走 `--launcher-tick`（确保守护 + 心跳），MUST NOT 写意图/直接启停看护；
- Windows 注册脚本幂等（同名 -Force 覆盖）且可卸载。
"""

from pathlib import Path

SPIKE = Path(__file__).parent
REPO = SPIKE.parents[2]


def test_ensure_script_uses_launcher_tick_and_never_sets_intent():
    text = (SPIKE / "ensure-factor-research.sh").read_text(encoding="utf-8")
    assert "--launcher-tick" in text
    assert "--set-intent" not in text
    assert "--reconcile-once" not in text
    assert "/root/factor_research_agent.py" in text


def test_register_script_is_idempotent_and_targets_ensure_entry():
    text = (REPO / "scripts" / "register-factor-research-task.ps1").read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in text
    assert "-Force" in text
    assert "-Unregister" in text
    assert "ensure-factor-research.sh" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/scripts/spike/test_launcher_scripts.py -q`
Expected: FAIL（`FileNotFoundError`）

- [ ] **Step 3: 创建 distro 入口**

`tests/scripts/spike/ensure-factor-research.sh`：

```bash
#!/usr/bin/env bash
# 研发看护自愈启动器入口（delta add-factor-research-selfheal）。
#
# 幂等节拍：确保研发代理守护存活 + 写 launcher.json 心跳。
# MUST NOT 写控制文件（用户意图唯一写入面）与直接启停看护（只由代理按期望态调和）。
set -u

WORKDIR=/root/rdagent-work
AGENT=/root/factor_research_agent.py
CONTROL=/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json
LAUNCHER=${FACTOR_RESEARCH_LAUNCHER_PATH:-/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/launcher.json}

if [ ! -f "$AGENT" ]; then
  echo "FAILED: 研发代理未部署（$AGENT 不存在）" >&2
  exit 1
fi

exec python3 "$AGENT" --launcher-tick \
  --workdir "$WORKDIR" \
  --control "$CONTROL" \
  --launcher "$LAUNCHER"
```

- [ ] **Step 4: 创建 Windows 注册脚本**

`scripts/register-factor-research-task.ps1`：

```powershell
#Requires -Version 5.1
<#
.SYNOPSIS
  注册/卸载研发看护自愈启动器（delta add-factor-research-selfheal）。
.DESCRIPTION
  用户登录时 + 每 2 分钟调用 distro ensure 入口：确保代理守护存活并写心跳。
  幂等：重复执行更新同名任务；-Unregister 卸载。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/register-factor-research-task.ps1
  powershell -ExecutionPolicy Bypass -File scripts/register-factor-research-task.ps1 -Unregister
#>
param(
  [switch]$Unregister,
  [string]$TaskName = 'finance-agent-factor-research-launcher',
  [string]$Distro = 'rdagent',
  [int]$IntervalMinutes = 2
)

$ErrorActionPreference = 'Stop'

if ($Unregister) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "unregistered: $TaskName"
  } else {
    Write-Output "not found: $TaskName"
  }
  exit 0
}

$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
if (-not (Test-Path $wsl)) { throw "wsl.exe not found: $wsl" }

# powershell 隐藏窗口，避免每次触发闪控制台
$inner = "& '$wsl' -d $Distro -u root -e bash /root/ensure-factor-research.sh"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -WindowStyle Hidden -Command `"$inner`""

$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$triggerEvery = New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action `
  -Trigger @($triggerLogon, $triggerEvery) -Settings $settings `
  -Description '研发看护自愈启动器：每 2 分钟确保 rdagent 内代理存活并写心跳' -Force | Out-Null

$info = Get-ScheduledTask -TaskName $TaskName
Write-Output "registered: $($info.TaskName) state=$($info.State)"
```

- [ ] **Step 5: 运行守护测试**

Run: `uv run pytest tests/scripts/spike/test_launcher_scripts.py -q`
Expected: PASS

- [ ] **Step 6: 语法检查（不注册）**

Run: `powershell -NoProfile -Command "$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts/register-factor-research-task.ps1',[ref]$null,[ref]$null)"`
Expected: 无输出（解析通过）

Run: `bash -n tests/scripts/spike/ensure-factor-research.sh`
Expected: 无输出（语法通过）

- [ ] **Step 7: 提交**

```bash
git add tests/scripts/spike/ensure-factor-research.sh scripts/register-factor-research-task.ps1 \
  tests/scripts/spike/test_launcher_scripts.py
git commit -m "feat(factors): 启动器入口与 Windows 计划任务注册脚本"
```

---

### Task 4: 后端 `launcher` 块与 `control.honor`（GET）

**Files:**
- Modify: `src/finance_agent/api.py`（`FACTOR_RESEARCH_STALE_AFTER_S` 所在段）
- Modify: `tests/test_api_factor_research.py`

**Interfaces:**
- Produces: `launcher` 块（`available` / `reason` / 透传字段 / `age_seconds` / `fresh`）。
- Produces: `control.honor`（`live` / `launcher` / `unavailable`）与 `control.honor_eta_seconds`（`0` / `180` / `null`）。

- [ ] **Step 1: 更新测试夹具与期望值（先让新契约失败）**

`tests/test_api_factor_research.py` 顶部替换：

```python
_NO_CONTROL = {
    "desired_running": None,
    "requested_at": None,
    "pending": False,
    "honor": "unavailable",
    "honor_eta_seconds": None,
}
_NO_LAUNCHER = {"available": False, "reason": "missing"}
```

新增 autouse fixture：

```python
@pytest.fixture(autouse=True)
def _launcher_path(tmp_path, monkeypatch):
    """启动器心跳路径指向 tmp（调用期读 env；delta add-factor-research-selfheal）。"""
    path = tmp_path / "launcher.json"
    monkeypatch.setenv("FACTOR_RESEARCH_LAUNCHER_PATH", str(path))
    return path
```

把 `test_missing_file_reports_missing` / `test_corrupt_text_reports_corrupt` /
`test_non_object_json_reports_corrupt` / `test_missing_timestamp_reports_corrupt` 的期望字典
统一加上 `"launcher": _NO_LAUNCHER`（`control` 用更新后的 `_NO_CONTROL`）。

新增测试：

```python
def _launcher(age_s: int = 40, agent_ok: bool = True) -> dict:
    epoch = int(time.time() - age_s)
    return {
        "checked_at": datetime.fromtimestamp(epoch, tz=_TZ).isoformat(),
        "checked_at_epoch": epoch,
        "agent_pid": 4321,
        "agent_ok": agent_ok,
        "snapshot_age_s": 12,
        "detail": None,
    }


def test_fresh_snapshot_honor_live_without_launcher(_status_path):
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["launcher"] == _NO_LAUNCHER
    assert body["control"]["honor"] == "live"
    assert body["control"]["honor_eta_seconds"] == 0


def test_stale_snapshot_with_fresh_launcher_honor_launcher(_status_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=60)), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["launcher"]["available"] is True
    assert body["launcher"]["fresh"] is True
    assert 60 <= body["launcher"]["age_seconds"] <= 65
    assert body["control"]["honor"] == "launcher"
    assert body["control"]["honor_eta_seconds"] == 180
    assert body["control"]["pending"] is False


def test_stale_launcher_unavailable(_status_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=600)), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["launcher"]["fresh"] is False
    assert body["control"]["honor"] == "unavailable"


def test_launcher_agent_not_ok_unavailable(_status_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(agent_ok=False)), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["control"]["honor"] == "unavailable"


def test_launcher_corrupt_reported(_launcher_path):
    _launcher_path.write_text("not json", encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["launcher"] == {
        "available": False,
        "reason": "corrupt",
    }
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_api_factor_research.py -q`
Expected: FAIL（响应无 `launcher` / `honor`）

- [ ] **Step 3: 实现 launcher 读取与 honor 判定**

在 `api.py` 的 `FACTOR_RESEARCH_STALE_AFTER_S` 旁加入：

```python
FACTOR_RESEARCH_LAUNCHER_FRESH_AFTER_S = 300  # 2× 启动器节拍（120s）+ 余量
FACTOR_RESEARCH_LAUNCHER_HONOR_ETA_S = 180  # 离线受理的披露上界（与陈旧阈值同值）


def _factor_research_launcher_payload() -> dict[str, Any]:
    """启动器心跳：透传 + 派生 ``available`` / ``age_seconds`` / ``fresh``。"""
    path = Path(
        os.environ.get("FACTOR_RESEARCH_LAUNCHER_PATH", "data/factor_research/launcher.json")
    )
    if not path.is_file():
        return {"available": False, "reason": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("launcher heartbeat is not an object")
        epoch = data.get("checked_at_epoch")
        if epoch is None:
            raise ValueError("no checked_at_epoch")
        epoch = float(epoch)
    except (OSError, ValueError, TypeError):
        return {"available": False, "reason": "corrupt"}
    age = max(0, int(time.time() - epoch))
    payload = dict(data)
    payload.update(
        {
            "available": True,
            "age_seconds": age,
            "fresh": age <= FACTOR_RESEARCH_LAUNCHER_FRESH_AFTER_S,
        }
    )
    return payload


def _factor_research_honor(
    snapshot: dict[str, Any], launcher: dict[str, Any]
) -> tuple[str, int | None]:
    """控制受理能力：快照新鲜 → live；启动器新鲜且代理就绪 → launcher；否则 unavailable。"""
    if snapshot.get("available") and not snapshot.get("stale"):
        return "live", 0
    if launcher.get("available") and launcher.get("fresh") and launcher.get("agent_ok") is True:
        return "launcher", FACTOR_RESEARCH_LAUNCHER_HONOR_ETA_S
    return "unavailable", None
```

`_factor_research_control_block` 改为：

```python
def _factor_research_control_block(
    snapshot: dict[str, Any], launcher: dict[str, Any] | None = None
) -> dict[str, Any]:
    """control 块：期望态 + pending + honor（受理能力与 ETA 的单一判定源）。"""
    control = _read_control_file(
        Path(os.environ.get("FACTOR_RESEARCH_CONTROL_PATH", "data/factor_research/control.json"))
    )
    pending = False
    if (
        control["desired_running"] is not None
        and snapshot.get("available")
        and not snapshot.get("stale")
    ):
        running = bool((snapshot.get("supervisor") or {}).get("running"))
        pending = control["desired_running"] != running
    honor, eta = _factor_research_honor(snapshot, launcher or {})
    return {**control, "pending": pending, "honor": honor, "honor_eta_seconds": eta}
```

`_factor_research_status_payload` 改为：

```python
def _factor_research_status_payload() -> dict[str, Any]:
    snapshot = _factor_research_snapshot_payload()
    launcher = _factor_research_launcher_payload()
    return {
        **snapshot,
        "launcher": launcher,
        "control": _factor_research_control_block(snapshot, launcher),
    }
```

`_factor_research_control_update` 的返回改为（本 Task 仅保持形状，门禁在 Task 5）：

```python
    return {
        "control": _factor_research_control_block(
            _factor_research_snapshot_payload(), _factor_research_launcher_payload()
        )
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_api_factor_research.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/api.py tests/test_api_factor_research.py
git commit -m "feat(factors): 状态端点新增 launcher 心跳块与 control.honor"
```

---

### Task 5: PUT 受理门（双离线才 409）

**Files:**
- Modify: `src/finance_agent/api.py`（`_factor_research_control_update`）
- Test: `tests/test_api_factor_research.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api_factor_research.py` 的 PUT 测试段追加：

```python
def test_control_put_accepted_when_snapshot_stale_but_launcher_fresh(
    _status_path, _control_path, _launcher_path
):
    """研究侧离线但启动器可用 → 仍受理（honor=launcher），请求等待下一节拍执行。"""
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=30)), encoding="utf-8")

    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True})

    assert resp.status_code == 200
    assert resp.json()["control"]["honor"] == "launcher"
    assert resp.json()["control"]["honor_eta_seconds"] == 180
    on_disk = json.loads(_control_path.read_text(encoding="utf-8"))
    assert on_disk["desired_running"] is True


def test_control_put_rejected_when_launcher_agent_not_ok(
    _status_path, _control_path, _launcher_path
):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(agent_ok=False)), encoding="utf-8")

    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "control_unavailable"
    assert not _control_path.exists()


def test_control_put_rejected_when_launcher_stale(_status_path, _control_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=600)), encoding="utf-8")

    assert (
        TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True}).status_code == 409
    )
    assert not _control_path.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_api_factor_research.py -q -k "launcher or accepted_when_snapshot_stale"`
Expected: `test_control_put_accepted_when_snapshot_stale_but_launcher_fresh` FAIL（409 ≠ 200）

- [ ] **Step 3: 实现受理门**

`_factor_research_control_update` 开头改为：

```python
def _factor_research_control_update(desired_running: bool) -> dict[str, Any]:
    """写控制文件（原子 tmp + replace）；无任何存活证据 → 409，MUST NOT 落文件。"""
    snapshot = _factor_research_snapshot_payload()
    launcher = _factor_research_launcher_payload()
    honor, _eta = _factor_research_honor(snapshot, launcher)
    if honor == "unavailable":
        raise HTTPException(status_code=409, detail="control_unavailable")
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_api_factor_research.py -q`
Expected: PASS（含既有三条"快照缺失/损坏/陈旧 → 409"用例——默认无心跳，仍判 unavailable）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/api.py tests/test_api_factor_research.py
git commit -m "feat(factors): 控制端点支持启动器在线的离线受理"
```

---

### Task 6: 前端类型与页面状态

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/factors/FactorResearchPage.tsx`
- Test: `frontend/src/test/factorResearchPage.test.tsx`

**Interfaces:**
- Consumes: `launcher` / `control.honor` / `agent`（Task 4–5 的后端契约）。
- Produces: 开关可达性 = `honor in {live, launcher}`；陈旧时位置取 `desired_running`。

- [ ] **Step 1: 写失败测试**

在 `frontend/src/test/factorResearchPage.test.tsx`：

1. `snapshotBody()` 的默认 `control` 改为：

```ts
    control: {
      desired_running: null,
      requested_at: null,
      pending: false,
      honor: 'live',
      honor_eta_seconds: 0,
    },
```

2. 把既有「陈旧：开关禁用」用例的 `snapshotBody({ stale: true, age_seconds: 400 })` 改为：

```ts
    mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: {
          desired_running: null,
          requested_at: null,
          pending: false,
          honor: 'unavailable',
          honor_eta_seconds: null,
        },
      }),
    ])
```

3. 在控制面 describe 末尾追加：

```tsx
  it('离线可开：honor=launcher → 开关可用、位置取意图、明示 ≤3 分钟', async () => {
    const { putCalls } = mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: {
          desired_running: true,
          requested_at: null,
          pending: false,
          honor: 'launcher',
          honor_eta_seconds: 180,
        },
        launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 30 },
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(false)
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain('≤3 分钟')

    fireEvent.click(sw)
    fireEvent.click(await screen.findByTestId('factor-research-control-confirm'))
    await waitFor(() => expect(putCalls).toHaveLength(1))
    expect(putCalls[0].body).toEqual({ desired_running: false })
  })

  it('双离线：honor=unavailable → 禁用并明示研究侧与启动器均离线', async () => {
    mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: {
          desired_running: true,
          requested_at: null,
          pending: false,
          honor: 'unavailable',
          honor_eta_seconds: null,
        },
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain(
      '研究侧与启动器均离线'
    )
  })

  it('已请求等待拉起：requested_at 晚于 collected_at → 明示等待启动器', async () => {
    mockFetch(
      snapshotBody({
        stale: true,
        age_seconds: 400,
        collected_at: '2026-10-19T18:00:00+08:00',
        control: {
          desired_running: true,
          requested_at: '2026-10-19T19:00:00+08:00',
          pending: false,
          honor: 'launcher',
          honor_eta_seconds: 180,
        },
        launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 30 },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain(
      '等待启动器拉起'
    )
  })

  it('启动失败：pending + start-failed → 展示原因而非切换中', async () => {
    mockFetch(
      snapshotBody({
        supervisor: { running: false, pid: null, started_at: null },
        control: {
          desired_running: true,
          requested_at: '2026-10-19T18:00:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        },
        agent: {
          last_action: 'start-failed',
          last_action_at: '2026-10-19T18:00:05+08:00',
          last_error: '看护未在启动窗口内就位（检查 launch 脚本与环境）',
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    const state = screen.getByTestId('factor-research-control-state').textContent ?? ''
    expect(state).toContain('启动失败')
    expect(state).toContain('看护未在启动窗口内就位')
    expect(state).not.toContain('切换中')
  })

  it('连续短命会话：最近 3 次均 <120 秒 → 告警', async () => {
    mockFetch(
      snapshotBody({
        sessions: [
          { log_file: 's3.log', started_at: '2026-10-18T09:35:26+08:00', ended_at: '2026-10-18T09:35:49+08:00', duration_s: 23 },
          { log_file: 's2.log', started_at: '2026-10-18T09:25:02+08:00', ended_at: '2026-10-18T09:25:26+08:00', duration_s: 24 },
          { log_file: 's1.log', started_at: '2026-10-18T09:14:37+08:00', ended_at: '2026-10-18T09:15:02+08:00', duration_s: 25 },
        ],
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const alert = await screen.findByTestId('factor-research-short-sessions')
    expect(alert.textContent).toContain('连续短命会话')
    expect(alert.textContent).toContain('23')
  })
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npm test -- --run src/test/factorResearchPage.test.tsx`
Expected: FAIL（`launcher`/`honor` 类型错误或文案断言失败）

- [ ] **Step 3: 扩展类型**

`frontend/src/types.ts` 在 `FactorResearchTotals` 之后加入：

```ts
export interface FactorResearchAgent {
  /** 最近一次期望态调和动作（控制文件缺失/损坏 → no_intent；一致 → noop） */
  last_action: 'no_intent' | 'noop' | 'started' | 'stopped' | 'start-failed' | 'stop-failed' | null
  last_action_at: string | null
  /** start-failed / stop-failed 时的可判读摘要 */
  last_error: string | null
}

export interface FactorResearchLauncher {
  available: boolean
  reason?: 'missing' | 'corrupt'
  checked_at?: string | null
  checked_at_epoch?: number | null
  age_seconds?: number
  /** age_seconds ≤ 300 */
  fresh?: boolean
  agent_pid?: number | null
  agent_ok?: boolean
  snapshot_age_s?: number | null
  detail?: string | null
}
```

`FactorResearchControl` 追加：

```ts
  /** 控制受理能力（后端单源判定） */
  honor: 'live' | 'launcher' | 'unavailable'
  /** 离线受理的预计拉起上界（秒；live=0，unavailable=null） */
  honor_eta_seconds: number | null
```

`FactorResearchStatusResponse` 追加：

```ts
  /** 代理调和动作块（独立采集器路径可能为 null） */
  agent?: FactorResearchAgent | null
  /** 启动器心跳块（delta add-factor-research-selfheal） */
  launcher?: FactorResearchLauncher | null
```

- [ ] **Step 4: 实现页面状态**

`FactorResearchPage.tsx` 控制面派生段（`const supervisor = ...` 起）替换为：

```tsx
  const supervisor = data?.supervisor ?? null
  const current = data?.current_session ?? null
  const sessions = data?.sessions ?? []
  const totals = data?.totals ?? null
  const stale = data?.stale === true
  const collectedAt = data?.collected_at ? formatSessionTime(data.collected_at) : '—'

  // ── 控制面派生（快照实态优先；离线时以持久化意图为可信位置）──
  const control = data?.control ?? null
  const launcher = data?.launcher ?? null
  const agentInfo = data?.agent ?? null
  const honor = control?.honor ?? 'unavailable'
  const snapshotLive = data !== null && data.available === true && !stale
  const running = snapshotLive
    ? supervisor?.running === true
    : control?.desired_running === true
  const pending = control?.pending === true
  // honor 由后端判定；live 额外要求快照确实新鲜（防御后端/夹具不一致）
  const reachable = (honor === 'live' && snapshotLive) || honor === 'launcher'
  const switchDisabled = submitting || pending || channelBlocked || !reachable

  const requestedAt = control?.requested_at ? formatSessionTime(control.requested_at) : null
  const awaitingLauncher =
    honor === 'launcher' &&
    control?.requested_at != null &&
    data?.collected_at != null &&
    Date.parse(control.requested_at) > Date.parse(data.collected_at)

  let controlStateText: string
  if (data === null) {
    controlStateText = '—'
  } else if (data.available !== true) {
    if (honor === 'launcher') {
      controlStateText = '研究侧启动中（等待首个快照）——启动器已确认代理存活'
    } else if (data.reason === 'corrupt') {
      controlStateText = '控制通道未接入（快照损坏）'
    } else {
      controlStateText = '控制通道未接入（采集器未接入）'
    }
  } else if (stale) {
    if (honor === 'launcher') {
      controlStateText = awaitingLauncher
        ? `已请求（${requestedAt}），等待启动器拉起`
        : `研究侧离线（最近采集 ${collectedAt}）——将在 ≤3 分钟内自动拉起`
    } else {
      controlStateText = '控制通道暂不可用（研究侧与启动器均离线）'
    }
  } else if (pending) {
    const failedAction =
      agentInfo?.last_action === 'start-failed' || agentInfo?.last_action === 'stop-failed'
        ? agentInfo.last_action
        : null
    if (failedAction !== null) {
      const label = failedAction === 'start-failed' ? '启动失败' : '停止失败'
      const at = agentInfo?.last_action_at ? formatSessionTime(agentInfo.last_action_at) : '—'
      controlStateText = `${label}（${at}）：${agentInfo?.last_error ?? '原因未记录'}；代理将自动重试`
    } else {
      controlStateText = control?.requested_at
        ? `切换中…（${formatSessionTime(control.requested_at)} 发起，等待研发侧受理）`
        : '切换中…（等待研发侧受理）'
    }
  } else {
    controlStateText = running ? '已开启' : '已关闭'
  }

  const recentDurations = (data?.sessions ?? [])
    .filter((s) => s.duration_s !== null)
    .slice(0, 3)
    .map((s) => s.duration_s as number)
  const shortSessions = recentDurations.length === 3 && recentDurations.every((d) => d < 120)
```

陈旧横幅文案（`factor-research-stale-banner` 内）改为：

```tsx
                数据已陈旧（最近采集 {collectedAt}）——采集器可能已停止，以下为最后一次快照
                {honor === 'launcher' && launcher?.checked_at != null && (
                  <>；启动器正在按节拍尝试恢复（最近检查 {formatSessionTime(launcher.checked_at)}）</>
                )}
```

在 `{stale && ...}` 横幅之后、看护状态区之前加入短命会话告警：

```tsx
            {shortSessions && (
              <div
                data-testid="factor-research-short-sessions"
                className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
                style={{ background: 'var(--status-warning-default)', color: 'white' }}
              >
                <i className="fas fa-exclamation-triangle"></i>
                连续短命会话：最近 3 次均在 {recentDurations.join(' / ')} 秒内结束——疑似凭证或配额问题（研发侧将退避重试）
              </div>
            )}
```

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && npm test -- --run src/test/factorResearchPage.test.tsx`
Expected: PASS（含既有全部用例）

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 退出码 0

- [ ] **Step 7: 提交**

```bash
git add frontend/src/types.ts frontend/src/pages/factors/FactorResearchPage.tsx \
  frontend/src/test/factorResearchPage.test.tsx
git commit -m "feat(factors): 监控页支持离线可开与失败可读"
```

---

### Task 7: E2E 配置与用例

**Files:**
- Modify: `tests/e2e/playwright/playwright.config.ts`
- Modify: `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`

- [ ] **Step 1: 注入心跳路径**

`playwright.config.ts` 的 webServer `env` 对象追加：

```ts
FACTOR_RESEARCH_LAUNCHER_PATH: 'data/test-e2e-factor-research-launcher.json',
```

- [ ] **Step 2: 写 E2E 用例（先确认失败）**

`factor-research-monitor.spec.ts` 顶部（`CONTROL_PATH` 之后）加入：

```ts
const LAUNCHER_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-launcher.json'
)

if (!LAUNCHER_PATH.includes('test-e2e')) {
  throw new Error(`launcher fixture 路径未隔离: ${LAUNCHER_PATH}`)
}

/** 启动器心跳 fixture（默认新鲜 + 代理就绪）。 */
function writeLauncher({ ageS = 30, agentOk = true }: { ageS?: number; agentOk?: boolean } = {}) {
  const epoch = Math.floor(Date.now() / 1000) - ageS
  writeFileSync(
    LAUNCHER_PATH,
    JSON.stringify({
      checked_at: isoAt(epoch),
      checked_at_epoch: epoch,
      agent_pid: 4321,
      agent_ok: agentOk,
      snapshot_age_s: 12,
      detail: null,
    })
  )
}

function removeLauncher() {
  rmSync(LAUNCHER_PATH, { force: true })
}

/** 控制文件写入（PUT 之外的人工前置态：如「已请求但尚未生效」）。 */
function writeControl(payload: Record<string, unknown>) {
  mkdirSync(path.dirname(CONTROL_PATH), { recursive: true })
  writeFileSync(CONTROL_PATH, JSON.stringify(payload), 'utf-8')
}

test.beforeEach(() => {
  removeLauncher()
})

test.afterAll(() => {
  removeLauncher()
})
```

在文件末尾追加用例：

```ts
test('9. 离线可开：陈旧快照 + 启动器心跳 → 开关可点并明示自动拉起', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  removeControl()
  writeLauncher()

  await page.goto('/factor-research')

  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeEnabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText('≤3 分钟')
  await expect(page.getByTestId('factor-research-stale-banner')).toContainText('启动器正在按节拍尝试恢复')

  await sw.click()  // 意图为 null → 位置为关 → 点击 = 开启
  await expect
    .poll(() => readControl()?.desired_running, { timeout: 10_000 })
    .toBe(true)
  await expect(page.getByTestId('factor-research-control-state')).toContainText('等待启动器拉起')
})

test('10. 双离线：陈旧快照 + 无心跳 → 开关禁用并明示理由', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  removeControl()

  await page.goto('/factor-research')

  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeDisabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText(
    '研究侧与启动器均离线'
  )
})

test('11. 启动失败：pending + start-failed → 展示原因而非切换中', async ({ page }) => {
  writeSnapshot(
    snapshotBody({
      supervisor: { running: false, pid: null, started_at: null },
      agent: {
        last_action: 'start-failed',
        last_action_at: isoAt(Math.floor(Date.now() / 1000) - 30),
        last_error: '看护未在启动窗口内就位（检查 launch 脚本与环境）',
      },
    })
  )
  writeControl({ desired_running: true, requested_at: isoAt(Math.floor(Date.now() / 1000)) })

  await page.goto('/factor-research')

  const state = page.getByTestId('factor-research-control-state')
  await expect(state).toContainText('启动失败')
  await expect(state).toContainText('看护未在启动窗口内就位')
  await expect(state).not.toContainText('切换中')
})

test('12. 连续短命会话：最近 3 次均 <120 秒 → 告警', async ({ page }) => {
  const now = Math.floor(Date.now() / 1000)
  writeSnapshot(
    snapshotBody({
      sessions: [
        { log_file: 's3.log', started_at: isoAt(now - 3600), ended_at: isoAt(now - 3577), duration_s: 23 },
        { log_file: 's2.log', started_at: isoAt(now - 4200), ended_at: isoAt(now - 4176), duration_s: 24 },
        { log_file: 's1.log', started_at: isoAt(now - 4800), ended_at: isoAt(now - 4775), duration_s: 25 },
      ],
    })
  )

  await page.goto('/factor-research')

  await expect(page.getByTestId('factor-research-short-sessions')).toContainText('连续短命会话')
})
```

同时把用例 7 的断言从 `'数据已陈旧'` 改为 `'研究侧与启动器均离线'`（开关文案；横幅仍保留「数据已陈旧」）：

```ts
  await expect(page.getByTestId('factor-research-control-state')).toContainText(
    '研究侧与启动器均离线'
  )
```

- [ ] **Step 3: 运行 E2E（目标 spec）**

Run: `cd tests/e2e/playwright && npx playwright test tests/factor-research-monitor.spec.ts`
Expected: PASS（12/12；若本地端口 8000/5173 被 Docker 栈占用，先按 `project-workflow.md` §5.6 处理端口，MUST NOT 打到生产库）

- [ ] **Step 4: 提交**

```bash
git add tests/e2e/playwright/playwright.config.ts \
  tests/e2e/playwright/tests/factor-research-monitor.spec.ts
git commit -m "test(factors): E2E 覆盖离线可开/双离线/失败披露/短命告警"
```

---

### Task 8: 运维文档、部署与真栈人工验证

**Files:**
- Modify: `tests/scripts/spike/README.md`
- Create: `tests/validation/2026-10-19-add-factor-research-selfheal-validation.md`

- [ ] **Step 1: 更新 README**

在 `tests/scripts/spike/README.md` 的运维说明中：
- 将「代理与看护都是**手动常驻**（distro 重启后需重新 `start`；不做开机自启）」改为：
  「代理由 **Windows 计划任务自愈启动器**保持常驻（登录时 + 每 2 分钟），看护启停仍由控制意图决定」；
- 新增小节「自愈启动器」：注册/卸载命令、心跳文件位置与新鲜阈值（300s）、排障
  （`launcher.json` 不更新 → 查计划任务状态 / `agent.log`）、回滚（卸载任务后行为退回「离线仅禁用」）。

- [ ] **Step 2: 部署到 distro（不重启看护）**

```bash
wsl -d rdagent -u root -- bash -c "tr -d '\r' > /root/ensure-factor-research.sh" < tests/scripts/spike/ensure-factor-research.sh
wsl -d rdagent -u root -- bash -c "tr -d '\r' > /root/factor_research_agent.py" < tests/scripts/spike/factor_research_agent.py
wsl -d rdagent -u root -- bash -c "tr -d '\r' > /root/collect_factor_status.py" < tests/scripts/spike/collect_factor_status.py
wsl -d rdagent -u root -- chmod +x /root/ensure-factor-research.sh
# 重启代理守护以加载新代码（先杀旧守护；看护进程是独立 supervisor，不受影响）
wsl -d rdagent -u root -e sh -c 'kill $(cat /root/rdagent-work/factor_research_agent.pid) 2>/dev/null || true'
wsl -d rdagent -u root -e bash /root/ensure-factor-research.sh
```

Expected: 输出 `launcher tick: agent_ok=True pid=<n>`；`data/factor_research/launcher.json` 出现。

- [ ] **Step 3: 注册计划任务**

```bash
powershell -ExecutionPolicy Bypass -File scripts/register-factor-research-task.ps1
```

Expected: `registered: finance-agent-factor-research-launcher state=Ready`

- [ ] **Step 4: 真栈人工验证（逐条记录结果）**

1. **代理自愈**：`kill <agent pid>` → 等 ≤2 分钟 → 新 pid 出现、心跳持续更新、快照恢复推进。
2. **distro 自愈**：`wsl.exe --terminate rdagent` → 等 ≤2 分钟 → distro 起、代理起、快照恢复。
3. **意图不被改写**：记录 `control.json` 字节摘要 → 连续 2 个节拍后比对不变。
4. **离线可开**（需 Owner 同意开始研发，会消耗 LLM 额度）：让快照陈旧（或保留离线窗口）→ 页面点开启 →
   控制文件写入 true → 下一节拍研究侧拉起 → 页面转「已开启」。
5. **重启语义**：意图=开 → `wsl --shutdown` + 重新注册/触发任务 → 自动继续；意图=关 → 保持停止。

- [ ] **Step 5: 写人工验证报告**

`tests/validation/2026-10-19-add-factor-research-selfheal-validation.md`：逐条记录步骤、命令、
实际输出、结论（含任何偏离与后续项）。

- [ ] **Step 6: 提交**

```bash
git add tests/scripts/spike/README.md tests/validation/2026-10-19-add-factor-research-selfheal-validation.md
git commit -m "docs(factors): 自愈启动器运维步骤与人工验证报告"
```

---

### Task 9: 全量验证与交接

- [ ] **Step 1: 后端测试**

Run:
```bash
uv run pytest tests/test_api_factor_research.py tests/scripts/spike/test_factor_research_agent.py \
  tests/scripts/spike/test_collect_factor_status.py tests/scripts/spike/test_launcher_scripts.py -q
```
Expected: 全绿

- [ ] **Step 2: Lint / 类型**

Run: `uv run ruff check && uv run mypy`
Expected: 无新增错误

- [ ] **Step 3: 前端测试与构建**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit`
Expected: 全绿 / 0 类型错误

- [ ] **Step 4: E2E 门禁**

Run: `cd tests/e2e/playwright && npx playwright test tests/factor-research-monitor.spec.ts`
Expected: PASS

- [ ] **Step 5: OpenSpec 校验**

Run: `openspec validate add-factor-research-selfheal --strict`
Expected: `Change 'add-factor-research-selfheal' is valid`

- [ ] **Step 6: 交接**

在 PR 描述中列出：delta 链接、E2E 结果、人工验证报告路径、回滚步骤（卸载计划任务）。
`tasks.md` 逐项勾选；sync + archive 待人工验证通过后执行。

---

## Self-Review

- **Spec coverage**：快照 `agent` 块 → Task 1；`--launcher-tick`/心跳 → Task 2；启动器入口与计划任务 →
  Task 3；`launcher` 块 + `honor` → Task 4；受理门 → Task 5；前端离线可开/位置取意图/失败可读/短命告警
  → Task 6；E2E → Task 7；运维与人工验证 → Task 8；门禁与交接 → Task 9。
- **Placeholder scan**：无 TBD / TODO / “add appropriate error handling”。
- **Type consistency**：`agent.last_action` 枚举在 spec、types、页面判断三处一致（含 `no_intent`）；
  `honor` 三态与 `honor_eta_seconds` 在 spec、api、types、页面一致；心跳字段名与 `--launcher` 参数一致。
