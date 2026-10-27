"""因子研发代理 TDD（delta add-factor-research-control T1）。

契约（spec factor-research-monitor「看护控制通道」+「状态快照采集契约」改编点）：

- 期望态调和：`desired_running=true` 且未运行 → 启动；`false` 且运行中 → 停止；
  期望=实态 → 零动作（幂等）；
- fail-safe：控制文件缺失 / 非合法 JSON / `desired_running` 缺失或非布尔 → 零动作；
- 停止语义与旧 stop 脚本逐字同序（pkill -P → kill → pkill -f fin_factor → pidfile 清理 → 校验）；
- 停止 MUST NOT 删除研发产物（workspace/候选/日志保持）；
- 代理独立于看护存活：看护停止后仍周期采集（快照 running=false 且持续推进）；
- flock 串行化（无 fcntl 的环境降级放行——仅测试环境）。

运行方式：``uv run pytest tests/scripts/spike/test_factor_research_agent.py -q``
（tests/scripts 不在默认套件内，与 test_collect_factor_status.py 同先例）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import collect_factor_status as cfs
import factor_research_agent as agent
import pytest

# ── read_desired：三态（合法 / 缺失 / 损坏或字段非法）──


def test_read_desired_valid(tmp_path):
    path = tmp_path / "control.json"
    path.write_text(
        json.dumps(
            {
                "desired_running": False,
                "requested_at": "2026-10-17T23:50:00+08:00",
                "requested_at_epoch": 1760716200,
                "request_id": "abc123",
            }
        ),
        encoding="utf-8",
    )
    assert agent.read_desired(path) == (False, "2026-10-17T23:50:00+08:00", "abc123")


def test_read_desired_missing(tmp_path):
    assert agent.read_desired(tmp_path / "absent.json") == (None, None, None)


def test_read_desired_corrupt(tmp_path):
    path = tmp_path / "control.json"
    path.write_text("not json", encoding="utf-8")
    assert agent.read_desired(path) == (None, None, None)


def test_read_desired_invalid_field(tmp_path):
    """desired_running 非布尔 / 缺失 → 不猜（None）。"""
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"desired_running": "true"}), encoding="utf-8")
    assert agent.read_desired(path)[0] is None
    path.write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert agent.read_desired(path)[0] is None


# ── reconcile_once：六态（no_intent/noop/started/stopped/start-failed/stop-failed）──


def _stub_reconcile(monkeypatch, *, running, start_ok=True, stop_ok=True):
    """把实态与动作替换为可断言的桩。"""
    calls: list[str] = []
    monkeypatch.setattr(agent, "supervisor_pid", lambda workdir: 7752 if running else None)
    monkeypatch.setattr(
        agent, "start_supervisor", lambda workdir: (calls.append("start"), start_ok)[1]
    )
    monkeypatch.setattr(
        agent, "stop_supervisor", lambda workdir: (calls.append("stop"), stop_ok)[1]
    )
    return calls


def _write_control(tmp_path, desired: bool):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"desired_running": desired}), encoding="utf-8")
    return path


def test_reconcile_no_intent_no_action(tmp_path, monkeypatch):
    calls = _stub_reconcile(monkeypatch, running=True)
    assert agent.reconcile_once(tmp_path, tmp_path / "missing.json") == "no_intent"
    assert calls == []


def test_reconcile_corrupt_no_action(tmp_path, monkeypatch):
    calls = _stub_reconcile(monkeypatch, running=True)
    bad = tmp_path / "control.json"
    bad.write_text("{broken", encoding="utf-8")
    assert agent.reconcile_once(tmp_path, bad) == "no_intent"
    assert calls == []


def test_reconcile_desired_true_starts_when_stopped(tmp_path, monkeypatch):
    calls = _stub_reconcile(monkeypatch, running=False)
    control = _write_control(tmp_path, True)
    assert agent.reconcile_once(tmp_path, control) == "started"
    assert calls == ["start"]


def test_reconcile_desired_false_stops_when_running(tmp_path, monkeypatch):
    calls = _stub_reconcile(monkeypatch, running=True)
    control = _write_control(tmp_path, False)
    assert agent.reconcile_once(tmp_path, control) == "stopped"
    assert calls == ["stop"]


def test_reconcile_equal_state_noop_both_directions(tmp_path, monkeypatch):
    calls = _stub_reconcile(monkeypatch, running=True)
    control = _write_control(tmp_path, True)
    assert agent.reconcile_once(tmp_path, control) == "noop"
    calls_running = _stub_reconcile(monkeypatch, running=False)
    control = _write_control(tmp_path, False)
    assert agent.reconcile_once(tmp_path, control) == "noop"
    assert calls == [] and calls_running == []


def test_reconcile_failures_reported(tmp_path, monkeypatch):
    _stub_reconcile(monkeypatch, running=False, start_ok=False)
    assert agent.reconcile_once(tmp_path, _write_control(tmp_path, True)) == "start-failed"
    _stub_reconcile(monkeypatch, running=True, stop_ok=False)
    assert agent.reconcile_once(tmp_path, _write_control(tmp_path, False)) == "stop-failed"


# ── 停止动作：命令序列与旧 stop 脚本逐字同语义 ──


def test_stop_commands_sequence_matches_legacy_script():
    assert agent.stop_commands(7752) == [
        ["pkill", "-P", "7752"],
        ["kill", "7752"],
        ["pkill", "-f", "fin_factor"],
    ]


def test_stop_supervisor_sequence_and_verify(tmp_path, monkeypatch):
    recorded: list[list[str]] = []
    monkeypatch.setattr(agent, "supervisor_pid", lambda workdir: 7752)
    monkeypatch.setattr(agent, "_run_quiet", lambda cmd: recorded.append(cmd))
    monkeypatch.setattr(agent, "_fin_factor_present", lambda: False)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    (tmp_path / agent.PIDFILE_NAME).write_text("7752", encoding="utf-8")

    assert agent.stop_supervisor(tmp_path) is True
    assert recorded == agent.stop_commands(7752)
    assert not (tmp_path / agent.PIDFILE_NAME).exists()


def test_stop_supervisor_verify_reports_residual(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "supervisor_pid", lambda workdir: None)
    monkeypatch.setattr(agent, "_run_quiet", lambda cmd: None)
    monkeypatch.setattr(agent, "_fin_factor_present", lambda: True)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    assert agent.stop_supervisor(tmp_path) is False


# ── 启动动作：看护脚本内容 + 拉起 ──


def test_supervisor_script_contains_pidfile_backoff_and_launch(tmp_path, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(agent.subprocess, "Popen", lambda *a, **k: spawned.append(a[0]))
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "supervisor_pid", lambda workdir: 999)

    assert agent.start_supervisor(tmp_path) is True
    script = (tmp_path / agent.SUPERVISOR_SCRIPT_NAME).read_text(encoding="utf-8")
    assert 'echo $$ > "$WORKDIR/factor_research.pid"' in script
    assert "sleep 600" in script and "sleep 60" in script
    assert agent.LAUNCH_SCRIPT in script
    assert spawned == [["bash", str(tmp_path / agent.SUPERVISOR_SCRIPT_NAME)]]


def test_start_supervisor_fails_when_pidfile_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "supervisor_pid", lambda workdir: None)
    assert agent.start_supervisor(tmp_path) is False


# ── 停止不清数据 + 代理独立存活（端到端 tick，纯文件系统）──


def _seed_workdir(tmp_path):
    workdir = tmp_path / "rdagent-work"
    (workdir / "factor_research_logs").mkdir(parents=True)
    ws_task = workdir / "git_ignore_folder" / "RD-Agent_workspace" / "task-1"
    ws_task.mkdir(parents=True)
    (ws_task / "factor.py").write_text("# candidate\n", encoding="utf-8")
    (tmp_path / "candidates.json").write_text(
        json.dumps({"candidates": [{"name": "f1"}]}), encoding="utf-8"
    )
    return workdir


def test_stop_does_not_delete_products(tmp_path, monkeypatch):
    workdir = _seed_workdir(tmp_path)
    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)
    monkeypatch.setattr(agent, "_run_quiet", lambda cmd: None)
    monkeypatch.setattr(agent, "_fin_factor_present", lambda: False)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)

    assert agent.stop_supervisor(workdir) is True
    assert (workdir / "git_ignore_folder" / "RD-Agent_workspace" / "task-1" / "factor.py").exists()
    assert (tmp_path / "candidates.json").exists()


def test_agent_keeps_collecting_while_supervisor_stopped(tmp_path, monkeypatch):
    """代理独立存活：看护停止（无 pidfile）下两个 tick 都写 running=false 快照。"""
    workdir = _seed_workdir(tmp_path)
    control = _write_control(tmp_path, False)  # 期望停止 + 实态停止 → noop
    out = tmp_path / "status.json"
    state = workdir / "factor_research_logs" / ".collector_state.json"

    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)

    action1, snap1 = agent.run_tick(workdir, control, out, state)
    time.sleep(1.05)
    action2, snap2 = agent.run_tick(workdir, control, out, state)

    assert action1 == action2 == "noop"
    assert snap1["supervisor"]["running"] is False
    assert snap2["supervisor"]["running"] is False
    assert out.is_file()
    assert snap2["collected_at_epoch"] > snap1["collected_at_epoch"]


def test_tick_lock_is_context_manager(tmp_path):
    with agent.tick_lock(tmp_path):
        pass


# ── ensure_daemon / set_intent ──


def test_set_intent_writes_atomic_control_file(tmp_path):
    control = tmp_path / "factor_research" / "control.json"
    payload = agent.set_intent(control, True)
    on_disk = json.loads(control.read_text(encoding="utf-8"))
    assert on_disk["desired_running"] is True
    assert on_disk["requested_at"] == payload["requested_at"]
    assert on_disk["request_id"].startswith("manual-")
    assert not list(tmp_path.rglob("*.tmp"))


def test_ensure_daemon_returns_existing_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "agent_pid", lambda w: 4242)
    assert (
        agent.ensure_daemon(
            tmp_path, tmp_path / "c.json", tmp_path / "s.json", tmp_path / "st.json", 15
        )
        == 4242
    )


# ── 评审修复轮（残留自愈 / 身份校验 / 单例 / 等锁超时 / CLI 契约 / env 默认）──


def test_stop_residual_sets_marker_and_reconcile_retries(tmp_path, monkeypatch):
    """停止验证失败 → 落残留标记；后续调和（desired=false 且 pidfile 已无）继续清扫。"""
    workdir = _seed_workdir(tmp_path)
    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)
    monkeypatch.setattr(agent, "_run_quiet", lambda cmd: None)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)

    monkeypatch.setattr(agent, "_fin_factor_present", lambda: True)  # 有残留 → False + 标记
    assert agent.stop_supervisor(workdir) is False
    assert (workdir / agent.RESIDUAL_MARKER_NAME).exists()

    # 调和：desired=false、实态=停（无 pidfile）→ 因标记存在而继续清扫
    calls: list[str] = []
    monkeypatch.setattr(agent, "stop_supervisor", lambda w: (calls.append("stop"), True)[1])
    assert agent.reconcile_once(workdir, _write_control(tmp_path, False)) == "stopped"
    assert calls == ["stop"]

    # 标记存在但期望开启（实态停）→ 走正常启动路径（不因标记干扰）
    calls.clear()
    monkeypatch.setattr(agent, "start_supervisor", lambda w: (calls.append("start"), True)[1])
    assert agent.reconcile_once(workdir, _write_control(tmp_path, True)) == "started"
    assert calls == ["start"]


def test_stop_clean_clears_residual_marker(tmp_path, monkeypatch):
    workdir = _seed_workdir(tmp_path)
    (workdir / agent.RESIDUAL_MARKER_NAME).write_text("1", encoding="utf-8")
    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)
    monkeypatch.setattr(agent, "_run_quiet", lambda cmd: None)
    monkeypatch.setattr(agent, "_fin_factor_present", lambda: False)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    assert agent.stop_supervisor(workdir) is True
    assert not (workdir / agent.RESIDUAL_MARKER_NAME).exists()


def test_reconcile_noop_without_marker(tmp_path, monkeypatch):
    """无标记时期望=实态 → noop（MUST NOT 对无标记的手动 fin_factor 会话盲杀）。"""
    workdir = _seed_workdir(tmp_path)
    calls = _stub_reconcile(monkeypatch, running=False)
    assert agent.reconcile_once(workdir, _write_control(tmp_path, False)) == "noop"
    assert calls == []


def test_agent_pid_rejects_foreign_pid(tmp_path, monkeypatch):
    """PID 复用防误判：pidfile 存活但 cmdline 非本代理 → None。"""
    (tmp_path / agent.AGENT_PIDFILE_NAME).write_text("4242", encoding="utf-8")
    monkeypatch.setattr(agent, "_alive", lambda pid: True)
    monkeypatch.setattr(agent, "_pid_is_agent", lambda pid: False)
    assert agent.agent_pid(tmp_path) is None
    monkeypatch.setattr(agent, "_pid_is_agent", lambda pid: True)
    assert agent.agent_pid(tmp_path) == 4242


class _FakeFcntl:
    """最小 flock 桩（在无 fcntl 的测试环境验证互斥/超时语义）。"""

    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 4

    def __init__(self, *, busy_on_exclusive: bool = False):
        self.held = False
        self.busy_on_exclusive = busy_on_exclusive

    def flock(self, fh, op):
        if op == (self.LOCK_EX | self.LOCK_NB):
            if self.held or self.busy_on_exclusive:
                raise OSError("flock busy")
            self.held = True
        elif op == self.LOCK_EX:
            self.held = True
        elif op == self.LOCK_UN:
            self.held = False


def test_singleton_lock_rejects_second_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "fcntl", _FakeFcntl())
    with agent.singleton_lock(tmp_path), pytest.raises(agent.SingletonBusy):  # noqa: SIM117 - 内层须在持有外层后入场
        agent.singleton_lock(tmp_path).__enter__()  # 第二次入场应立刻抛 SingletonBusy


def test_tick_lock_timeout_degrades_instead_of_hanging(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "fcntl", _FakeFcntl(busy_on_exclusive=True))
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    with agent.tick_lock(tmp_path, timeout_s=0.01):  # 抢不到 → 告警后继续（不抛错）
        pass


def test_main_reconcile_once_exit_codes(tmp_path, monkeypatch):
    workdir = _seed_workdir(tmp_path)
    monkeypatch.setenv("FACTOR_RESEARCH_CONFIG_PATH", str(tmp_path / "pipeline.json"))
    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)
    monkeypatch.setattr(agent, "_run_quiet", lambda cmd: None)
    monkeypatch.setattr(agent, "_fin_factor_present", lambda: False)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    control_stop = _write_control(tmp_path, False)
    assert (
        agent.main(["--reconcile-once", "--workdir", str(workdir), "--control", str(control_stop)])
        == 0
    )
    monkeypatch.setattr(agent, "start_supervisor", lambda w, config=None: False)
    control_start = _write_control(tmp_path, True)
    assert (
        agent.main(["--reconcile-once", "--workdir", str(workdir), "--control", str(control_start)])
        == 1
    )


def test_main_control_path_defaults_to_env(tmp_path, monkeypatch):
    """未传 --control 时取 env FACTOR_RESEARCH_CONTROL_PATH（规格「经环境变量可覆盖」）。"""
    workdir = _seed_workdir(tmp_path)
    env_control = tmp_path / "env-control.json"
    env_control.write_text(json.dumps({"desired_running": True}), encoding="utf-8")
    monkeypatch.setenv("FACTOR_RESEARCH_CONTROL_PATH", str(env_control))
    monkeypatch.setenv("FACTOR_RESEARCH_CONFIG_PATH", str(tmp_path / "pipeline.json"))
    calls: list[str] = []
    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)
    monkeypatch.setattr(
        agent, "start_supervisor", lambda w, config=None: (calls.append("start"), True)[1]
    )
    assert agent.main(["--reconcile-once", "--workdir", str(workdir)]) == 0
    assert calls == ["start"]


def test_ensure_daemon_spawns_with_explicit_args(tmp_path, monkeypatch):
    import sys as _sys

    spawned: list[list[str]] = []
    pids = iter([None, 4242])
    monkeypatch.setattr(agent, "agent_pid", lambda w: next(pids))
    monkeypatch.setattr(agent.subprocess, "Popen", lambda args, **kw: spawned.append(list(args)))
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    control = tmp_path / "c.json"
    out = tmp_path / "s.json"
    state = tmp_path / "st.json"
    assert agent.ensure_daemon(tmp_path, control, out, state, 15) == 4242
    args = spawned[0]
    assert args[0] == _sys.executable and args[1].endswith("factor_research_agent.py")
    assert "--loop" in args and str(control) in args and str(out) in args and str(state) in args


def test_ensure_daemon_returns_none_when_spawn_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "agent_pid", lambda w: None)
    monkeypatch.setattr(agent.subprocess, "Popen", lambda args, **kw: None)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    assert (
        agent.ensure_daemon(
            tmp_path, tmp_path / "c.json", tmp_path / "s.json", tmp_path / "st.json", 15
        )
        is None
    )


def test_tick_reflects_started_supervisor_in_snapshot(tmp_path, monkeypatch):
    """「期望开启拉起看护」的 AND 条款：调和后同一 tick 的快照 running=true。"""
    workdir = _seed_workdir(tmp_path)
    control = _write_control(tmp_path, True)
    out = tmp_path / "status.json"
    state = workdir / "factor_research_logs" / ".collector_state.json"

    def fake_popen(args, **kw):
        (workdir / agent.PIDFILE_NAME).write_text("4242", encoding="utf-8")  # 模拟看护落 pidfile
        return None

    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(agent, "_alive", lambda pid: True)
    monkeypatch.setattr(cfs, "_process_alive", lambda pid: True)  # 采集侧存活判定（勿真发信号）
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)

    action, snapshot = agent.run_tick(workdir, control, out, state)
    assert action == "started"
    assert snapshot["supervisor"]["running"] is True


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
        tmp_path,
        control,
        tmp_path / "status.json",
        tmp_path / "state.json",
        tmp_path / "launcher.json",
        15,
    )

    assert control.read_bytes() == before


def test_launcher_tick_reports_agent_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ensure_daemon", lambda *a, **k: None)

    payload = agent.launcher_tick(
        tmp_path,
        tmp_path / "control.json",
        tmp_path / "status.json",
        tmp_path / "state.json",
        tmp_path / "launcher.json",
        15,
    )

    assert payload["agent_ok"] is False
    assert payload["agent_pid"] is None
    assert payload["detail"]


# ── 研发节奏配置（add-factor-research-settings）──


def test_read_config_defaults_when_missing(tmp_path):
    cfg = agent.read_config(tmp_path / "pipeline.json")
    assert cfg["values"] == agent.DEFAULT_CONFIG_VALUES
    assert cfg["source"] == "defaults"
    assert cfg["invalid_fields"] == []
    assert cfg["updated_at"] is None


def test_read_config_file_values(tmp_path):
    path = tmp_path / "pipeline.json"
    path.write_text(
        json.dumps(
            {
                "snapshot_interval_s": 30,
                "session_gap_s": 120,
                "short_session_backoff_s": 900,
                "short_session_threshold_s": 60,
                "updated_at": "2026-10-21T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    cfg = agent.read_config(path)
    assert cfg["values"] == {
        "snapshot_interval_s": 30,
        "session_gap_s": 120,
        "short_session_backoff_s": 900,
        "short_session_threshold_s": 60,
    }
    assert cfg["source"] == "file"
    assert cfg["updated_at"] == "2026-10-21T10:00:00+08:00"


def test_read_config_invalid_fields_fallback(tmp_path):
    path = tmp_path / "pipeline.json"
    path.write_text(
        json.dumps({"session_gap_s": 99999, "short_session_backoff_s": "600"}),
        encoding="utf-8",
    )
    cfg = agent.read_config(path)
    assert cfg["values"]["session_gap_s"] == agent.DEFAULT_CONFIG_VALUES["session_gap_s"]
    assert (
        cfg["values"]["short_session_backoff_s"]
        == agent.DEFAULT_CONFIG_VALUES["short_session_backoff_s"]
    )
    assert set(cfg["invalid_fields"]) == {"session_gap_s", "short_session_backoff_s"}
    assert cfg["source"] == "file"


def test_read_config_corrupt_and_bool_rejected(tmp_path):
    path = tmp_path / "pipeline.json"
    path.write_text("not json", encoding="utf-8")
    assert agent.read_config(path)["source"] == "corrupt"
    path.write_text(json.dumps({"snapshot_interval_s": True}), encoding="utf-8")
    cfg = agent.read_config(path)
    assert (
        cfg["values"]["snapshot_interval_s"] == agent.DEFAULT_CONFIG_VALUES["snapshot_interval_s"]
    )
    assert cfg["invalid_fields"] == ["snapshot_interval_s"]


def test_read_config_defaults_override_for_cli_interval(tmp_path):
    cfg = agent.read_config(
        tmp_path / "pipeline.json",
        defaults={**agent.DEFAULT_CONFIG_VALUES, "snapshot_interval_s": 30},
    )
    assert cfg["values"]["snapshot_interval_s"] == 30


def test_render_supervisor_script_uses_config(tmp_path):
    script = agent.render_supervisor_script(
        tmp_path,
        {
            **agent.DEFAULT_CONFIG_VALUES,
            "session_gap_s": 120,
            "short_session_backoff_s": 900,
            "short_session_threshold_s": 60,
        },
    )
    assert "sleep 120" in script
    assert "sleep 900" in script
    assert "-lt 60" in script
    assert "sleep 600" not in script


# ── 重启请求：去重执行 + 持久化（add-factor-research-settings）──


def _write_restart_control(tmp_path, *, desired=True, request_id="r123", epoch=None):
    path = tmp_path / "control.json"
    payload = {"desired_running": desired}
    if request_id is not None:
        payload["restart_request_id"] = request_id
        payload["restart_requested_at_epoch"] = epoch or int(time.time())
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stub_restart(monkeypatch, *, running, stop_ok=True, start_ok=True):
    calls: list[str] = []
    monkeypatch.setattr(agent, "supervisor_pid", lambda workdir: 7752 if running else None)
    monkeypatch.setattr(
        agent, "stop_supervisor", lambda workdir: (calls.append("stop"), stop_ok)[1]
    )
    monkeypatch.setattr(
        agent, "start_supervisor", lambda workdir, config=None: (calls.append("start"), start_ok)[1]
    )
    return calls


def test_reconcile_restart_stops_then_starts_once(tmp_path, monkeypatch):
    calls = _stub_restart(monkeypatch, running=True)
    control = _write_restart_control(tmp_path, epoch=int(time.time()))

    assert agent.reconcile_once(tmp_path, control) == "started"
    assert calls == ["stop", "start"]
    state = json.loads(
        (tmp_path / "factor_research_logs" / agent.RESTART_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state["last_restart_request_id"] == "r123"
    assert state["last_restart_result"] == "restarted"

    # 同一请求下一周期不重复执行（幂等）
    assert agent.reconcile_once(tmp_path, control) == "noop"
    assert calls == ["stop", "start"]


def test_reconcile_restart_when_stopped_only_starts(tmp_path, monkeypatch):
    calls = _stub_restart(monkeypatch, running=False)
    control = _write_restart_control(tmp_path)
    assert agent.reconcile_once(tmp_path, control) == "started"
    assert calls == ["start"]


def test_reconcile_restart_ignored_when_desired_false(tmp_path, monkeypatch):
    calls = _stub_restart(monkeypatch, running=False)
    control = _write_restart_control(tmp_path, desired=False)
    assert agent.reconcile_once(tmp_path, control) == "noop"
    assert calls == []


def test_reconcile_restart_stop_failure_never_starts_second_supervisor(tmp_path, monkeypatch):
    calls = _stub_restart(monkeypatch, running=True, stop_ok=False)
    control = _write_restart_control(tmp_path)
    assert agent.reconcile_once(tmp_path, control) == "stop-failed"
    assert calls == ["stop"]
    state = json.loads(
        (tmp_path / "factor_research_logs" / agent.RESTART_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state["last_restart_result"] == "restart-failed"


def test_reconcile_restart_start_failure_recorded(tmp_path, monkeypatch):
    calls = _stub_restart(monkeypatch, running=False, start_ok=False)
    control = _write_restart_control(tmp_path)
    assert agent.reconcile_once(tmp_path, control) == "start-failed"
    assert calls == ["start"]
    state = json.loads(
        (tmp_path / "factor_research_logs" / agent.RESTART_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state["last_restart_result"] == "restart-failed"


def test_reconcile_restart_new_request_id_runs_again(tmp_path, monkeypatch):
    _stub_restart(monkeypatch, running=True)
    control = _write_restart_control(tmp_path, request_id="r1")
    assert agent.reconcile_once(tmp_path, control) == "started"
    control = _write_restart_control(tmp_path, request_id="r2")
    assert agent.reconcile_once(tmp_path, control) == "started"


def test_agent_meta_includes_config_and_restart(tmp_path):
    config = {
        "values": {**agent.DEFAULT_CONFIG_VALUES, "snapshot_interval_s": 30},
        "source": "file",
        "invalid_fields": [],
        "updated_at": None,
    }
    restart = {
        "request_id": "r123",
        "requested_at_epoch": 100,
        "result": "restarted",
        "handled_at": "2026-10-21T10:00:00+08:00",
        "handled_at_epoch": 105,
    }
    meta = agent._agent_meta("started", config=config, restart=restart)
    assert meta["config"]["values"]["snapshot_interval_s"] == 30
    assert meta["config"]["source"] == "file"
    assert meta["restart"] == restart
    assert isinstance(meta["last_action_epoch"], int)


def test_run_tick_reports_agent_config(tmp_path, monkeypatch):
    workdir = _seed_workdir(tmp_path)
    control = _write_control(tmp_path, False)
    out = tmp_path / "status.json"
    state = workdir / "factor_research_logs" / ".collector_state.json"
    monkeypatch.setattr(agent, "supervisor_pid", lambda w: None)

    _, snap = agent.run_tick(workdir, control, out, state)

    assert snap["agent"]["config"]["values"] == agent.DEFAULT_CONFIG_VALUES
    assert snap["agent"]["config"]["source"] == "defaults"
    assert snap["agent"]["restart"] is None


# ── TestPruneWorkspace：保留窗清理（incident 051 防复发 1/4）─────────────────


def _touch_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# candidate\n", encoding="utf-8")


def _set_mtime(path: Path, epoch: float) -> None:
    os.utime(path, (epoch, epoch))


def _disk_bytes(path: Path) -> int:
    """单文件占用字节（st_blocks*512；无 st_blocks 的平台按 apparent size 512 对齐）。"""
    st = path.stat()
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return ((st.st_size + 511) // 512) * 512
    return blocks * 512


class TestPruneWorkspace:
    """spec「会话产物保留窗清理」：删过期任务目录、保留窗内不碰、失败容忍不致崩。"""

    @staticmethod
    def _workspace(tmp_path) -> Path:
        return tmp_path / "git_ignore_folder" / "RD-Agent_workspace"

    def test_expired_deleted_and_fresh_kept(self, tmp_path):
        now = time.time()
        ws = self._workspace(tmp_path)
        _touch_file(ws / "task-old" / "factor.py")
        _touch_file(ws / "task-fresh" / "factor.py")
        _touch_file(ws / "loose-file.txt")  # workspace 根下的散文件不是任务目录
        _set_mtime(ws / "task-old" / "factor.py", now - 72 * 3600)
        _set_mtime(ws / "task-fresh" / "factor.py", now - 10 * 3600)
        _set_mtime(ws / "loose-file.txt", now - 72 * 3600)

        result = agent.prune_workspace(tmp_path, 48, now_epoch=now)

        assert result["deleted"] == ["task-old"]
        assert result["kept"] == 1
        assert result["failed"] == []
        assert not (ws / "task-old").exists()
        assert (ws / "task-fresh").exists()
        assert (ws / "loose-file.txt").exists()

    def test_boundary_exact_threshold_kept_one_second_earlier_deleted(self, tmp_path):
        now = 1_800_000_000.0
        ws = self._workspace(tmp_path)
        _touch_file(ws / "task-eq" / "factor.py")
        _touch_file(ws / "task-early" / "factor.py")
        cutoff = now - 48 * 3600
        _set_mtime(ws / "task-eq" / "factor.py", cutoff)  # 恰好等于阈值 → 留
        _set_mtime(ws / "task-early" / "factor.py", cutoff - 1)  # 低 1 秒 → 删

        result = agent.prune_workspace(tmp_path, 48, now_epoch=now)

        assert result["deleted"] == ["task-early"]
        assert result["kept"] == 1
        assert (ws / "task-eq").exists()

    def test_walk_error_recorded_and_others_pruned_with_freed_bytes(self, tmp_path, monkeypatch):
        """整树遍历失败 → 进 failed 不中断；freed_bytes=已删目录文件 st_blocks*512 之和。"""
        now = time.time()
        ws = self._workspace(tmp_path)
        _touch_file(ws / "task-bad" / "factor.py")
        _touch_file(ws / "task-old" / "factor.py")
        _set_mtime(ws / "task-bad" / "factor.py", now - 72 * 3600)
        _set_mtime(ws / "task-old" / "factor.py", now - 72 * 3600)

        real_walk = os.walk

        def fake_walk(path, *args, **kwargs):
            if Path(path).name == "task-bad":
                raise OSError("simulated I/O error")
            return real_walk(path, *args, **kwargs)

        monkeypatch.setattr(agent.os, "walk", fake_walk)

        expected = sum(_disk_bytes(p) for p in (ws / "task-old").rglob("*") if p.is_file())

        result = agent.prune_workspace(tmp_path, 48, now_epoch=now)

        assert result["deleted"] == ["task-old"]
        assert len(result["failed"]) == 1
        assert result["failed"][0]["path"].endswith("task-bad")
        assert result["failed"][0]["error"]
        assert result["freed_bytes"] == expected
        assert (ws / "task-bad").exists()

    def test_main_prune_prints_json_exit_zero(self, tmp_path, capsys):
        now = time.time()
        ws = self._workspace(tmp_path)
        _touch_file(ws / "task-old" / "factor.py")
        _set_mtime(ws / "task-old" / "factor.py", now - 72 * 3600)

        rc = agent.main(["--prune", "--workdir", str(tmp_path)])

        assert rc == 0
        data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert data["deleted"] == ["task-old"]
        assert data["kept"] == 0
        assert data["failed"] == []
        assert not (ws / "task-old").exists()


# ── TestDiskWatermark：低水位判定与暂停文件（incident 051 防复发 2/4）────────


class TestDiskWatermark:
    """spec「低水位停发保护」采集侧：阈值边界、失败置空 fail-open、暂停文件落盘。"""

    @staticmethod
    def _usage_fn(free_by_path: dict[str, int], error_path: str | None = None):
        """disk_usage 桩：按路径返回 (total, used, free)；error_path 抛 OSError，未知路径模拟不存在。"""

        def fn(path):
            if error_path is not None and str(path) == error_path:
                raise OSError("simulated statvfs failure")
            if str(path) not in free_by_path:
                raise FileNotFoundError(path)  # shutil.disk_usage 对缺失路径的同款行为
            return (10**12, 0, free_by_path[str(path)])

        return fn

    @staticmethod
    def _clear_threshold_env(monkeypatch):
        monkeypatch.delenv("FACTOR_DISK_MIN_FREE_GB_GUEST", raising=False)
        monkeypatch.delenv("FACTOR_DISK_MIN_FREE_GB_HOST", raising=False)
        monkeypatch.delenv("FACTOR_RESEARCH_HOST_MOUNT", raising=False)

    def test_boundary_exact_threshold_not_low_one_byte_low(self, monkeypatch):
        self._clear_threshold_env(monkeypatch)
        gb = 2**30
        w = agent.disk_watermark(
            guest_path="/guest",
            host_path="/host",
            disk_usage_fn=self._usage_fn({"/guest": 30 * gb, "/host": 20 * gb}),
        )
        assert w["low"] is False
        assert w["reason"] is None
        assert w["error"] is None
        assert w["guest_free_bytes"] == 30 * gb
        assert w["host_free_bytes"] == 20 * gb
        assert w["thresholds"] == {"guest_min_bytes": 30 * gb, "host_min_bytes": 20 * gb}

        w = agent.disk_watermark(
            guest_path="/guest",
            host_path="/host",
            disk_usage_fn=self._usage_fn({"/guest": 30 * gb - 1, "/host": 20 * gb}),
        )
        assert w["low"] is True and w["reason"] == "guest"

        w = agent.disk_watermark(
            guest_path="/guest",
            host_path="/host",
            disk_usage_fn=self._usage_fn({"/guest": 30 * gb, "/host": 20 * gb - 1}),
        )
        assert w["low"] is True and w["reason"] == "host"

    def test_single_side_oserror_none_and_not_low(self, monkeypatch):
        """单侧失败 → 该侧 None（fail-open 不计 low），另一侧照常判定。"""
        self._clear_threshold_env(monkeypatch)
        gb = 2**30
        w = agent.disk_watermark(
            guest_path="/guest",
            host_path="/host",
            disk_usage_fn=self._usage_fn({"/host": 40 * gb}, error_path="/guest"),
        )
        assert w["guest_free_bytes"] is None
        assert w["host_free_bytes"] == 40 * gb
        assert w["low"] is False and w["reason"] is None

        # host 路径不存在 → 同样置 None（不猜）
        w = agent.disk_watermark(
            guest_path="/guest",
            host_path="/host-absent",
            disk_usage_fn=self._usage_fn({"/guest": 40 * gb}),
        )
        assert w["host_free_bytes"] is None
        assert w["low"] is False

    def test_both_sides_fail_error_recorded_fail_open(self, monkeypatch):
        self._clear_threshold_env(monkeypatch)
        w = agent.disk_watermark(
            guest_path="/x",
            host_path="/y",
            disk_usage_fn=self._usage_fn({}, error_path="/y"),
        )
        assert w["guest_free_bytes"] is None
        assert w["host_free_bytes"] is None
        assert w["low"] is False
        assert w["error"]
        assert w["reason"] is None

    def test_env_threshold_override(self, monkeypatch):
        gb = 2**30
        monkeypatch.setenv("FACTOR_DISK_MIN_FREE_GB_GUEST", "5")
        monkeypatch.setenv("FACTOR_DISK_MIN_FREE_GB_HOST", "1")
        w = agent.disk_watermark(
            guest_path="/guest",
            host_path="/host",
            disk_usage_fn=self._usage_fn({"/guest": 6 * gb, "/host": 1 * gb}),
        )
        assert w["thresholds"] == {"guest_min_bytes": 5 * gb, "host_min_bytes": 1 * gb}
        assert w["low"] is False  # host 恰等覆盖后的阈值 → 不 low

    def test_main_disk_check_writes_pause_file_and_exit_codes(self, tmp_path, monkeypatch, capsys):
        """低水位 → paused=true 写盘 exit 2；正常 → paused=false 覆盖残留；写失败 → exit 1。"""
        monkeypatch.setenv("FACTOR_RESEARCH_HOST_MOUNT", str(tmp_path))
        monkeypatch.setenv("FACTOR_DISK_MIN_FREE_GB_GUEST", "100000000")  # 必然低水位
        monkeypatch.setenv("FACTOR_DISK_MIN_FREE_GB_HOST", "0")

        rc = agent.main(["--disk-check", "--workdir", str(tmp_path)])

        assert rc == 2
        pause_path = tmp_path / "factor_research_logs" / ".disk_pause.json"
        pause = json.loads(pause_path.read_text(encoding="utf-8"))
        assert pause["paused"] is True and pause["reason"] == "guest"
        assert set(pause) == {
            "paused",
            "reason",
            "guest_free_bytes",
            "host_free_bytes",
            "checked_at",
        }

        # 正常（阈值 0 → 永不低水位）也写 paused=false，防陈旧暂停态残留
        monkeypatch.setenv("FACTOR_DISK_MIN_FREE_GB_GUEST", "0")
        rc = agent.main(["--disk-check", "--workdir", str(tmp_path)])
        assert rc == 0
        pause = json.loads(pause_path.read_text(encoding="utf-8"))
        assert pause["paused"] is False and pause["reason"] is None

        # 暂停文件写失败 → exit 1
        def broken_write(*args, **kwargs):
            raise OSError("simulated write failure")

        monkeypatch.setenv("FACTOR_DISK_MIN_FREE_GB_GUEST", "100000000")
        monkeypatch.setattr(agent.cfs, "atomic_write_json", broken_write)
        rc = agent.main(["--disk-check", "--workdir", str(tmp_path)])
        assert rc == 1
        assert "FAILED" in capsys.readouterr().err


# ── TestSupervisorRender：看护脚本接线（incident 051 防复发 3/4）──────────────


def _bash_usable() -> bool:
    """bash 可执行性探测：Windows 的 msys bash 在部分原生进程环境下 fork 自爆（0xC0000142）。"""
    bash = shutil.which("bash")
    if bash is None:
        return False
    try:
        probe = subprocess.run(  # noqa: S603 - bash 探测（PATH 解析），无外部输入
            ["bash", "-c", "true"],  # noqa: S607 - bash 走 PATH 解析（同 agent 模块先例）
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


class TestSupervisorRender:
    """prune 与低水位停发进看护循环；既有 5 占位符替换语义不回归。"""

    def test_rendered_script_gates_launch_and_prunes_after_session(self, tmp_path):
        script = agent.render_supervisor_script(tmp_path)
        # 计划逐字条款：launch 前低水位门禁；会话结束日志行后清理
        gate = (
            f'if ! python3 "{agent.AGENT_SCRIPT_PATH}" --disk-check; '
            f"then sleep {agent.DISK_CHECK_BACKOFF_S}; continue; fi"
        )
        assert gate in script
        prune_line = (
            f'python3 "{agent.AGENT_SCRIPT_PATH}" --prune >> "$LOGDIR/prune.log" 2>&1 || true'
        )
        assert prune_line in script
        # 顺序约束：门禁在 launch 之前；prune 在 session ended 日志之后
        assert script.index("--disk-check") < script.index(f"bash {agent.LAUNCH_SCRIPT}")
        assert script.index("--prune") > script.index("[supervisor] session ended")

    def test_legacy_placeholders_still_replaced(self, tmp_path):
        script = agent.render_supervisor_script(tmp_path)
        for placeholder in (
            "{{WORKDIR}}",
            "{{LAUNCH}}",
            "{{SHORT_THRESHOLD}}",
            "{{SHORT_BACKOFF}}",
            "{{SESSION_GAP}}",
            "{{AGENT}}",
            "{{DISK_BACKOFF}}",
        ):
            assert placeholder not in script
        assert str(tmp_path) in script
        assert agent.LAUNCH_SCRIPT in script
        assert "-lt 120" in script  # 默认 short_session_threshold_s
        assert "sleep 600 " in script  # 默认短命退避（带尾注）
        assert "\n    sleep 60\n" in script  # 默认会话间隔

    @pytest.mark.skipif(
        not _bash_usable(), reason="环境 bash 不可执行（无 bash 或 msys fork 故障）"
    )
    def test_rendered_script_passes_bash_syntax_check(self, tmp_path):
        script = agent.render_supervisor_script(tmp_path)
        result = subprocess.run(  # noqa: S603 - bash -n 语法校验，无外部输入
            ["bash", "-n"],  # noqa: S607 - bash 走 PATH 解析（同 agent 模块先例）
            input=script.encode("utf-8"),
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
