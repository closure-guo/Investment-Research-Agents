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
import time

import factor_research_agent as agent

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
