## Why

2026-10-22 incident 049：rdagent distro 因资源耗尽进入「读/exec 全 I/O 错误」的不可用态，
停摆 **2h20m**。期间监控页只能显示「数据已陈旧——**采集器可能已停止**」，而实际是**采集器没停、
整个 WSL 子系统不可用**：既有监控面没有任何「distro 还能不能执行」的判据，自愈启动器 69 次
尝试全部以 SIGBUS 收场，也没有任何地方记录这件事。恢复动作（重启 WSL 虚拟机）只能靠人肉发现。

## What Changes

- **宿主侧探活**：自愈启动器每次节拍先跑 `wsl -d <distro> -e /bin/true`（≤20s 超时），把结果原子写入
  宿主侧 `data/factor_research/distro_probe.json`（`ok` / `exit_code` / `detail` /
  `consecutive_failures` / `terminated_at` / `terminate_count`）。
- **不可用自愈（三条件同时成立才动作）**：探活连续失败 ≥15 次（≈30 分钟）**且**状态快照同时停摆
  ≥900 秒**且**距上次自愈 ≥1800 秒 → 执行一次 `wsl --terminate <distro>`（下一次节拍自然重新拉起）。
  **判据依据（本 delta 的关键设计输入）**：历史 839 次节拍中 **611 次（72.8%）以非零退出**
  （WSL relay 超时），而其中管线实际在跑 ⇒ 单次探活失败 MUST NOT 被当作「distro 已死」，
  必须叠加「快照同时停摆」这一互斥条件（健康时快照必然新鲜），否则会反复杀掉在跑的研发会话。
- **只读派生 + 如实披露**：端点增 `distro` 块（透传 + `age_seconds` / `fresh`）；监控页在探活新鲜且
  `ok=false` 时展示独立提示，并**修正失实表述**——快照陈旧且探活失败时不再声称「采集器可能已停止」，
  改为指向 distro 不可用与人工恢复动作。
- 零命令面不变：页面不新增可点击处置入口；探活只读（不改控制文件、不动研发产物）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `factor-research-monitor`: 新增「研究侧 distro 探活与不可用自愈」与「distro 探活只读派生」两项需求
  （既有「研究侧自愈启动」的五条约束不变：探活不碰控制文件、不启停看护、不删研发产物）。
- `frontend`: 因子研发监控页新增 distro 探活披露，并修正陈旧横幅的失实表述。

## Impact

- 宿主 `scripts/FinanceAgentLauncher.cs`（探活 + 探活文件原子写 + 门控自愈）与
  `scripts/register-factor-research-task.ps1`（新增探活/快照文件路径参数；需重新注册计划任务）
- 后端 `src/finance_agent/api.py`（`distro` 派生块 + 探活路径环境变量）
- 前端 `frontend/src/pages/factors/FactorResearchPage.tsx`、`types.ts`
- 测试：`tests/test_api_factor_research.py`、`frontend/src/test/factorResearchPage.test.tsx`、
  `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`；启动器以「不存在的 distro 名 +
  陈旧快照 fixture」在宿主上安全演练失败/自愈两条路径（不触碰运行中的 rdagent）
