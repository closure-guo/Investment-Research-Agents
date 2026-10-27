// 研发看护无窗口启动器（delta add-factor-research-selfheal）。
// 由计划任务调用，代替 powershell.exe -WindowStyle Hidden —— Windows 11 默认终端为
// Windows Terminal 时控制台被委托给 WindowsTerminal.exe -Embedding，SW_HIDE 被忽略，
// 每 2 分钟会弹出可见窗口。GUI 子系统进程 + CreateNoWindow 不创建任何控制台。
//
// 参数（均可省略）：<distro> <远程脚本> <日志路径> <探活文件路径> <状态快照路径>
// 编译：scripts/register-factor-research-task.ps1 自动执行
//   csc.exe /nologo /target:winexe /out:FinanceAgentLauncher.exe scripts/FinanceAgentLauncher.cs
//
// delta add-factor-research-distro-liveness-probe（2026-10-22 incident 049）：
// 每节拍先探活 `wsl -d <distro> -e /bin/true`，把结果写宿主侧 distro_probe.json；
// 仅当「连续探活失败 ≥15 次 ∧ 状态快照停摆 ≥900 秒 ∧ 距上次自愈 ≥1800 秒」三条件同时成立时，
// 才执行一次 `wsl --terminate <distro>`。单次探活失败不足以判死——历史 839 次节拍中 611 次
// （72.8%）非零退出（WSL relay 超时）而管线仍在跑，故必须叠加「快照同时停摆」这一互斥条件。
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

static class Program
{
    // 探活超时（毫秒）
    private const int ProbeTimeoutMs = 20000;
    // 自愈门控：连续失败次数 / 快照停摆秒数 / 自愈冷却秒数
    private const int HealAfterFailures = 15;
    private const int HealSnapshotStaleS = 900;
    private const int HealCooldownS = 1800;
    // detail 摘要截断长度
    private const int DetailMaxChars = 200;

    private static int Main(string[] args)
    {
        string distro = args.Length > 0 && args[0].Length > 0 ? args[0] : "rdagent";
        string remoteScript = args.Length > 1 && args[1].Length > 0
            ? args[1]
            : "/root/ensure-factor-research.sh";
        string logPath = args.Length > 2 && args[2].Length > 0
            ? args[2]
            : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launcher-task.log");
        string probePath = args.Length > 3 && args[3].Length > 0
            ? args[3]
            : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "distro_probe.json");
        string statusPath = args.Length > 4 && args[4].Length > 0 ? args[4] : "";

        // 探活 + 门控自愈（失败绝不影响下面的 ensure 流程）
        try
        {
            ProbeAndMaybeHeal(distro, probePath, statusPath, logPath);
        }
        catch (Exception ex)
        {
            Log(logPath, "PROBE-EXCEPTION: " + ex.Message);
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = @"C:\Windows\System32\wsl.exe",
                Arguments = string.Format("-d {0} -u root -e bash {1}", distro, remoteScript),
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = @"C:\Windows\System32",
            };

            using (Process p = Process.Start(psi))
            {
                // 异步读双流，避免缓冲区互锁
                var outTask = p.StandardOutput.ReadToEndAsync();
                var errTask = p.StandardError.ReadToEndAsync();
                p.WaitForExit();

                string stdout = outTask.Result.Trim();
                string stderr = errTask.Result.Trim();

                if (p.ExitCode != 0 || stderr.Length > 0)
                {
                    Log(logPath, string.Format(
                        "exit={0}{1}stdout: {2}{1}stderr: {3}",
                        p.ExitCode,
                        Environment.NewLine,
                        stdout.Length > 0 ? stdout : "(empty)",
                        stderr.Length > 0 ? stderr : "(empty)"));
                }

                return p.ExitCode;
            }
        }
        catch (Exception ex)
        {
            Log(logPath, "EXCEPTION: " + ex);
            return 1;
        }
    }

    /// <summary>探活 distro 可执行性；按三条件门控决定是否终止 distro；结果写探活文件。</summary>
    private static void ProbeAndMaybeHeal(string distro, string probePath, string statusPath, string logPath)
    {
        bool ok = false;
        int exitCode = -1;
        string detail = null;

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = @"C:\Windows\System32\wsl.exe",
                Arguments = string.Format("-d {0} -e /bin/true", distro),
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = @"C:\Windows\System32",
            };

            using (Process p = Process.Start(psi))
            {
                var outTask = p.StandardOutput.ReadToEndAsync();
                var errTask = p.StandardError.ReadToEndAsync();
                if (p.WaitForExit(ProbeTimeoutMs))
                {
                    exitCode = p.ExitCode;
                    ok = exitCode == 0;
                    if (!ok)
                    {
                        string err = errTask.Result.Trim();
                        detail = Truncate(err.Length > 0 ? err : outTask.Result.Trim());
                    }
                }
                else
                {
                    try { p.Kill(); } catch { }
                    exitCode = -1;
                    detail = "probe timeout after " + ProbeTimeoutMs + "ms";
                }
            }
        }
        catch (Exception ex)
        {
            exitCode = -1;
            detail = Truncate(ex.Message);
        }

        // delta add-factor-workspace-retention-disk-watch（incident 051）：exec 探针可被
        // WSL 页缓存假阴性命中（/bin/true 走缓存成功而 ext4 文件系统 I/O 实际全挂）。
        // exec 成功后追加读写探针：distro 内创建并删除临时文件，验证真实读写。
        // 失败 → ok=false、exitCode 记 rw 退出码、detail=fs_rw_failed，与 exec 失败同走
        // 自愈门控（计入 consecutive_failures）；exec 失败路径行为不变。
        if (ok)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = @"C:\Windows\System32\wsl.exe",
                    Arguments = string.Format(
                        "-d {0} -e /bin/sh -c \": > /tmp/.factor_probe_rw && rm /tmp/.factor_probe_rw\"",
                        distro),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WorkingDirectory = @"C:\Windows\System32",
                };

                using (Process p = Process.Start(psi))
                {
                    var outTask = p.StandardOutput.ReadToEndAsync();
                    var errTask = p.StandardError.ReadToEndAsync();
                    if (p.WaitForExit(ProbeTimeoutMs))
                    {
                        exitCode = p.ExitCode;
                        ok = exitCode == 0;
                        if (!ok)
                        {
                            detail = "fs_rw_failed";
                        }
                    }
                    else
                    {
                        try { p.Kill(); } catch { }
                        exitCode = -1;
                        ok = false;
                        detail = "fs_rw_failed";
                    }
                }
            }
            catch
            {
                exitCode = -1;
                ok = false;
                detail = "fs_rw_failed";
            }
        }

        // 读取上一次探活状态（best effort：自愈计数与冷却依赖它，缺失即从 0 起）
        int prevFailures = 0;
        long prevTerminatedEpoch = 0;
        int prevTerminateCount = 0;
        try
        {
            if (File.Exists(probePath))
            {
                string prev = File.ReadAllText(probePath, Encoding.UTF8);
                prevFailures = ReadInt(prev, "consecutive_failures");
                prevTerminatedEpoch = ReadLong(prev, "terminated_at_epoch");
                prevTerminateCount = ReadInt(prev, "terminate_count");
            }
        }
        catch { }

        int failures = ok ? 0 : prevFailures + 1;
        long nowEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        bool snapshotStale = SnapshotStale(statusPath, nowEpoch, HealSnapshotStaleS);
        bool heal = ShouldHeal(ok, failures, snapshotStale, prevTerminatedEpoch, nowEpoch);

        long terminatedEpoch = prevTerminatedEpoch;
        int terminateCount = prevTerminateCount;
        if (heal)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = @"C:\Windows\System32\wsl.exe",
                    Arguments = string.Format("--terminate {0}", distro),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WorkingDirectory = @"C:\Windows\System32",
                };
                using (Process p = Process.Start(psi))
                {
                    p.StandardOutput.ReadToEnd();
                    p.StandardError.ReadToEnd();
                    p.WaitForExit(ProbeTimeoutMs);
                }
                terminatedEpoch = nowEpoch;
                terminateCount = prevTerminateCount + 1;
                Log(logPath, string.Format(
                    "HEAL: distro {0} 探活连续失败 {1} 次且快照停摆 >= {2}s → 已执行 wsl --terminate（第 {3} 次）",
                    distro, failures, HealSnapshotStaleS, terminateCount));
            }
            catch (Exception ex)
            {
                Log(logPath, "HEAL-EXCEPTION: " + ex.Message);
            }
        }

        var sb = new StringBuilder();
        sb.Append("{");
        sb.Append("\"probed_at\":\"").Append(Escape(DateTimeOffset.Now.ToString("yyyy-MM-dd'T'HH:mm:sszzz"))).Append("\",");
        sb.Append("\"probed_at_epoch\":").Append(nowEpoch).Append(",");
        sb.Append("\"distro\":\"").Append(Escape(distro)).Append("\",");
        sb.Append("\"ok\":").Append(ok ? "true" : "false").Append(",");
        sb.Append("\"exit_code\":").Append(exitCode).Append(",");
        sb.Append("\"detail\":").Append(detail == null ? "null" : "\"" + Escape(detail) + "\"").Append(",");
        sb.Append("\"consecutive_failures\":").Append(failures).Append(",");
        sb.Append("\"snapshot_stale\":").Append(snapshotStale ? "true" : "false").Append(",");
        sb.Append("\"terminated_at\":").Append(
            terminatedEpoch > 0
                ? "\"" + Escape(DateTimeOffset.FromUnixTimeSeconds(terminatedEpoch).ToLocalTime()
                    .ToString("yyyy-MM-dd'T'HH:mm:sszzz")) + "\""
                : "null").Append(",");
        sb.Append("\"terminated_at_epoch\":").Append(terminatedEpoch).Append(",");
        sb.Append("\"terminate_count\":").Append(terminateCount);
        sb.Append("}");

        WriteAtomic(probePath, sb.ToString());
    }

    /// <summary>
    /// 自愈门控（纯函数，便于真值表验证）：三条件**合取**，任一不成立即 MUST NOT 动作。
    /// 单次探活失败不构成「distro 已死」——历史 839 次节拍中 611 次非零退出而管线在跑，
    /// 故必须叠加「状态快照同时停摆」这一健康态下不可能成立的条件。
    /// </summary>
    internal static bool ShouldHeal(
        bool ok, int failures, bool snapshotStale, long lastTerminatedEpoch, long nowEpoch)
    {
        if (ok) return false;
        if (failures < HealAfterFailures) return false;
        if (!snapshotStale) return false;
        if (lastTerminatedEpoch > 0 && (nowEpoch - lastTerminatedEpoch) < HealCooldownS) return false;
        return true;
    }

    /// <summary>状态快照是否已停摆 >= staleSeconds（读不到快照 → 视为停摆，由调用方与失败计数合取）。</summary>
    private static bool SnapshotStale(string statusPath, long nowEpoch, int staleSeconds)
    {
        if (string.IsNullOrEmpty(statusPath)) return false;
        try
        {
            if (!File.Exists(statusPath)) return true;
            string text = File.ReadAllText(statusPath, Encoding.UTF8);
            long epoch = ReadLong(text, "collected_at_epoch");
            if (epoch <= 0) return true;
            return (nowEpoch - epoch) >= staleSeconds;
        }
        catch
        {
            return true;
        }
    }

    private static int ReadInt(string json, string key)
    {
        long v = ReadLong(json, key);
        return v > int.MaxValue ? int.MaxValue : (int)v;
    }

    private static long ReadLong(string json, string key)
    {
        Match m = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*(\\d+)");
        long v;
        return m.Success && long.TryParse(m.Groups[1].Value, NumberStyles.Integer,
            CultureInfo.InvariantCulture, out v) ? v : 0;
    }

    private static string Truncate(string s)
    {
        if (string.IsNullOrEmpty(s)) return null;
        s = s.Replace("\r", " ").Replace("\n", " ").Trim();
        return s.Length <= DetailMaxChars ? s : s.Substring(0, DetailMaxChars) + "…";
    }

    private static string Escape(string s)
    {
        if (s == null) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    /// <summary>原子写（临时文件 + 替换）；失败不抛出，避免影响看护节拍。</summary>
    private static void WriteAtomic(string path, string content)
    {
        try
        {
            string dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, content, new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(tmp, path, null);
            else File.Move(tmp, path);
        }
        catch
        {
            // 探活写失败不得阻断看护（fail-safe）
        }
    }

    private static void Log(string logPath, string message)
    {
        try
        {
            string dir = Path.GetDirectoryName(logPath);
            if (!string.IsNullOrEmpty(dir))
            {
                Directory.CreateDirectory(dir);
            }

            if (File.Exists(logPath) && new FileInfo(logPath).Length > 2 * 1024 * 1024)
            {
                File.Delete(logPath);
            }

            File.AppendAllText(
                logPath,
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine,
                Encoding.UTF8);
        }
        catch
        {
            // 日志失败不得影响看护节拍
        }
    }
}
