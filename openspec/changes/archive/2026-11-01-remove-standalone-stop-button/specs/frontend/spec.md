# Delta for frontend

## ADDED Requirements

### Requirement: 停止入口唯一（Composer 停止按钮）

深度分析与快速对话运行期间，停止入口 SHALL 唯一为输入框 Composer 的停止按钮（深度模式停止 SHALL 经 `POST /api/sessions/{id}/cancel`，quick run 停止 SHALL 经本地 abort）；页面 SHALL NOT 渲染页面级独立停止按钮（原 fixed 底部「停止生成」按钮）。

#### Scenario: 运行态不渲染独立停止按钮

- GIVEN 深度分析或快速对话流式进行中
- WHEN 页面渲染运行态
- THEN 页面 SHALL NOT 出现「停止生成」文字的页面级独立按钮
- AND 输入框 Composer 的停止按钮为唯一停止入口且位于输入框右下角可点击

#### Scenario: 运行结束后停止入口恢复为发送键

- GIVEN 深度分析运行中，停止入口为 Composer 停止按钮
- WHEN 收到终态事件（done / interrupted / error）
- THEN Composer 停止按钮消失，发送键恢复
- AND 页面 SHALL NOT 残留任何停止按钮
