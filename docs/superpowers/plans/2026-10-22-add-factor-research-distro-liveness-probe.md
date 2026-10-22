# distro 探活与不可用自愈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「distro 还能不能执行」成为可观测事实，并在证据充分时自愈，消除 incident 049 那类「distro 不可用 2h20m 而页面只说采集器可能已停止」的盲区。

**Architecture:** 宿主侧自愈启动器（Windows 计划任务）每节拍先探活 `wsl -d <distro> -e /bin/true`，把结果原子写入宿主侧 `distro_probe.json`；自愈门控是**三条件合取**（连续失败 ≥15 ∧ 状态快照停摆 ≥900s ∧ 距上次自愈 ≥1800s）。后端 `GET /api/factor-research/status` 派生只读 `distro` 块；前端在「快照陈旧 ∧ 探活新鲜 ∧ ok=false」时展示独立提示，并修正陈旧横幅的失实表述。

**Tech Stack:** C#（.NET Framework 4.0 `csc.exe`，C# 5 语法上限）/ Python 3.12 + FastAPI + pytest / React 18 + TypeScript + vitest / Playwright。

## Global Constraints

- **门控是合取，且判据有实测依据**：单次探活失败 MUST NOT 判死——历史 839 次节拍中 **611 次（72.8%）非零退出**（WSL relay 超时）而管线在跑。阈值：`HealAfterFailures=15`、`HealSnapshotStaleS=900`、`HealCooldownS=1800`。
- **探活只读**：MUST NOT 改控制文件、MUST NOT 删研发产物、MUST NOT 启停看护；探活/写文件失败 MUST NOT 阻断既有 ensure 流程。
- **后端只派生不改判定**：`distro.ok=false` MUST NOT 影响 `actual_state` / `control.honor`；端点零处置动作。
- **阈值单源**：后端 `FACTOR_RESEARCH_DISTRO_PROBE_FRESH_AFTER_S = 300`；启动器三个常量各一处。
- **前端判定组合**：`stale===true ∧ available===true ∧ fresh===true ∧ ok===false` 才下结论；否则保持既有表述、不展示探活横幅。
- **C# 5 语法**：本机 csc 为 .NET Framework 4.0 编译器——MUST NOT 用数字分隔符、`using var`、模式匹配等下位语法。
- **`.ps1` 含中文必须带 UTF-8 BOM**（PS 5.1 否则按 GBK 读 → 解析失败）。
- **部署纪律**：`docker compose up` 默认合并 `docker-compose.override.yml`（= dev 模式）；生产部署必须 `docker compose -f docker-compose.yml up -d --build <svc>`。
- testid：`factor-research-distro-probe-banner`；E2E 必须用 `playwright.isolated.config.ts`。

---

### Task 1: 启动器探活 + 门控自愈（C#）

**Files:** Modify `scripts/FinanceAgentLauncher.cs`、`scripts/register-factor-research-task.ps1`

**Interfaces:** 产出宿主侧 `distro_probe.json`：`probed_at`/`probed_at_epoch`/`distro`/`ok`/`exit_code`/`detail`/`consecutive_failures`/`snapshot_stale`/`terminated_at`/`terminated_at_epoch`/`terminate_count`；纯函数 `internal static bool Program.ShouldHeal(bool ok, int failures, bool snapshotStale, long lastTerminatedEpoch, long nowEpoch)`。

- [x] **Step 1: 写门控真值表程序**（`tmp/gate-test.cs`，与启动器同程序集编译，`/main:GateTest`）——7 条：成功永不自愈 / 失败 14 / 快照新鲜 / 三条件齐备 / 冷却 300s / 冷却 1800s / 失败 40 但快照新鲜。
- [x] **Step 2: 运行确认失败**（未实现时 `ShouldHeal` 不存在 → 编译失败）。实测：`CS1002`/`CS1010`（数字分隔符与文件损坏），修后编译通过。
- [x] **Step 3: 实现**：`ProbeAndMaybeHeal`（探活 ≤20s → 读旧状态 → `failures = ok ? 0 : prev+1` → `ShouldHeal` → 必要时 `wsl --terminate` 并记 `terminated_at`/`terminate_count` → 原子写探活文件）；`SnapshotStale` 读 `status.json` 的 `collected_at_epoch`；`register-factor-research-task.ps1` 传第 4/5 个参数（探活路径、快照路径）+ `csc /codepage:65001`。
- [x] **Step 4: 运行确认通过**：`tmp\gate.exe` → **ALL PASS (7/7)**。
- [x] **Step 5: 部署**：补 `.ps1` 的 UTF-8 BOM → `powershell -ExecutionPolicy Bypass -File scripts/register-factor-research-task.ps1` → 任务参数含探活/快照路径；≤2 分钟后生产探活文件 `ok=true`、`LastTaskResult=0`。

### Task 2: 后端 `distro` 派生块

**Files:** Modify `src/finance_agent/api.py`（`FACTOR_RESEARCH_DISTRO_PROBE_FRESH_AFTER_S` + `_factor_research_distro_payload()` + `_factor_research_status_payload()` 追加 `"distro"`）、`tests/test_api_factor_research.py`

**Interfaces:** 消费 Task 1 的探活文件；产出响应顶层 `distro` 块（透传 + `available`/`age_seconds`/`fresh`；缺失/损坏 → `{available:false, reason}`）。

- [x] **Step 1: 写失败测试**：`TestDistroProbe` 4 例（透传+派生+零状态影响 / missing / corrupt / 陈旧不 fresh），并给既有 4 处「快照不可用」`==` 精确断言补 `distro` 字段（保持全量相等）。
- [x] **Step 2: 运行确认失败**：`4 failed, 58 deselected`（均 `KeyError: 'distro'`）。
- [x] **Step 3: 实现**（含 autouse fixture 把 `FACTOR_RESEARCH_DISTRO_PROBE_PATH` 指向 tmp——否则启动器落地后生产探活文件会污染既有精确断言）。
- [x] **Step 4: 运行确认通过**：`4 passed`；全文件 `62 passed`；`ruff check` 通过。

### Task 3: 监控页探活披露

**Files:** Modify `frontend/src/types.ts`、`frontend/src/pages/factors/FactorResearchPage.tsx`、`frontend/src/test/factorResearchPage.test.tsx`

**Interfaces:** 消费 Task 2 的 `distro` 块；产出 testid `factor-research-distro-probe-banner` 与陈旧横幅的 distro 分支文案。

- [x] **Step 1: 写失败测试**：4 例（distroDown 提示可见+陈旧横幅不误指采集器+零命令面 / ok=true 保持既有 / available=false 保持既有 / stale=false 不展示）。
- [x] **Step 2: 运行确认失败**：`1 failed | 3 passed`（否定断言天然通过，属正常形态）。
- [x] **Step 3: 实现** `distroDown` 派生 + 两处文案分支。
- [x] **Step 4: 运行确认通过**：`38 passed`；`tsc -b` exit 0。

### Task 4: E2E 门禁

**Files:** Modify `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`、两套 config（注入 `FACTOR_RESEARCH_DISTRO_PROBE_PATH`）

- [x] **Step 1-3: 加隔离守卫 + 探活 fixture writer + #18/#19 两例**（先确认 E2E 打的是隔离栈）。
- [x] **Step 4: 运行确认通过**：`npx playwright test factor-research-monitor.spec.ts --config playwright.isolated.config.ts` → **19 passed (1.3m)**。

### Task 5: 生产部署与验收

- [x] 红线门禁（`/api/sessions` 无 running）→ `docker compose -f docker-compose.yml up -d --build backend frontend`（**必须带 `-f`**：默认会合并 override 切成 dev 模式）。
- [x] 验收：端点返回 `distro.ok=true / fresh=true`；前端 nginx 产物含新 testid 与文案；探活文件每 2 分钟刷新。
- [x] 人工验证报告落 `tests/validation/2026-10-22-add-factor-research-distro-liveness-probe-validation.md`；archive + sync 主规范库（`validate --specs --strict` → 82 passed）。

## 未验证项（如实记账）

沙箱内启动器进程派生的 `wsl.exe` 一律 `E_ACCESSDENIED` 或挂起，故**自愈的「执行」分支未对真实失败 distro 演练**；已用真值表（门控）+ 沙箱实测（探活/计数/文件）+ 生产实测（成功路径）替代覆盖。详见验证报告「异常记录 1」。
