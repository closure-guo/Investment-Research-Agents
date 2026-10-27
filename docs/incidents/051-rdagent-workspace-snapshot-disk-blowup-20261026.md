# incident 051：RD-Agent 会话快照无清理 → 虚拟盘 817GB 打爆宿主 D: → distro I/O 损坏、管线停摆（2026-10-26）

> 同族：[047](047-wsl-memory-exhaustion-container-enomem-20261021.md)（VM 内存）、[049](049-pandarallel-dev-shm-exhaustion-distro-io-error-20261022.md)（tmpfs / distro I/O error）。
> 本起补上该族缺的第三块资源：**磁盘**。049 的教训清单治理了内存与 tmpfs，唯独没治磁盘水位。

## 现象

1. 宿主 D:（1.6TB）用到 **99%，仅剩 18.7GB**；占用大头 `D:\WSL\rdagent\ext4.vhdx` = **816.8 GiB（非稀疏）**。
2. 该 vhdx 在 10-22 12:04 incident 049 迁移（`wsl --manage rdagent --move D:\WSL\rdagent`）时仅 **102.7 GiB**——
   **4.4 天膨胀 7 倍（+714 GiB ≈ 160GB/天）**。迁移前备份 `D:\WSL\backup\rdagent-20261022\ext4.vhdx`（102.7 GiB）另占一份。
3. 10-26 **20:56** 起 distro 进入第三种不可用形态：
   - 采集器快照停摆（`status.json` 最后写入 20:56），管线中断 2.5h+；
   - guest 内 `/usr/bin/df`、`/bin/ls`、`/usr/bin/python3` 等 exec 全部 `I/O error`，**python 进程 mmap 命中坏块直接 SIGBUS（exit 135）**——自愈启动器日志里连续 `exit=135` 的来源；
   - 但**探针 `/bin/true` 仍 `ok=true`**（小二进制恰在页缓存）⇒ 现有 distro 探活出现**假阴性**，监控面不可判；
   - kmsg 唯一存留行：`I/O error, dev sdd, sector 16986112 op 0x0:(READ)`。
4. guest ext4 自认为还有 **140.9 GiB 空闲**（total 1006.9 GiB）——「客人以为有空间，宿主已无力支撑」。
5. 连带：生产栈同窗口不可达（:8000 无响应、docker engine 500）——疑似被同一宿主卷耗尽拖累（未做单独归因）。

## 根因

### 占用大户（精确定位）

`/root/rdagent-work/git_ignore_folder/RD-Agent_workspace/<uuid>/`，共 **4,114 个会话快照目录 ≈ 820GB**
（可测 569.7 GiB + ~850 个 lstat 失败文件 ≈245GB，损坏区=最近写入区）：

| 构成 | 份数 | 体积 |
|---|---|---|
| `combined_factors_df.parquet`（全市场×全日期因子面板） | 949 | 357 GB（单份 0.4–1.9 GB） |
| `*.h5`（result.h5 等回测产物） | 4,587 | 210 GB |
| 其余（py/yaml/md/log） | — | ≈1 GB |

RD-Agent 的 `Workspace` 语义 = 每个任务在 `cwd/git_ignore_folder/RD-Agent_workspace/` 下新建 uuid 目录、
把面板与回测产物写进去，**全链路（RD-Agent 本体 + 本仓库启动器/看护）零清理**。
会话目录在本项目的唯一消费方是监控计数（`collect_factor_status._workspace_totals` 的任务数/候选数）；
生产收割走宿主 `data/factor_research/*.json`（formulation 在 JSON 内）——**旧目录可安全删除**。

按日增长（文件 mtime 分桶）：10-20：9.2 / 10-21：14.3 / **10-22：127.7** / 10-23：94.7 / 10-24：131.8 / 10-25：77.9 / 10-26：111.8 GB。

### 为什么 10-22 起加速 8 倍（14GB/天 → 110–130GB/天）

不是泄漏（`.pandarallel_mem` 实测仅 0.54GB；`/tmp` 3,400 个临时目录仅 ~30MB）。双因叠加：

1. **incident 049 的 IC 闭包修复（12:30 上线）让会话第一次跑完 combine→backtest 全程**——此前会话死在
   IC dedup，不产出大文件；修复后每次完整会话稳定产出 1.5–1.9GB parquet + h5。磁盘爆炸是那次修复的副作用；
2. 同日 distro 从只剩几 GB 的 C: 迁到 D:，解除了增长阻力。

### 为什么写坏（机制链）

guest ext4 有 140.9 GiB 空闲 ⇒ 持续写入 ⇒ vhdx 需要同步扩张；宿主 D: 只剩 18.7GB ⇒
**VHDX 无法分配新块** ⇒ guest 块设备 READ/WRITE EIO ⇒ ext4 部分块不可读（`/usr` 370+、`/opt` 405 个 stat 失败）⇒
python mmap SIGBUS。`.wslconfig` 的 `sparseVhd=true` **只对新建盘生效**，迁来的盘非稀疏、从不自动回收。

## 处置（2026-10-26 深夜，本起全记录）

1. **停自愈启动器**（`schtasks /change /disable`），防止维护窗口内拉起会话。
2. **恢复 distro**：`wsl --terminate rdagent` 无效（`CreateInstance/E_FAIL`，错误码 6 失败步骤 2 = vhdx 挂载失败）；
   `wsl --shutdown` 全 VM 重启后 distro **完全恢复**（二进制全可用、可写）——损坏是运行态（块层被写爆卡死），非永久 fs 损伤。
3. **删旧快照**：保 48h（按每目录最新 mtime 判定），**删 3,295 目录 / 释放 389.95 GiB / 0 失败**，保留 819；
   `fstrim -av` 完成；ext4 空闲 140.9 → **530.9 GiB**。清单落 `D:\tmp\cleanup_deleted_names.log`。
4. **压缩 vhdx**：`diskpart compact` 两度被「文件正在使用」拦下——**有外部机制在秒级拉起 rdagent distro**
   （Docker Desktop WSL 集成自动拉起为最强嫌疑，未最终定罪；`wsl --manage --set-sparse` 亦被
   `WSL_E_DISTRO_NOT_STOPPED` 拦下）。最终以「停 WslService + taskkill vmmemWSL/wslhost/wslrelay +
   同进程内立刻 diskpart attach 抢锁」完成：**vhdx 816.8 GiB → 428 GiB**（压实 12 分钟，attach 期间任何
   distro 拉起会失败，无害），**宿主 D: 空闲 18.7 → 407.4 GB**。
5. **重启生产栈 + 恢复看护（00:36–00:41 全部验证通过）**：
   - Docker Desktop 拉起后引擎 10s 内恢复；compose 基础设施容器经 restart 策略自启，backend/frontend 需
     `docker compose up -d` 补拉，最终 8 容器全 Up（backend healthy）、`/api/sessions` 200；
     435 会话 0 running（24 个 interrupted 为事故窗口遗留，按既有机制处置）；
   - 重新启用启动器计划任务并手动触发：supervisor 恢复（pid 17，00:39:08），新会话
     `session-20261027-003908` 在跑（loop 0 / coding），探活 `ok=true`、快照新鲜（`snapshot_stale=false`），
     采集器恢复 15s 节拍；D: 空闲稳定 407.4 GB。

## 教训与防护

- **guest 空闲 ≠ 宿主空闲**：ext4 报 140GB 空闲时宿主只剩 18.7GB；「写入还能撑多久」由宿主决定。
  研发 distro 的磁盘水位必须在**宿主侧**监控（047 内存、049 tmpfs、本起磁盘——同一族三块拼图）。
- **RD-Agent 每会话全量物化面板**（1.5–1.9GB parquet + h5）且零保留策略：任何长跑部署必须配保留窗清理。
- **探针假阴性第三形态**：`/bin/true` 在页缓存可成功，而 fs 大面积不可读；探活应加「读写真实文件」探针
  （049 教训「VM 活着 ≠ distro 可用」的延伸：**探针活着 ≠ fs 可用**）。
- **WSL 后台进程不跨界存活**：`nohup … &` 后 wsl.exe 客户端退出 ⇒ distro 空闲回收 ⇒ 进程被杀
  （本次清理脚本首跑即死于该机制）。guest 内长任务必须挂在存活客户端下跑。
- **vhdx 独占操作（diskpart / --set-sparse）需要 distro 全停**，且存在外部自动拉起源（Docker 集成嫌疑最大）——
  维护前先停 Docker Desktop 全家（进程 + 服务）再抢锁。
- **terminate 单独无法恢复 I/O 损坏态**（E_FAIL 挂载失败），必须 `wsl --shutdown` 全 VM 重启。

## 待办（挂账）

1. ~~**防复发 delta**~~ **已落地**（2026-10-27）：delta `add-factor-workspace-retention-disk-watch`
   已实施、部署并验证——保留窗清理（48h，看护每会话结束 prune，实跑删 10 目录/5.6GB）+
   宿主/guest 双侧磁盘水位采集与低水位停发（30/20GB）+ 探活读写双探针；门禁
   pytest 190 / vitest 47 / E2E 3（专属端口对）全绿；验证报告
   `tests/validation/2026-10-27-add-factor-workspace-retention-disk-watch-validation.md`。
   部署备忘：**代理守护是常驻进程，supervisor restart 不换其内存模块**——部署采集器/代理
   改动后必须 kill 守护，由启动器 2 分钟节拍重拉。
2. 102.7 GiB 迁移备份 `D:\WSL\backup\rdagent-20261022\ext4.vhdx`：**本起活盘损坏期间是唯一干净副本，未删**；
   待压实完成 + 新盘观察稳定后清理。
3. vhdx 周期性压实/稀疏化运维任务（与 #1 的水位监控联动）。
4. 生产栈在本窗口的不可达（docker engine 500）未单独归因，随恢复验证一并观察。

## 关联

- 同族：047（VM 内存上限）、049（tmpfs + distro I/O error + 僵死判定 + 探活探针）
- 环境与排障手册：`tests/scripts/spike/README.md`；启动器：`tests/scripts/spike/launch-fin-factor.sh`
- 契约：`openspec/specs/factor-research-monitor/spec.md`
