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


def test_launcher_cs_probes_fs_rw():
    """探活读写双探针（delta add-factor-workspace-retention-disk-watch，incident 051）。

    exec 探针（/bin/true）可被 WSL 页缓存假阴性命中——/bin/true 可执行不代表文件系统
    可读写。探活必须在 exec 成功后追加真实读写验证（distro 内创建并删除临时文件
    .factor_probe_rw），失败原因区分 ``fs_rw_failed``（spec「distro 探活覆盖页缓存假阴性」）。
    """
    text = (REPO / "scripts" / "FinanceAgentLauncher.cs").read_text(encoding="utf-8")
    assert "factor_probe_rw" in text
    assert "fs_rw_failed" in text
