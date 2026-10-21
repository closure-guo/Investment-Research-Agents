# E2E Infrastructure Specification

> 来源：change `add-e2e-test-infrastructure`（F2 E2E 测试基础设施）
> 基线构建日期：2026-07-26
> 说明：本 spec 从 change `add-e2e-test-infrastructure` 的 delta 同步而来，定义 E2E 测试基础设施（Playwright 项目骨架 + 测试模式开关 + 健康端点 + 测试数据端点）的行为契约。

## Purpose

定义 E2E 测试基础设施的行为契约，确保前后端全栈可在 CI 和本地以一致方式拉起、探活并完成 smoke 测试。覆盖以下方面：

- 后端健康端点 `/api/health` 用于 Playwright `webServer` 探活
- `TESTING` 环境变量开关与测试模式行为
- `/api/test/seed` 与 `/api/test/reset` 测试数据端点（F2 骨架，F3 落地实际逻辑）
- TypeScript Playwright 项目骨架位于 `tests/e2e/playwright/`
- `playwright.config.ts` 的双 webServer 配置（后端 + 前端）
- smoke 测试验证全栈可达性

## Requirements

---

### Requirement: Health Endpoint for Readiness Polling

The system SHALL expose a GET `/api/health` endpoint that returns HTTP 200 with a JSON body `{"status": "ok"}`, available without authentication, for use by Playwright's `webServer` config and any CI readiness probe.

> 已存在实现：`src/finance_agent/api.py:462-464`。本需求将其行为契约固化为 spec，避免后续重构时丢失。

#### Scenario: Playwright webServer 探活

- **GIVEN** 后端以 `TESTING=1` 启动，监听 8000 端口
- **WHEN** Playwright `webServer.url` 轮询 `http://localhost:8000/api/health`
- **THEN** 返回 HTTP 200
- **AND** 响应体为 `{"status": "ok"}`

#### Scenario: 非 TESTING 模式下健康端点仍可用

- **GIVEN** 后端以普通模式启动（无 `TESTING=1`）
- **WHEN** 访问 `/api/health`
- **THEN** 返回 HTTP 200 + `{"status": "ok"}`（健康端点不区分测试/生产模式）

### Requirement: Testing Mode Switch

The system SHALL read the `TESTING` environment variable at startup and expose a module-level `TESTING` boolean constant. When `TESTING == "1"`, the system enters test mode: testing-only endpoints are registered, and the LLM client is replaced with a controlled stub (`StubLLMClient`) that emits fixed text deltas at a controlled pace (replacing F2's placeholder `return None`).

(Previously: F2 占位实现--TESTING=1 时 `_make_llm_client` 返回 None，不创建真实 LiteLLMClient。完整 stub 推迟到 F3。)

#### Scenario: TESTING=1 进入测试模式

- **GIVEN** 环境变量 `TESTING=1` 已设置
- **WHEN** 后端启动
- **THEN** `finance_agent.api.TESTING` 常量为 `True`
- **AND** `/api/test/seed` 与 `/api/test/reset` 端点可访问
- **AND** `agent_factory._make_llm_client` 返回 `StubLLMClient` 实例（非 None 占位，非真实 LiteLLMClient）

#### Scenario: 未设 TESTING 时测试端点 404

- **GIVEN** 环境变量 `TESTING` 未设置或非 `"1"`
- **WHEN** 访问 `/api/test/seed` 或 `/api/test/reset`
- **THEN** 返回 HTTP 404（测试端点在非测试模式下不可用）

### Requirement: Test Data Seed and Reset Endpoints

The system SHALL expose `POST /api/test/seed` and `POST /api/test/reset` endpoints **only** when `TESTING=1`. F2 implements endpoint skeletons that return `{"status": "ok", "mode": "testing"}`; the actual seed/reset logic is deferred to F3.

#### Scenario: 测试模式下调用 seed 端点

- **GIVEN** 后端以 `TESTING=1` 启动
- **WHEN** POST `/api/test/seed` with body `{"symbol": "300308"}`
- **THEN** 返回 HTTP 200
- **AND** 响应体为 `{"status": "ok", "mode": "testing"}`（F2 骨架响应，不实际造数据）

#### Scenario: 测试模式下调用 reset 端点

- **GIVEN** 后端以 `TESTING=1` 启动
- **WHEN** POST `/api/test/reset`（body 可空）
- **THEN** 返回 HTTP 200
- **AND** 响应体为 `{"status": "ok", "mode": "testing"}`（F2 骨架响应，不实际清理）

### Requirement: Playwright Project Skeleton

The system SHALL have a TypeScript Playwright project at `tests/e2e/playwright/` with `package.json`, `playwright.config.ts`, and `tests/smoke.spec.ts`. The project SHALL be runnable via `npx playwright test` from the `tests/e2e/playwright/` directory.

#### Scenario: 项目结构完整

- **GIVEN** 仓库已 clone
- **WHEN** 检查 `tests/e2e/playwright/` 目录
- **THEN** 存在 `package.json`（含 `@playwright/test` 依赖）
- **AND** 存在 `playwright.config.ts`（含双 webServer 配置）
- **AND** 存在 `tests/smoke.spec.ts`

#### Scenario: 一条命令跑测试

- **GIVEN** Node 22+ 已安装，`tests/e2e/playwright/` 已 `npm install`
- **WHEN** 在 `tests/e2e/playwright/` 执行 `npx playwright test`
- **THEN** Playwright 自动拉起前后端 webServer
- **AND** smoke.spec.ts 全绿
- **AND** 退出码为 0

### Requirement: Dual webServer Configuration

The `playwright.config.ts` SHALL configure two `webServer` entries: one for the Python backend (with `TESTING=1` env), one for the Vite frontend. Both SHALL poll their respective URL on startup and only proceed when reachable.

#### Scenario: 后端 webServer 配置

- **GIVEN** `playwright.config.ts` 已加载
- **WHEN** Playwright 启动后端 webServer
- **THEN** 执行命令含 `TESTING=1` 环境变量
- **AND** 命令为 `uv run uvicorn finance_agent.api:app --port 8000`（在 repo-root 执行）
- **AND** 轮询 `http://localhost:8000/api/health` 直到返回 200
- **AND** `reuseExistingServer: true`（本地复用，CI 全新启动）

#### Scenario: 前端 webServer 配置

- **GIVEN** `playwright.config.ts` 已加载
- **WHEN** Playwright 启动前端 webServer
- **THEN** 命令为 `npm run dev -- --port 5173`（在 `frontend/` 执行）
- **AND** 轮询 `http://localhost:5173` 直到可达
- **AND** `reuseExistingServer: true`（本地复用，CI 全新启动）

### Requirement: Smoke Test Verifies Full Stack Reachability

The `smoke.spec.ts` SHALL verify that (1) the frontend page loads with expected title, and (2) the backend `/api/health` responds 200. It SHALL NOT trigger `/api/analyze` or `/api/chat` (those are F3 specs).

(Previously: 前端标题断言为 `/Finance Analysis Agent/i`，其余行为相同。)

#### Scenario: 前端首页可达

- **GIVEN** 双 webServer 已启动
- **WHEN** `smoke.spec.ts` 执行 `page.goto('http://localhost:5173')`
- **THEN** 页面加载完成
- **AND** `await expect(page).toHaveTitle(/Investment Research Agents/i)` 通过

#### Scenario: 后端健康端点可达

- **GIVEN** 双 webServer 已启动
- **WHEN** `smoke.spec.ts` 执行 `request.get('http://localhost:8000/api/health')`
- **THEN** 响应状态为 200
- **AND** 响应体 `status` 字段为 `"ok"`
