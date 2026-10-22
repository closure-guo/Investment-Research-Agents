# 人工验证报告: add-factor-research-distro-liveness-probe

**日期**: 2026-10-22
**验证人**: 控制者（agent）+ 待 owner 抽查
**关联 delta**: `openspec/changes/add-factor-research-distro-liveness-probe/`
**E2E 门禁**: `tests/e2e/playwright/`（`--config playwright.isolated.config.ts`，隔离栈 8001/5174）——**19 passed (1.3m)**

## 背景

incident 049：distro 不可用 2h20m 期间，监控页只能说「数据已陈旧——采集器可能已停止」（**失实**：
采集器没停，是 WSL 子系统不可用），且自愈启动器的 69 次失败除自身日志外零留痕。本 delta 让
「distro 还能不能执行」成为可观测事实，并在证据充分时自愈。

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 探活门控：三条件合取（连续失败 ≥15 ∧ 快照停摆 ≥900s ∧ 冷却 ≥1800s） | 否（真值表程序） | 7 条真值表全对 | `gate.exe` → **ALL PASS (7/7)**，含「探活成功永不自愈」「快照新鲜不自愈」「失败 40 次但快照新鲜仍不自愈」 | ✅ |
| 探活成功照常拉起（真实 rdagent） | 否（生产实测） | `ok=true`、`exit_code=0`、`consecutive_failures=0`，既有 ensure 流程不变 | 生产探活文件：`ok=true / exit_code=0 / consecutive_failures=0 / snapshot_stale=false`，`LastTaskResult=0` | ✅ |
| 探活失败如实记录（`ok/exit_code/detail`/计数自增） | 否（沙箱实测） | 失败计数跨节拍累加、成功归零、detail 捕获 WSL 错误 | 沙箱四例实测：`consecutive_failures` 1→15→16→41；`detail` 捕获 `Wsl/E_ACCESSDENIED` 原文；文件为合法 JSON | ✅ |
| 探活文件原子写、写失败不阻断 | 否（代码审查） | 临时文件 + 替换；异常仅记日志 | 代码审查 + 沙箱实测文件始终可解析 | ✅ |
| 端点 `distro` 块透传 + `age_seconds`/`fresh` | 是（#18/#19 间接） | 合法 → available/fresh；缺失/损坏 → 二分 reason | pytest `TestDistroProbe` 4 例通过；生产端点实测 `age_seconds=33 / fresh=true` | ✅ |
| `distro.ok=false` 不改 `actual_state`/`control.honor`、端点零副作用 | 否（单元） | 与无探活文件时同值 | pytest 同用例内 baseline 对比断言通过 | ✅ |
| 探活失败 + 快照陈旧 → 独立提示，且陈旧横幅不再误指采集器 | 是（#18） | 含「连续 8 次」「distro」「采集器并未停止」；陈旧横幅不含「采集器可能已停止」；横幅内无 button/a | E2E #18 通过 | ✅ |
| 探活正常 → 保持既有陈旧表述、无探活提示 | 是（#19） | 陈旧横幅含「采集器可能已停止」；无探活横幅 | E2E #19 通过 | ✅ |
| 探活不可用/不新鲜/快照新鲜 → 不冒充结论 | 是（#19 部分 + 单元） | 不展示探活横幅 | E2E #19（ok=true）+ vitest「available=false」「stale=false」两例通过 | ✅ |
| E2E 隔离：探活 fixture 不读生产文件 | 是（config 注入） | 两套 config 均注入 `FACTOR_RESEARCH_DISTRO_PROBE_PATH` → `data/test-e2e-*` | spec 内含路径隔离守卫；两 config 已注入；E2E 19/19 | ✅ |
| 既有能力零回归 | 是（#1–#17） | 17 个既有用例全绿 | E2E 19/19；pytest 62；vitest 38 | ✅ |

**证据命令（全部由控制者独立复跑）**

```
csc /codepage:65001 /target:exe /main:GateTest /out:tmp\gate.exe scripts\FinanceAgentLauncher.cs tmp\gate-test.cs
  && tmp\gate.exe                                        → ALL PASS (7/7)
uv run pytest tests/test_api_factor_research.py -q        → 62 passed
cd frontend; npx vitest run src/test/factorResearchPage.test.tsx → 38 passed
cd frontend; npx tsc -b                                   → exit 0
uv run ruff check src/finance_agent/api.py tests/test_api_factor_research.py → All checks passed!
cd tests/e2e/playwright; npx playwright test factor-research-monitor.spec.ts \
  --config playwright.isolated.config.ts                  → 19 passed (1.3m)
```

**生产生效证据（2026-10-22 15:00）**

- 探活文件 `data/factor_research/distro_probe.json`：`ok=true / exit_code=0 / consecutive_failures=0`
  （由计划任务每 2 分钟刷新；任务参数已含探活与快照路径）
- `GET /api/factor-research/status` → `distro={"available":true,"fresh":true,"ok":true,...,"age_seconds":33}`
- 前端 nginx 产物 `index-BmPFOvUg.js` 同时含 `factor-research-distro-probe-banner`、「采集器并未停止」
  与上一 delta 的 `factor-research-stall-banner`

**主观项（机器看不了，待 owner 抽查）**

- 探活提示文案是否足够指明「采集器没停 / 是 distro 不可用 / 恢复动作是什么」；
- 「连续 N 次探活失败」这一表述是否会让读者误以为已经判定 distro 死亡（文案刻意不下死结论）。

## 异常记录

1. **（环境限制，未验证项）自愈的「执行」分支未对真实失败 distro 演练**：DSH 沙箱内启动器进程
   派生的 `wsl.exe` 一律返回 `E_ACCESSDENIED` 或挂起（我直接调用 `wsl` 却正常），因此
   `wsl --terminate` 的**真实执行**未在本次验证中触发过。已验证的替代证据：门控逻辑真值表 7/7、
   探活/计数/文件写入（沙箱四例实测）、以及真实成功路径（生产探活文件）。
   **生产中的自愈只会在「探活连续失败 ≥15 次 ∧ 快照同时停摆 ≥900 秒」时触发**，健康态不可能命中。
2. **（本 delta 引入并已修复）`register-factor-research-task.ps1` 缺 UTF-8 BOM**：Windows PowerShell 5.1
   按 GBK 读该文件的中文 → 解析失败（`The string is missing the terminator`），首次重注册失败。
   已补 BOM（`EF BB BF`）并重注册成功。**教训**：本仓 `.ps1` 若含中文，必须带 BOM（`edit` 工具会写成
   无 BOM，改动后需补）。
3. **（部署误操作并已恢复）`docker compose up -d --build` 默认合并 `docker-compose.override.yml`**，
   把前后端从**生产模式**（nginx 静态产物 + 无 `src` 挂载）切成了 **dev 模式**（vite dev + 源码挂载）。
   已用 `docker compose -f docker-compose.yml up -d --build backend frontend` 恢复生产模式，并核验
   `Config.Cmd`（`nginx -g daemon off;` / 无 `--reload`）与静态产物含新 testid。
4. **未提交**：`tests/e2e/playwright/playwright.config.ts` 与 `playwright.isolated.config.ts` 的
   `env` 区域内同时存在**他人未提交改动**（`add-analysis-concurrency-setting` 的
   `ANALYSIS_CONCURRENCY_PATH`，该 delta 已 archive）与本次的 `FACTOR_RESEARCH_DISTRO_PROBE_PATH`，
   二者在同一 hunk 内，无法按 hunk 切分——提交时如实记账（见提交说明）。

## 结论

- [x] 全部通过，可 archive
- [ ] 存在失败项，需修复后重新验证

（唯一未验证项为异常 1 所述的「自愈执行分支」，其门控逻辑已由真值表确定性覆盖；如实记账。）
