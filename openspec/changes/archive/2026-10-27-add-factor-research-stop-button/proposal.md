# Proposal: add-factor-research-stop-button

## Why

`/factor-research` 的看护控制只有启停开关与「重启管线」按钮。开关是状态位形态（拨到关＝停止），
页面上没有字面「停止」入口：要停下正在跑的研发看护（当前会话 + 后续循环）必须先读出「拨关开关」
这一层含义，操作者容易漏看。设置中心「因子研发配置」分区已有「启动 / 停止 / 重启管线」三个显式
动作，监控页与之不一致。用户要求加一个显式停止按钮。

## What Changes

- `/factor-research` 控制条新增「停止」按钮（常驻可见，位于启停开关与「重启管线」之间）
- 点击经既有二次确认（同一弹窗，明示「将终止当前研发会话」）后调用
  `PUT /api/factor-research/control`（`desired_running=false`）；未确认前零请求
- 与启停开关共享禁用条件（提交中 / `pending` / `restart_pending` / 通道不可用 / 409 封禁）；
  「无可停止对象」（`actual_state="stopped"` 且无运行意图）时禁用
- 派生单一共享源：`deriveControlView` 增 `stopDisabled`（监控页消费，设置分区现有 gating 不动）
- 开关位置语义不变（仍只取 `actual_state`）；零后端 / 端点 / 采集器 / 代理变更

## Capabilities

- **Modified Capabilities**: `frontend`（Requirement: 因子研发监控页）

## Impact

- `frontend/src/lib/factorResearch.ts`：`FactorResearchControlView` 增 `stopDisabled`
- `frontend/src/pages/factors/FactorResearchPage.tsx`：控制条停止按钮
- `frontend/src/test/factorResearchPage.test.tsx`：停止按钮单测
- `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`：真实控制文件写入的 E2E 用例
- 后端 `api.py` / 采集器 / 研发代理：无改动（复用既有控制通道）
