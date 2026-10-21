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


def test_register_script_uses_windowless_helper():
    """动作必须是 GUI 子系统 helper，不得退回 powershell.exe -WindowStyle Hidden。

    Windows 11 默认终端为 Windows Terminal 时 SW_HIDE 被忽略，任务每 2 分钟
    弹出可见控制台（2026-10-21 任务改名重建后复发过）。
    """
    text = (REPO / "scripts" / "register-factor-research-task.ps1").read_text(encoding="utf-8")
    assert "FinanceAgentLauncher.cs" in text
    assert "New-ScheduledTaskAction -Execute $helperExe" in text
    assert "-Execute 'powershell.exe'" not in text
    assert (REPO / "scripts" / "FinanceAgentLauncher.cs").exists()
