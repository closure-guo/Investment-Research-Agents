# Tasks: add-factor-research-stop-button

- [x] `/factor-research` 控制条新增常驻「停止」按钮（二次确认后 `PUT /api/factor-research/control` 写 `desired_running=false`）
- [x] `deriveControlView` 派生 `stopDisabled`（与开关同禁用源；`stopped` 且无运行意图 → 禁用），开关位置语义不变
- [x] 单测：运行中可点 / 确认后请求体仅布尔 / 取消零请求 / 已停止禁用 / `pending` 禁用 / 通道不可用禁用（factorResearchPage：53 passed）
- [x] E2E spec 覆盖停止按钮（真实端点 + 隔离 fixture 控制文件真值断言；专属端口 8009/5182，1/1 绿；整份 monitor 套件回归 23/23 绿）
- [x] 人工验证报告落 `tests/validation/`
- [x] sync + archive（`openspec validate --strict` 通过）
