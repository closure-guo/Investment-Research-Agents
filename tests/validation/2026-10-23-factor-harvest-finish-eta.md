# 人工验证报告：收割链路收尾态口径修正

**日期**：2026-10-23
**关联 delta**：`openspec/changes/add-factor-harvest-ui/`（T7，spec「运行结束后不冒充预计」/「已收尾不渲染预计还需」）
**触发**：线上页面停在「已完成 · 批次 5/12 · 剩余 36 · 预计还需 38分5秒」，用户判断「应该已经完成了」
**E2E 门禁**：`tests/e2e/playwright/playwright-report`（`npx playwright test tests/factor-harvest-ui.spec.ts` → 1 passed，真实栈、未 mock 业务接口）

## 现象

2026-10-22 23:45 前后，`/factor-research` 的「因子收割链路」区块同时显示：

```
已完成   批次 5/12   已处理 896   剩余 36   本轮入库 2 · 拒收 64   预计还需 38分5秒
```

四小时后读数不变（只缓慢变大），看起来「卡住没跑完」。

## 根因

1. **任务其实已收工**：`job_runs` run_id=227 `status=ok`、`19:55:43 → 21:12:23`、
   summary `batches=5, drained=true, proposed=216, admitted=2, rejected=59`；
   `harvest_state.json` 与批内心跳同日 `21:12:23` 落盘（`phase=done`）。
2. **ETA 是纯时钟函数**：`eta = pending / (本轮处理数 / 自 started_at 已过时长)`。收尾后分子
   216 冻结、分母继续增长 → `eta = elapsed / 6`，每 6 分钟涨 1 分钟，永不收敛。
   实测：21:20→14分、22:00→21分、23:00→31分、23:45→38分、23:52→38分30秒。
3. **两套窗口并排**：`batches_done` / `本轮入库·拒收` 是「本轮窗口」（自 started_at），
   `已处理` / `剩余` 是「全局队列」（候选文件 − 已处理）。21:12 之后 RD-Agent 继续产候选
   （`candidates.json` 23:26 仍在写），队列里的 36 条属于**下一次**运行，不是本轮尾巴。
4. **`批次 5/12`** 的 12 是单次运行上限（`DEFAULT_MAX_BATCHES_PER_RUN`），本轮 5 批已清空当时队列，
   带上分母即被读成「12 批只跑了 5 批」。

## 修复

- 后端 `src/finance_agent/api.py::_factor_research_harvest_payload`：`eta_s` 仅在
  `job.status == "running"` 时计算，已收尾一律 `null`（队列 `pending` 照实返回）。
- 前端 `frontend/src/pages/factors/FactorResearchPage.tsx`：进度区按运行/收尾两态分流——
  运行中保留「批次 N/12 + ETA + 秒级时钟」；收尾后改「本轮 N 批 · 入库 x · 拒收 y」「已处理」
  「队列剩余 M · 待下次 MM-DD HH:mm 自动跑」，不再渲染 ETA 与上限分母。

## 验证证据

| 项 | 命令 / 读数 | 结果 |
|---|---|---|
| 后端单测 | `uv run pytest tests/test_api_factor_research.py -q` | 70 passed（新增 `test_finished_run_reports_no_eta[ok|failed]`，改前实测 `eta_s=15445` 红） |
| 前端单测 | `npx vitest run src/test/factorResearchPage.test.tsx` | 41 passed（新增「已收尾：不渲染预计还需，队列归属下次自动跑」，改前红） |
| 类型/风格 | `npx tsc -b`、`uv run ruff check` | 0 错误 / All checks passed |
| 真实端点 | `GET /api/factor-research/harvest` | `status=ok`、`finished_at=21:12:23`、`pending=39`、`eta_s=null`、`next_fire_time=2026-10-23T15:55:00+08:00` |
| E2E | `npx playwright test tests/factor-harvest-ui.spec.ts` | 1 passed（真实后端 + 真实页面） |
| 页面文案 | Playwright 读 DOM | 「已完成」「本轮 5 批 · 入库 2 · 拒收 64」「已处理 896」「队列剩余 39 · 待下次 10-23 15:55 自动跑」，无「预计还需」 |

截图：`tests/e2e/playwright/tmp/harvest-ui-after-fix.png`

## 结论

- [x] 收尾态不再出现「已完成 + 预计还需」的自相矛盾读数
- [x] 队列剩余正确归属下一次运行，并给出下次自动跑时刻
- [x] 运行中口径（批次进度 / ETA / 秒级活性）未回退
- [ ] archive（留待 `add-factor-harvest-ui` 整体收口）
