## Context

incident 049 的失效形态是「VM 仍报 Running、distro 却无法 exec 任何二进制」（读/exec 全 EIO），
持续 2h20m。既有监控面只有两个维度：快照新鲜度（`stale`）与会话进度新鲜度（`progress.stalled`）。
前者在 distro 不可用时表现为「数据已陈旧——采集器可能已停止」（**失实**：采集器没停），后者需要
采集器还活着才能上报。自愈启动器的 69 次尝试全部 SIGBUS，且除自己的日志外无任何留痕。

自愈启动器是宿主侧 Windows 计划任务（`scripts/FinanceAgentLauncher.cs`，由
`register-factor-research-task.ps1` 编译注册），每 2 分钟调 `wsl -d rdagent -u root -e bash
/root/ensure-factor-research.sh`。它**天然能看见 distro 的不可执行性**（调用失败），只是没把这个
信息写出来、也没有任何恢复动作。

关键约束：启动器已有的失败信号**噪声极大**——历史 839 次节拍中 611 次（72.8%）非零退出
（WSL relay 超时 `0x8007274c`），而同期管线在正常跑。任何「失败即动作」的设计都会反复杀掉健康会话。

## Goals / Non-Goals

**Goals:**

- 让「distro 还能不能执行」成为可观测事实（宿主侧留痕 + 端点派生 + 页面如实披露）。
- 在**证据充分**时自愈（终止不可用 distro，使下一节拍重新拉起），把 2h20m 级停摆压到 ~30 分钟。
- 修正陈旧横幅的失实表述（不再把 distro 不可用说成「采集器可能已停止」）。

**Non-Goals:**

- 不自动启停**看护**（意图唯一写入面不变，仍由代理按期望态调和）。
- 不自动重启 Docker Desktop / WSL 全局（会连带打死生产栈；`wsl --shutdown` 仍是人工禁区）。
- 不引入新的控制入口（页面零命令面不变）。

## Decisions

**D1：探活在宿主侧做，结果写宿主侧文件。** 探活 `wsl -d <distro> -e /bin/true` 由启动器（Windows
进程）执行并写 `data/factor_research/distro_probe.json`。
*备选*：让 distro 侧代理自己上报「我还活着」→ 否：distro 死时它无法上报，这正是要观测的形态。

**D2：探活失败本身不构成「distro 已死」，必须叠加「快照同时停摆」。** 自愈门控为三条件合取：
`consecutive_failures ≥ 15`（≈30 分钟）∧ 快照 `collected_at_epoch` 距当前 ≥900 秒 ∧ 距上次自愈
≥1800 秒。
*理由*：611/839（72.8%）的节拍失败发生在管线健康时（WSL relay 超时），单条件会误杀；
而「快照同时停摆 ≥900 秒」在健康态不可能成立（采集周期 ≤60 秒），二者合取才构成不可用证据。
*备选*：探活失败即 terminate → 否（会按 72.8% 的比例误杀在跑的会话）。
*备选*：完全自动化为「不做自愈、只披露」→ 否（incident 049 的代价就是等人发现，30 分钟量级的
自动恢复是本次的目标收益）。

**D3：探活文件原子写，且写失败不阻断。** 复用既有「临时文件 + 替换」纪律；写失败只记日志，
不影响 ensure 流程（fail-safe）。

**D4：后端只派生不改判定。** `distro` 块独立于 `actual_state` / `control.honor`——后者语义是
「看护在不在跑 / 控制通道能不能受理」，与 distro 可执行性正交；混在一起会污染既有开关位置逻辑。

**D5：前端只在「探活新鲜 + ok=false + 快照陈旧」时下结论。** 探活不可用/不新鲜时保持既有表述，
避免把「没探活」当成「distro 坏了」；快照新鲜时不展示探活提示（D2 的同一逻辑在前端的体现）。

## Risks / Trade-offs

- [自愈误杀健康会话] → D2 的三条件合取 + 1800 秒限流；且终止 distro 不删研发产物（workspace /
  候选 / 会话日志保持），代理在 ≤3 分钟内重拉会话，代价有界。
- [探活本身抖动（relay 超时）导致 `consecutive_failures` 增长] → 只影响「是否自愈」的计数，
  不影响披露文案的准确性（文案只说「探活失败 N 次」，不下「已死」结论）。
- [计划任务参数变更后旧任务仍在跑] → `register-factor-research-task.ps1` 幂等重注册；先手工跑新
  exe 验证两条路径，再注册（保留旧 exe 备份）。
- [宿主机上无法安全演练真实失败] → 用「不存在的 distro 名 + 陈旧快照 fixture」演练失败与自愈
  分支：`wsl --terminate <不存在的名字>` 无害失败，不触碰运行中的 rdagent。

## Migration Plan

1. 后端与前端先合（`distro.available=false` 时前端保持既有行为，旧启动器无探活文件 → 天然兼容）。
2. 重编译并重注册计划任务（`register-factor-research-task.ps1`，幂等），确认探活文件在 ≤2 分钟内
   出现且 `ok=true`、`consecutive_failures=0`。
3. 回滚：恢复旧 exe 并重注册（探活文件成为孤儿，端点报 `distro.available=true` 但 `fresh=false`
   → 前端不展示提示，无副作用）。

## Open Questions

- `consecutive_failures` 的阈值（15 ≈ 30 分钟）是否应按事件史再调？本 delta 取保守值，理由是
  incident 049 的不可用持续 2h20m，30 分钟量级的恢复已把损失压掉 ~78%。
