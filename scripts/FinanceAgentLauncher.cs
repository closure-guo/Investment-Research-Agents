// 研发看护无窗口启动器（delta add-factor-research-selfheal）。
// 由计划任务调用，代替 powershell.exe -WindowStyle Hidden —— Windows 11 默认终端为
// Windows Terminal 时控制台被委托给 WindowsTerminal.exe -Embedding，SW_HIDE 被忽略，
// 每 2 分钟会弹出可见窗口。GUI 子系统进程 + CreateNoWindow 不创建任何控制台。
// 参数（均可省略）：<distro> <远程脚本> <日志路径>
// 编译：scripts/register-factor-research-task.ps1 自动执行
//   csc.exe /nologo /target:winexe /out:FinanceAgentLauncher.exe scripts/FinanceAgentLauncher.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

static class Program
{
    private static int Main(string[] args)
    {
        string distro = args.Length > 0 && args[0].Length > 0 ? args[0] : "rdagent";
        string remoteScript = args.Length > 1 && args[1].Length > 0
            ? args[1]
            : "/root/ensure-factor-research.sh";
        string logPath = args.Length > 2 && args[2].Length > 0
            ? args[2]
            : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launcher-task.log");

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
