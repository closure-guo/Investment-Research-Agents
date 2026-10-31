# Proposal: remove-standalone-stop-button

## Why

页面级独立「停止生成」按钮（App.tsx fixed 容器，`bottom: 90px`）是 #43 / incident 013 时代的旧停止入口。此后 `adopt-assistant-ui-chat` 已把停止主入口迁移到输入框 Composer（运行中发送键切换为停止按钮，主规范「会话运行中禁止发送」「输入区 Composer」两条 Requirement 已契约化），独立按钮成为冗余残留。且它存在实测缺陷：`bottom: 90px` 按旧版输入框高度硬编码，现输入框（模式切换行 + Composer + 计量条）实际高度约 110–130px；按钮容器与输入框同为 z-40、DOM 序靠后，输入框绘制在上层，按钮下半截被遮挡、露出部分落输入框点击区内基本不可点。

## What Changes

- 删除 App.tsx 中页面级独立「停止生成」按钮及其 fixed 容器（运行态三种条件全部已被 Composer 停止按钮覆盖：深度运行态 → `deepRunning` → `ComposerPrimitive.Cancel`；消息流式中 → 同左；quick run → `composerSend` 显式停止按钮分支）。
- E2E 两个以独立按钮为「流式进行中/已结束」观察代理的定位符迁移到 `composer-stop`：`message-actions.spec.ts`（timeline config）、`refresh-resume-accept.spec.ts`（@live 人工验证）。
- 新增契约：停止入口唯一——运行期间停止入口 SHALL 唯一为输入框 Composer 停止按钮，页面 SHALL NOT 渲染页面级独立停止按钮。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: frontend（新增「停止入口唯一」Requirement，随既有「会话运行中禁止发送」「输入区 Composer」语义）

## Impact

- `frontend/src/App.tsx`：删除独立按钮块（约 19 行），无接口变化；`stopGeneration` 仍被 Composer 停止桥与 `AnalysisRuntimeProvider.onCancel` 使用。
- `tests/e2e/playwright/tests/message-actions.spec.ts`、`tests/e2e/playwright/tests/refresh-resume-accept.spec.ts`：定位符替换，断言语义（流式进行中 / 运行已结束）不变。
- 无后端改动；无数据迁移。
