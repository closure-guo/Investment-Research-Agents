# 人工验证报告: add-ops-missed-today-badge

**日期**: 2026-10-24
**验证人**: AI（自动化验证 + 浏览器人工抽查）
**关联 delta**: `openspec/changes/add-ops-missed-today-badge/`（issue #453）
**E2E 门禁**: 已执行 → **3 passed**（`npx playwright test -c playwright.isolated.config.ts tests/ops-missed-badge.spec.ts`，8001/5174 + 独立测试库）

## 验证结果

| Scenario | 自动覆盖 | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| missed_today=true → 漏跑告警 | `dailyOpsPage.test.tsx` 六用例 + E2E | 「今日漏跑」徽章（degraded 琥珀），摘要明示漏跑 + 排程时刻 + 上次运行 | 单测 6/6；E2E 徽章 data-status=degraded、文案逐字 | ✅ |
| 当天已失败不误报为漏跑 | 同上 | failed 红徽章 + 错误逐字，全页无「今日漏跑」 | E2E：failed 徽章 + 错误原文，同屏漏跑任务对照可见 | ✅ |
| 未到排程时刻不告警 | 同上 | 「尚未到排程时刻」文案，无告警 | E2E 冻结周四 15:00：无漏跑徽章 | ✅ |
| 周末 mon-fri 不告警 | 同上 | 无漏跑；摘要「未到本期排程日」（修正原「今日未跑」谎称） | E2E 冻结周六 17:00 + 单测 | ✅ |
| 月批非排程日不告警 | 单测 | 「未到本期排程日」 | 单测 + 截图（每月 1 日任务灰徽章） | ✅ |
| 旧载荷字段缺失回退 | 单测 | 维持「未跑」retired，不臆造告警 | 单测（无 missed_today 字段 → retired） | ✅ |
| seed ops 造数块 | `test_ops_api.py::TestSeedOpsClockAndRuns` | now 冻结 / runs 造行回拨 / runs_reset 清表 | 3/3 passed；时钟逐用例还原 | ✅ |

## 验证命令与证据

```
cd frontend && npx vitest run src/test/dailyOpsPage.test.tsx → 22 passed（含 6 个新用例）
cd frontend && npm test → 947 passed；npm run build → ✓ built in 9.80s
uv run pytest tests/test_ops_api.py -q → 80 passed
uv run ruff check + ruff format --check（改动文件）→ All checks passed
npx playwright test -c playwright.isolated.config.ts tests/ops-missed-badge.spec.ts → 3 passed
```

浏览器人工抽查（隔离栈 8001/5174，seed ops 冻结时钟，截图 `tmp/spot-ops-20261024/`，未入库）：

- `ops-1-all-missed.png`：周四 16:10 冻结——日批时间线全部任务琥珀「今日漏跑」徽章，摘要「今日漏跑（排程 HH:MM 已过，当天无任何运行行）」
- `ops-2-failed-vs-missed.png`：同屏对照——「15:40 选股」红色实心「失败」徽章 + `RuntimeError: LLM 网关整体不可用` 逐字；「15:45 因子衰减监控」琥珀描边「今日漏跑」；月批任务（每月 1 日）灰「未跑」+「未到本期排程日」。三种状态视觉可区分，漏跑（琥珀）与失败（红）一眼可辨
- `ops-3-weekend-no-alarm.png`：周六 17:00——全页无漏跑告警，mon-fri 任务如实「未到本期排程日」

## 异常记录

- E2E 初版两处时钟口径踩坑（均已修正并留注释）：① describeJob 的「今日有运行行」判定用浏览器时钟，后端冻结时钟只作用于 `missed_today` 字段——失败行日期须与浏览器「今天」一致；② 本机 Playwright 1.62 无 `page.clock` API → 用 `addInitScript` 覆盖 `Date`（只 fake 「现在」，不劫持定时器）。
- **CI 抓出断言语义错误（重要）**：初版周六用例断言全页 `今日漏跑` 数为 0——但注册表中 `factor_harvest_daily` 是 `day_of_week="*"`（每天跑），冻结周六 17:00 时它漏跑是**正确语义**（徽章 + 摘要共 2 处文案）。CI 双 retry 都红、本地三连绿的假象来自该断言对渲染时序敏感。修正：周六断言收窄到 mon-fri 任务卡（`screening_daily` 不出现漏跑 + 摘要「未到本期排程日」），全局 count 断言只保留在周四 15:00（全部排程未来到，确定性成立）。修正后连跑 3 次（45.3s / 24.9s / 17.8s）全绿。
- 浏览器抽查初跑曾把 `/api` 打到 vite 默认代理目标（8000 生产栈）——重启前端时显式 `VITE_API_TARGET=127.0.0.1:8001` 后复跑。抽查全称未触碰生产数据（seed 打 8001 直连，页面经代理读 8001）。

## 结论

- [x] 自动化验证全部通过（单测 6 新 + E2E 3 + 后端 seed 块 3）
- [x] 浏览器人工抽查通过：四态可见表现齐备，漏跑/失败样式可区分
- [x] 全部通过，可 archive
