# Remove Standalone Stop Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 删除页面级独立「停止生成」按钮，停止入口唯一化为输入框 Composer 停止按钮。

**Architecture:** 纯前端删除 + E2E 定位符迁移。运行态三种条件（深度运行/消息流式/quick run）已全部被 Composer 停止按钮覆盖（`deepRunning` → `ComposerPrimitive.Cancel`；quick → `composerSend` 显式停止分支），独立按钮无独占场景，删除无功能回退。

**Tech Stack:** React + vitest + testing-library；Playwright（stub 套件）。

## Global Constraints

- 验收基准 = 主规范 `frontend/spec.md`「SSE Stream Abort Control / 显式停止生成」不变：停止仍走 `POST /api/sessions/{id}/cancel`（深度）/ 本地 abort（quick），半截回复保留中断标记。
- E2E 禁 mock 被测系统；stub 套件经隔离端口对运行（生产栈占 8000/5173）。
- E2E selector 用既有稳定 testid：`composer-stop` / `send-button`，禁止盲写。

---

### Task 1: 失败测试（红）

**Files:**
- Create: `frontend/src/test/standalone-stop-button-removal.test.tsx`

**Interfaces:**
- Consumes: App 渲染（无 props）、既有 fetch mock 形态（/api/sessions、/api/llm-config、/api/analyze SSE）
- Produces: 断言「运行态 queryByText('停止生成') 为 null + composer-stop 在」「终态后 send-button 恢复 + composer-stop 消失」

- [ ] **Step 1**: 写测试（运行态流保持打开）
- [ ] **Step 2**: 运行 `npx vitest run src/test/standalone-stop-button-removal.test.tsx` → 期望 FAIL（现按钮存在）

### Task 2: 删除实现（绿）

**Files:**
- Modify: `frontend/src/App.tsx:1108-1126`（删除注释 + fixed 容器整块）

- [ ] **Step 3**: 删除按钮块
- [ ] **Step 4**: 同测试 → 期望 PASS；跑全量前端单测确认无回归

### Task 3: E2E 定位符迁移

**Files:**
- Modify: `tests/e2e/playwright/tests/message-actions.spec.ts`（timeline 套件）
- Modify: `tests/e2e/playwright/tests/refresh-resume-accept.spec.ts`（@live 人工验证，stub 不跑）

- [ ] **Step 5**: 「停止生成」定位符 → `composer-stop`，语义不变
- [ ] **Step 6**: 隔离端口对跑默认 stub 套件 + timeline 套件（含 message-actions）

### Task 4: 收口

- [ ] **Step 7**: commit + PR
- [ ] **Step 8**: 人工验证报告 → `tests/validation/`，sync + archive
