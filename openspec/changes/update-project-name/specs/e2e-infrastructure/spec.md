## MODIFIED Requirements

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
