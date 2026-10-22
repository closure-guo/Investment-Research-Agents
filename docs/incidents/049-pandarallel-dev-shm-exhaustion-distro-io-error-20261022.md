# incident 049：因子研发会话崩于 /dev/shm（tmpfs）耗尽 → distro 进入 I/O 错误态、自愈无法恢复（2026-10-22）

> 编号说明：本起原拟编 048，与同日另一起 [048](048-prompt-drift-unmerged-branch-20261022.md)
> （prompt 漂移）撞号，改编 **049**。

> **本文件初稿的更正声明（同日）**：初稿据「宿主 C: 占用 96%」推断宿主卷耗尽是主因，并称
> `docker_data.vhdx` 也在 C:、是两个「炸弹」。经 junction 解析与 pandarallel 源码取证后**两处均错**：
> ① 崩溃点是 **`/dev/shm`（tmpfs，内存后端）**，不是磁盘；② Docker Desktop 数据**不在 C:**
> （`C:\Users\Administrator\AppData\Local\Docker\wsl` 是指向 `D:\WorkSpace\docker-wsl` 的 junction，
> 2026-09-10 建立）。下面为更正后版本，错误结论保留在「教训」一节以免重蹈。

## 现象

1. 2026-10-22 05:08，`/factor-research` 监控页进入「数据已陈旧（最近采集 05:08:41）」，
   实态「状态未知」（后端 `actual_state=unverified`，陈旧 >180s 的既定降级）。
2. 最后一个会话 `session-20261022-050154.log`（05:01:54 起，loop 2 / 第 2/5 步 coding）
   以如下堆栈收尾，日志文件最后写入时间 **05:08:03**：

   ```
   concat_feature.groupby("datetime").parallel_apply(...)
   /opt/rdagent/lib/python3.11/site-packages/pandarallel/core.py:263 in closure
       pickle.dump(chunk, file_descriptor)
   OSError: [Errno 28] No space left on device
   ```

3. 此后 distro 不可用：**读与 exec 全部 I/O error**（实测于 07:26，VM 重启前）——

   ```
   $ wsl -d rdagent -e /bin/true
   <3>WSL (23201 - Relay) ERROR: CreateProcessCommon:818: execvpe(/bin/true) failed: I/O error
   $ wsl -d rdagent -e bash -lc "df -h"
   bash: /etc/profile: Input/output error
   bash: line 1: df: command not found
   ```

4. 自愈启动器（计划任务，每 2 分钟）**05:09:55 起连续 69 次 exit=135（SIGBUS）**，零效果
   （`launcher-task.log`）。日志目录里 05:08:03 之后到 07:27:56 之间**没有任何新会话文件**——
   看护没能拉起下一个会话，说明整个 distro 已不可用，不是单纯某个进程死。
5. 07:26:31 WSL 工具虚拟机重启后自愈：07:27:56 代理守护（pid 13）调和 `desired_running=True`
   拉起看护，`session-20261022-072756` 开跑，候选 1249 → 1254 → 1262。中断
   **05:08:03 → 07:27:56 = 2h19m53s**，零数据丢失（候选/工作区在宿主 D: 桥上完好，生产栈未受影响）。

## 现象二（07:40 起死锁，同日 11:47 实测仍在进行）

07:26:31 VM 重启后的新会话 `session-20261022-072756` 跑到 07:39 时进入同一个并行 IC 计算，
**这次撞的是 VM 内存上限而不是 tmpfs 上限**：

- 07:39:15 前后 pandarallel 派生 9 个 worker（`ppid=18`，即会话进程）；
- **07:40:21 与 07:40:27 内核 OOM killer 连杀两个 rdagent 进程**
  （`dmesg`：`Killed process 10906 (rdagent) total-vm:12486532kB, anon-rss:5961176kB` /
  `10910 ... anon-rss:5790768kB`）；
- 会话日志最后写入 **07:39:36**，此后零字节；候选/任务数冻结在 1262 / 1952；
- 存活进程全部停摆：会话进程 `wchan=unix_stream_read_generic`（pandarallel 的
  `SyncManager` unix socket），9 个 worker 全部 `wchan=futex_do_wait`；
- **60 秒采样 CPU 时间零增量**（pid 18 `utime+stime=15431` 两次相同；worker 1746 同理），
  日志 size/mtime 不变 ⇒ **死锁，不是慢**（该会话历史最长合法时长 4.25h，故必须用 CPU 增量判别）；
- 内存仍处高压：`Mem 17983 总 / 413 空闲`，`Swap 3562 / 4096 已用`。

机制（证据支持、非推测）：OOM 杀掉部分 worker 后，master 等一个永不到来的结果、
其余 worker 等 futex——pandarallel 无超时，**永久死锁**；而看护只在会话进程退出后才重拉，
会话进程「活着但零进展」⇒ 看护永不介入。截至 11:47 已停摆 **4h07m**（当日累计损失 ≈ 6.5h）。

### 监控盲区（本次第二处）

页面在此期间一直显示「运行中 / 存活 / 已运行 603s · 预计剩余 905s」：

- `alive` = `_process_alive(pid) and _rdagent_session_alive()`，**只探进程存在**（`collect_factor_status.py:199/213/421`）；
- `step_elapsed_s` / `step_eta_s` 解析自日志里**最后一行** tqdm 进度条（同文件 :99/:102/:426），
  日志冻结后该读数即永久定格在旧值。

⇒ 死锁会话与健康会话在监控面上不可区分。需要「日志 N 分钟无新行 / CPU 零增量」的僵死判定，
与 exec 存活探针并列。

## 根因三（真正的源头，12:35 定位并已修复）：lambda 闭包捕获两个 DataFrame

`rdagent/scenarios/qlib/developer/factor_runner.py` 的并行 IC 计算：

```python
.concat_feature.groupby("datetime").parallel_apply(
    lambda x: self.calculate_information_coefficient(x, SOTA_feature.shape[1], new_feature.shape[1])
)
```

lambda 内**按名字引用** `SOTA_feature` / `new_feature`（函数局部变量 ⇒ 闭包自由变量），
而 pandarallel 用 `dill.dumps(user_defined_function)`（`core.py:265`）把整个闭包**按值**序列化：

- **每个 worker 各拿一份完整特征矩阵**；同一 lambda 还捕获绑定方法
  `self.calculate_information_coefficient` ⇒ 连带 pickle 整个 runner 实例。
  实测每 worker 匿名内存 **5.44 / 5.47 GB**（`dmesg` OOM 报告），默认 worker 数 =
  `NB_PHYSICAL_CORES` = **8** ⇒ 8 × 5.4GB ≫ VM 18GB（OOM 时 `active_anon + inactive_anon = 17.4GB`，
  而 `shmem` 仅 0.74GB）→ OOM 杀 worker → 死锁；
- **每个 chunk 文件同样含这份拷贝** ⇒ 05:08 那次 `/dev/shm` 才会在 8.8GB 上限处报 `Errno 28`。

⇒ **一个 bug、两种死法**：05:08 撞 tmpfs 上限（会话崩），07:40 / 12:19 撞 VM 内存上限（OOM 后死锁）。
「根因一 / 二」是表现，本节的闭包捕获是源头。

**已实施修复**（`tests/scripts/spike/patch-ic-closure.py`，幂等 + 备份 `.bak-ic-closure-patch`）：

1. 形状先算成 int（`_sota_cols, _new_cols = ...`），lambda 只闭包两个整数；
2. IC 实现提为模块级 `_ic_of_columns`，不再捕获 `self`（对外方法签名不变）；
3. `pandarallel.initialize(..., nb_workers=int(os.environ.get("RDAGENT_PANDARALLEL_WORKERS", "8")))`
   ——核数不是本 VM 的合适默认（核多而内存固定 18GB 且与生产栈共享）；
4. 增一行 `[incident-049] IC dedup: concat_feature=… GB` 尺寸日志，供同类问题取证。

配套 `launch-fin-factor.sh`：`MEMORY_FS_ROOT` 指向磁盘目录
`/root/rdagent-work/.pandarallel_mem`（把 tmpfs 占用从内存里拿掉）+ 导出 `RDAGENT_PANDARALLEL_WORKERS`。

## 根因一（会话崩溃，已证实）：`/dev/shm` tmpfs 上限

pandarallel 把每个 worker 的输入 chunk 落盘到 **`MEMORY_FS_ROOT`，默认 `/dev/shm`**：

```python
# /opt/rdagent/lib/python3.11/site-packages/pandarallel/core.py
33:  MEMORY_FS_ROOT = os.environ.get("MEMORY_FS_ROOT", "/dev/shm")
247: input_files = [NamedTemporaryFile(prefix=PREFIX_INPUT, suffix=SUFFIX,
                                       dir=MEMORY_FS_ROOT, delete=False) ...]
```

`/dev/shm` 是 **tmpfs（内存后端）**，容量 = VM 内存的 50% = **8.8 GiB**（`.wslconfig` `memory=18GB`）。
并行 IC 计算（`concat_feature.groupby("datetime").parallel_apply(...)`）把整个特征矩阵按 worker 切片
写入，总字节数超过 8.8 GiB 即 `Errno 28`。属**内存类资源上限**，与 incident 047 同族，**不是磁盘**。

取证：

- `MEMORY_FS_ROOT` **未设置**——`launch-fin-factor.sh`（仓库 `tests/scripts/spike/launch-fin-factor.sh`）
  只 export `DOCKER_HOST`，`.env` 只有 chat/embedding 凭据 → 走默认 `/dev/shm`；
- `df -h /dev/shm` → `8.8G`，与 18GB×50% 吻合；
- 现场 `/dev/shm` 仍有 pandarallel 泄漏文件（`delete=False` + 异常路径不清理）：
  `pandarallel_output_*.pickle`（07:39–07:42），实测占用 1.4G。

**反证「宿主 C: 卷耗尽」**（初稿的错误结论）：

1. 宿主 C: **现在仍有 7.0 GB 空闲**，而 `ext4.vhdx` 自崩溃以来只长了 5.06 GiB，另扣掉
   07:26 新 VM 新建的 4.1 GB `swap.vhdx` ⇒ 崩溃时 C: 约 **8–12 GB 空闲**，不是 0；
2. 宿主卷满的症状应是 **rootfs 报 ENOSPC**，而实测是 **`/dev/shm` 报 ENOSPC + rootfs 报 EIO**——
   两个症状都对不上「宿主盘满」；
3. 崩溃时 VM 并未换页：旧 VM 的 `%LOCALAPPDATA%\Temp\125CB2F5-…\swap.vhdx`（4.1GB）
   最后写入时间是 **2026-10-21 00:41**，30 小时无 swap 写入。

## 根因二（2h20m 不可用，机制未证实）

rootfs 进入「读 EIO」态并持续到 VM 重启。`journald` 非持久（无 `/var/log/journal`）+ VM 已重启
⇒ 内核日志不可得，**未能证实机制**。候选（未验证，勿当结论）：

- 宿主侧虚拟盘 I/O 未能完成 → guest 块设备队列挂起（ext4 挂载参数为
  `rw,relatime,discard,errors=remount-ro,data=ordered`）；
- VM 资源耗尽的连锁故障（本次无 swap 写入，证据偏弱）。

可确证的是**失败形态**：VM 仍报 `Running`、distro 却无法 exec 任何二进制——这是现有探针
（只看快照/心跳新鲜度）无法区分的新形态，也是中断被拉长到 2h20m 的直接原因。

## 独立风险（非本次主因，但需治理）：宿主 C: 容量

C: 300GB / 已用 293GB（96%），构成（实测）：

| 项 | 占用 |
|---|---|
| `C:\WSL\rdagent\ext4.vhdx`（**非稀疏**，虚拟 1TB，仍会增长） | 102.75 GiB |
| Users（不含 Docker，见下） | 86.0 GiB |
| Program Files / Windows / ProgramData / PF(x86) | 31.0 / 30.7 / 14.5 / 10.2 GiB |
| 杂项（Recovery、预装、Python314…） | ~2.5 GiB |

**Docker Desktop 数据不在 C:**：`C:\Users\Administrator\AppData\Local\Docker\wsl` 是
**junction → `D:\WorkSpace\docker-wsl`**（`dir /AL` 实证，2026-09-10 建立）；
`D:\WorkSpace\docker-wsl\disk\docker_data.vhdx`（102.23 GiB）由 VHDMP 记录为 07:26:32
挂到 VM `851282A3`。即 Docker 早已迁到 D:，**它不占 C: 空间**。

## 处置

### 已实施（2026-10-22 12:30–12:36）

1. **修根因三（闭包捕获）**：部署 `patch-ic-closure.py` 到 distro 并应用到
   `/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/developer/factor_runner.py`；
   自检四项全 OK + `py_compile` 通过。
2. **配套环境**：`launch-fin-factor.sh` 增 `MEMORY_FS_ROOT=/root/rdagent-work/.pandarallel_mem`
   （磁盘）与 `RDAGENT_PANDARALLEL_WORKERS=8`；`bash -n` 通过，旧脚本备份
   `/root/launch-fin-factor.sh.bak-20261022`。
   注意 pandarallel 在 **import 期**读 `MEMORY_FS_ROOT`（`core.py:33`），必须放在 `exec` 之前。
3. **解死锁**：`POST /api/factor-research/restart`（`restart_request_id=ca00d06ffdc1`）→
   代理 12:30:15 回报 `result=restarted`，死锁会话被杀，新会话 `session-20261022-123013` 起跑。
4. **生效验证**：新会话进程 env 实测含
   `MEMORY_FS_ROOT=/root/rdagent-work/.pandarallel_mem` 与 `RDAGENT_PANDARALLEL_WORKERS=8`；
   `/dev/shm` 占用由 1.3–1.4GB 降到 **1.6MB**（残留文件全部是 12:19–12:20 旧会话的）；
   监控 8 分钟：内存稳定 3.7–4.8GB used / 13.3GB available、swap 稳定 750MB、日志持续增长、
   **无新增 OOM**（`dmesg` OOM 计数仍为 2，均为 12:20 旧事件）。
5. **C: 容量治理**：用户已自行完成 `wsl --manage rdagent --move D:\WSL\rdagent`
   （`BasePath=D:\WSL\rdagent`），C: 空闲 **6.4GB → 118.9GB**。
   注：D: 上留有一份搬迁前的备份 `D:\WSL\backup\rdagent-20261022\ext4.vhdx`（102.7GB），
   确认新盘稳定后可删。

6. **IC dedup 实跑验收（12:46:40 通过）**：新会话在 loop 2 的 `running` 步真正走到
   `deduplicate_new_factors`，日志输出
   `[incident-049] IC dedup: concat_feature=(14323214, 7) 0.86GB, sota_cols=4, new_cols=3`；
   全程内存 6.6GB used / 11.4GB available、swap 749MB、**`dmesg` OOM 计数仍为 2（无新增）**，
   随后 `Factor data processing completed.` → `Experiment execution ...`，**顺利越过原先的死亡点**。
   机制量化验证（同构 lambda 直接量 `dill.dumps`）：旧写法 = **48.0 MB**（闭包
   `['DataFrame','R']`，体积等于矩阵本身），新写法 = **0.4 KB**（闭包 `['int','int']`）。

### 待办

1. **监控盲区（本次两处）——其一已交付**：
   - ✅ **僵死判定**已由 delta `add-factor-research-stall-detection` 落地并 archive（2026-10-22）：
     采集器上报 `current_session.log_age_s`（原始读数），端点派生顶层
     `progress.{log_age_seconds,stalled}`（阈值单源 `FACTOR_RESEARCH_STALL_AFTER_S=900`，取实测历史
     最大静默 602s 的 1.5 倍），页面在僵死时展示无进展横幅并**停止把定格的步骤读数当当前进度**。
     E2E 17/17、pytest 58、vitest 34 全绿；人工验证报告见
     `tests/validation/2026-10-22-add-factor-research-stall-detection-validation.md`。
   - ⏳ **exec 存活探针**未做：启动器加 `wsl -d rdagent -e /bin/true`（或写标记文件）判活，失败时
     页面如实显示「distro I/O 错误，需重启 VM」，并允许启动器尝试 `wsl --terminate rdagent`
     （本次真正生效的动作就是 VM 重启）。**动启动器权限边界，需单独 delta 讨论**。
2. **pandarallel 泄漏**：`delete=False` 的 chunk 文件在异常路径残留（`/dev/shm` 与新的
   `.pandarallel_mem` 都实测有残留），长跑蚕食空间；应纳入清理或改 `delete=True` 的读回方式。
3. **VM 内存配额**：18GB 由研发侧与生产栈共享，本次是研发侧单点打满；IC dedup 修好后
   仍需评估是否需要给 VM 更多内存（宿主 46.5GB，需先看宿主余量）。

## 教训与防护

- **「No space left on device」不一定是磁盘**：tmpfs（`/dev/shm`）报同一个 errno。看到它先问
  「这个 fd 写在哪个挂载点」——本次初稿正是跳过这一步，凭宿主占用率直接归因，结论全错。
- **pandarallel 的 chunk 走 `/dev/shm`，与 VM 内存预算强耦合**（8.8GB = 18GB 的 50%）：
  大 DataFrame 的 `parallel_apply` 会被 tmpfs 上限掐死，且报错文本会把人引向磁盘。
- **VM 活着 ≠ distro 可用**：探针必须探「能不能执行」，不能只探心跳/快照新鲜度。
- **junction 陷阱**：C: 上的 `...\Docker\wsl` 可能只是指向 D: 的 junction。按「C: 路径存在」
  统计占用会算错（本次初稿即错在此）；`dir /AL` + `fsutil reparsepoint query` 是唯一可信解。
- 与 incident 047 同族：共同教训是**研发侧常驻负载的资源边界（内存 / tmpfs / 宿主卷）
  必须在宿主侧显式治理**，且报错文本往往指向错误的那一层。

## 关联

- 同族：`047-wsl-memory-exhaustion-container-enomem-20261021.md`（VM 内存上限）、044/046
- 环境与排障手册：`tests/scripts/spike/README.md`；启动器：`tests/scripts/spike/launch-fin-factor.sh`
- 契约：`openspec/specs/factor-research-monitor/spec.md`（陈旧 180s / 心跳 300s / `honor` 三态）
