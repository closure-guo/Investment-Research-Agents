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
