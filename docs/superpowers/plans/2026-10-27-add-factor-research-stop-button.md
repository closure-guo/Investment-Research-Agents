# Factor Research Stop Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** `/factor-research` 控制条新增显式「停止」按钮（二次确认 → `PUT /api/factor-research/control` `desired_running=false`），与既有启停开关共享禁用条件与确认弹窗，零后端变更。

**Architecture:** 派生集中在 `frontend/src/lib/factorResearch.ts` 的 `deriveControlView`（唯一控制视图单源），监控页只消费 `stopDisabled`；按钮与开关关闭方向走同一 `submitControl(false)` 路径，因此不存在第二写路径。

**Tech Stack:** React 18 + TS + Vitest/Testing Library（单测）+ Playwright（E2E，真实后端 + 隔离 fixture 文件）。

## Global Constraints

- 开关位置语义不变：位置只取 `actual_state`，MUST NOT 由意图决定
- 停止按钮禁用条件与开关一致：`submitting` / `restartSubmitting` / `pending` / `restartPending` / `channelBlocked` / 通道不可达
- 另加「无可停止对象」禁用：`actual_state="stopped"` 且 `control.desired_running !== true`
- 二次确认文案复用既有「将终止当前研发会话」弹窗；未确认前零请求
- 零后端 / 端点 / 采集器 / 代理变更

---

### Task 1: `stopDisabled` 派生（lib）

**Files:**
- Modify: `frontend/src/lib/factorResearch.ts`（`FactorResearchControlView` + `deriveControlView`）
- Test: `frontend/src/test/factorResearchPage.test.tsx`

**Interfaces:**
- Produces: `FactorResearchControlView.stopDisabled: boolean`（消费方 = 监控页停止按钮）

- [ ] Step 1: 在页面测试新增红灯用例（运行中可用 / 确认后请求体 / 取消零请求 / 已停止禁用 / 有待启动意图可撤销 / pending 禁用 / 通道不可用禁用）
- [ ] Step 2: `npx vitest run src/test/factorResearchPage.test.tsx` → 新增用例 FAIL（按钮不存在）
- [ ] Step 3: 最小实现：`const stopDisabled = switchDisabled || (actualState === 'stopped' && control?.desired_running !== true)`
- [ ] Step 4: 重跑 → PASS

### Task 2: 控制条停止按钮（page）

**Files:**
- Modify: `frontend/src/pages/factors/FactorResearchPage.tsx`

- [ ] Step 1: 在开关与「重启管线」之间插入 `Button`（`data-testid="factor-research-stop-button"`，`fa-stop` 图标，文案「停止」），`disabled={stopDisabled}`，点击 `setConfirmOpen(true)`
- [ ] Step 2: 复用既有确认弹窗（`factor-research-control-confirm`）→ `submitControl(false)`
- [ ] Step 3: 跑全部前端测试 + `tsc` + eslint

### Task 3: E2E（交互门禁）

**Files:**
- Modify: `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`

- [ ] Step 1: 新增用例「6b」：快照 running + 隔离控制文件前置守卫 → 点「停止」→ 弹窗可见且控制文件不存在 → 确认 → 轮询控制文件 `desired_running=false` 真值 → pending 期间按钮禁用 → 推进快照后「已关闭」
- [ ] Step 2: `npx playwright test tests/factor-research-monitor.spec.ts --config playwright.config.ts` → 全绿

### Task 4: 验证与收口

- [ ] `npm test -- --run`（前端全量）、`npx tsc --noEmit`、`npm run lint`
- [ ] E2E 门禁（Task 3）
- [ ] 人工验证报告 `tests/validation/2026-10-27-add-factor-research-stop-button-validation.md`
- [ ] `openspec validate add-factor-research-stop-button --strict` → sync + archive
