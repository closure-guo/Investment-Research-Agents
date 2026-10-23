# 人工验证报告: add-factor-research-stall-detection

**日期**: 2026-10-22
**验证人**: 控制者（agent）+ 待 owner 抽查
**关联 delta**: `openspec/changes/add-factor-research-stall-detection/`
**E2E 门禁**: `tests/e2e/playwright/`（`--config playwright.isolated.config.ts`，隔离栈 8001/5174）——**17 passed (1.4m)**

## 背景

incident 049：因子研发会话因 pandarallel 并行 IC 计算撑爆资源而死锁，07:40→11:47 停摆 4h07m，
而监控页全程显示「运行中 / 存活 / 已运行 603s · 预计剩余 905s」（`alive` 只探进程存在，
进度读日志末行，日志冻结后读数定格）。本 delta 让「采集正常但会话无进展」可观测、可判定、可披露。

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 采集器上报 `current_session.log_age_s` 原始读数 | 否（单元 + 真机） | 数值 = 日志 mtime 距采集时刻秒数 | 单测 42（±1）✓；**真机部署后 `log_age_s=7`，与 mtime 实际差 7 完全一致** | ✅ |
| 端点派生 `progress.stalled`（超阈值 3600s） | 是（#16） | `{log_age_seconds:3600, stalled:true}` | pytest 断言通过 | ✅ |
| 阈值内不告警（602s = 实测历史静默上界） | 否（单元） | `stalled=false` | pytest 通过 | ✅ |
| 看护未运行 / 会话非存活 → 不判僵死 | 否（单元） | `stalled=false` | pytest 通过（两个用例） | ✅ |
| 快照不可用/损坏 → `progress={null,false}` | 是（#3/#4 间接） | 不误报、不 500 | pytest 通过 | ✅ |
| 旧采集器未上报 → `{null,false}`（不判定） | 否（单元 + 前端） | 前端不提示、也不声称「进度正常」 | pytest + vitest 通过 | ✅ |
| 监控页僵死横幅含无进展时长 | 是（#16） | 「当前会话无进展 1小时0分」 | E2E 断言 `toContainText('无进展 1小时0分')` 通过 | ✅ |
| 僵死时不把定格读数当当前进度 | 是（#16） | `step-timing` 消失、`progress-stale` 出现 | E2E 断言 `step-timing` count=0 通过 | ✅ |
| 未僵死不告警且进度照常 | 是（#17） | 无横幅、`step-timing` 仍显示「本步已用 9分55秒」 | E2E 通过 | ✅ |
| 横幅零命令面 | 是（#16） | 横幅内无 `button`/`a` | E2E 断言 `locator('button, a')` count=0 通过 | ✅ |
| 僵死与陈旧提示互斥不遮蔽 | 是（#16） | 采集正常时 `stale-banner` 不出现 | E2E 断言 count=0 通过 | ✅ |
| 阈值单源（全仓仅一处 900） | 否（代码审查） | 采集器/前端无第二份阈值 | `grep STALL_AFTER_S` 仅 `src/finance_agent/api.py:4152` 定义，其余为文档/测试引用 | ✅ |
| 端点对 `stalled=true` 零副作用 | 否（代码审查 + 既有回归） | 不写控制文件、不碰研发进程 | `_factor_research_progress_block` 为纯函数；端点其余逻辑未改 | ✅ |
| 既有能力零回归 | 是（#1-#15） | 15 个既有用例全绿 | E2E 17/17；pytest 58；vitest 34 | ✅ |

**证据命令（全部由控制者独立复跑，非 agent 自述）**

```
uv run pytest tests/test_api_factor_research.py -q                      → 58 passed
（采集器，Windows 需分离子进程跑，见异常 1）
  uv run python <SIGINT_IGN 包装> → pytest tests/scripts/spike/test_collect_factor_status.py -q
                                                                        → 30 passed, CHILD_EXIT=0
cd frontend; npx vitest run src/test/factorResearchPage.test.tsx        → 34 passed
cd tests/e2e/playwright; npx playwright test factor-research-monitor.spec.ts \
  --config playwright.isolated.config.ts                                → 17 passed (1.4m)
```

**主观项（机器看不了，待 owner 抽查）**

- 横幅文案可读性：「当前会话无进展 1小时0分——进程仍存活但日志已停止更新，看护不会自动重拉；
  需经「重启管线」由人发起」是否足够指明下一步动作。
- 僵死时把「本步已用/预计剩余」替换为「进度读数来自 N 前的日志」是否比继续显示更不易误读。

## 异常记录

1. **（既有缺陷，与本变更无关）** Windows 下 `tests/scripts/spike/test_collect_factor_status.py` 无法直接
   在 pwsh 里跑：既有用例把 `pid=os.getpid()` 写进 pidfile，`_process_alive()` 走 `os.kill(pid, 0)`，
   而 Windows 上 `CTRL_C_EVENT == 0` → CPython 调 `GenerateConsoleCtrlEvent`，把 CTRL_C 打到共享
   控制台，**打断 pytest 并杀掉外层 shell**（表现为 `[exit code: 1]` 且无 echo；工作区里的
   `pytest_spike.txt` / `spike_out.txt` 即其产物）。已用**未改动的既有用例** `TestAgentMeta`
   单独复现（`2 passed` 但 exit 1）证明与本次改动无关。规避：分离子进程 + `signal.SIGINT` 忽略。
   **未在本次改动中修既有测试基础设施**；建议另开 issue。
2. **未提交（deliberate）**：工作区同时存在 3+ 个并发会话的未提交改动（`git status` 47 个 M 文件），
   且 `src/finance_agent/api.py` 内含**他人 WIP**（`add-analysis-concurrency-setting` 的
   `_analyze_concurrency` / `_reset_analyze_queue` 等 hunk）。按文件 `git add` 会把他人 WIP 一并提交，
   故本次**不做 commit**，交付物留在工作区，待并发工作流收口后由 owner 统一提交。
   本次涉及文件清单见下。
3. **生产后端尚未生效**：生产容器（8000）跑的是旧 `api.py`（无 `progress` 块）。前端与采集器已
   前向兼容（旧端点无 `progress` → `stalled=false` → 不显示横幅，无异常）。要让监控页在生产生效，
   需重启后端容器——属独立动作，按 AGENTS.md 红线须先确认 `GET /api/sessions` 无 running 分析会话
   （当前 0），**待 owner 决定**。

## 本次涉及文件

- `openspec/changes/add-factor-research-stall-detection/`（proposal / design / specs×2 / tasks）
- `docs/superpowers/plans/2026-10-22-add-factor-research-stall-detection.md`
- `tests/scripts/spike/collect_factor_status.py`（`log_age_s`）
- `tests/scripts/spike/test_collect_factor_status.py`（+3 用例）
- `src/finance_agent/api.py`（`FACTOR_RESEARCH_STALL_AFTER_S` + `_factor_research_progress_block` + 三处返回）
- `tests/test_api_factor_research.py`（+6 用例）
- `frontend/src/types.ts`、`frontend/src/pages/factors/FactorResearchPage.tsx`、
  `frontend/src/test/factorResearchPage.test.tsx`
- `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`（+2 用例）
- distro：`/root/collect_factor_status.py`（已部署，旧版备份 `.bak-<时间戳>`）

## 结论

- [x] 全部通过，可 archive
- [ ] 存在失败项，需修复后重新验证

（唯一非阻塞项为上述异常 2/3：提交与生产部署待 owner 决定。）
