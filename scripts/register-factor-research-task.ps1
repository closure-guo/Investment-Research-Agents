#Requires -Version 5.1
<#
.SYNOPSIS
  注册/卸载研发看护自愈启动器（delta add-factor-research-selfheal）。
.DESCRIPTION
  用户登录时 + 每 2 分钟调用 distro ensure 入口：确保代理守护存活并写心跳。
  幂等：重复执行更新同名任务；-Unregister 卸载。
  动作使用无窗口 helper（GUI 子系统 + CreateNoWindow）—— 不要改回
  `powershell.exe -WindowStyle Hidden`：Windows 11 默认终端下每 2 分钟会弹出可见控制台。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/register-factor-research-task.ps1
  powershell -ExecutionPolicy Bypass -File scripts/register-factor-research-task.ps1 -Unregister
#>
param(
  [switch]$Unregister,
  [string]$TaskName = 'investment-research-agents-factor-research-launcher',
  [string]$Distro = 'rdagent',
  [string]$RemoteScript = '/root/ensure-factor-research.sh',
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

# 无窗口 helper：GUI 子系统进程，CreateNoWindow 调 wsl，不产生可见控制台
$repoRoot = Split-Path -Parent $PSScriptRoot
$helperDir = Join-Path $env:LOCALAPPDATA 'FinanceAgent\launcher'
$helperSource = Join-Path $PSScriptRoot 'FinanceAgentLauncher.cs'
$helperExe = Join-Path $helperDir 'FinanceAgentLauncher.exe'
$taskLog = Join-Path $repoRoot 'data\factor_research\launcher-task.log'
# delta add-factor-research-distro-liveness-probe：探活文件（宿主侧写）与状态快照（判「采集同时停摆」）
$probeFile = Join-Path $repoRoot 'data\factor_research\distro_probe.json'
$statusFile = Join-Path $repoRoot 'data\factor_research\status.json'

function Initialize-LauncherExe {
  if (-not (Test-Path $helperSource)) { throw "launcher source not found: $helperSource" }
  $needsBuild = (-not (Test-Path $helperExe)) -or
    ((Get-Item $helperExe).LastWriteTimeUtc -lt (Get-Item $helperSource).LastWriteTimeUtc)
  if (-not $needsBuild) { return }

  $csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
  if (-not (Test-Path $csc)) { throw "csc.exe not found: $csc" }

  New-Item -ItemType Directory -Force -Path $helperDir | Out-Null
  $tmpExe = Join-Path $helperDir 'FinanceAgentLauncher.tmp.exe'
  Remove-Item $tmpExe -ErrorAction SilentlyContinue
  & $csc /nologo /codepage:65001 /target:winexe "/out:$tmpExe" "$helperSource" | Out-Null
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmpExe)) {
    throw "launcher build failed (csc exit $LASTEXITCODE)"
  }

  # 任务每 2 分钟会跑该 exe，替换撞上运行时重试
  for ($i = 0; $i -lt 20; $i++) {
    try {
      Move-Item -Force $tmpExe $helperExe
      Write-Output "built: $helperExe"
      return
    } catch {
      Start-Sleep -Milliseconds 250
    }
  }
  throw "cannot replace $helperExe (still in use?)"
}

Initialize-LauncherExe

$action = New-ScheduledTaskAction -Execute $helperExe `
  -Argument "$Distro $RemoteScript `"$taskLog`" `"$probeFile`" `"$statusFile`""

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
