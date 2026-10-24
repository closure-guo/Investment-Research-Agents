# Tasks: add-composer-stat-pills

- [x] `UsageAccumulator` 记录单次请求峰值 prompt tokens，且有单测覆盖（含无 usage 保持 0）
- [x] `sessions` 表新增 4 个计量列并可幂等迁移，`update_session_usage` 累加语义有单测
- [x] deep 分析线程内激活 usage 收集并在结束时落库，单测证明 provider usage 进入会话记录
- [x] quick 模式 Agent 层累计 usage 并在 AG-UI/SSE 收尾落库，chat 会话 steps 回退为 assistant 消息数
- [x] `GET /api/sessions/{id}/stats` 返回真实轮次/步骤/耗时/token/上下文峰值，404 与无数据行为有单测
- [x] 输入框下方渲染三枚计量控件，数据缺失各自隐藏，替换免责声明
- [x] 控件交互（互斥开合、Escape/外部点击关闭、锚定位置）有组件测试
- [x] E2E spec 已覆盖核心交互场景（`tests/e2e/playwright/tests/composer-stat-pills.spec.ts`，3 passed）
- [x] 报告页脚与导出产物仍保留免责声明（回归断言）
- [x] `uv run pytest` 相关子集、`cd frontend && npm test`、`npm run build` 全部通过
- [x] 人工验证报告落 `tests/validation/`（2026-10-24 浏览器人工抽查完成，真实 provider，见报告「浏览器人工抽查」节）
