# Tasks: refine-llm-settings-pane

- [x] LlmConfigPane 按「连接 / 模型 / 高级」三段渲染，props 与外部行为不变
- [x] API Key 只写：已存密钥不回显、输入即替换、眼睛切换
- [x] 密钥/测试连接状态以行内状态点呈现
- [x] API Base URL 与上下文长度字段级校验（非法时红字 + 阻止保存）
- [x] 「最大输出」只读展示 probe 能力（后端 capability 已补 max_output）
- [x] 更新 settingsProfileSwitch 测试并新增分组/只写/校验用例
- [x] E2E spec 已覆盖三段分组、只写密钥与字段级校验（3 passed）
- [x] `cd frontend && npm test` 与 `npm run build` 通过
- [x] 人工验证报告落 `tests/validation/`（2026-10-24 浏览器人工抽查完成，真实 probe 联动，见报告「浏览器人工抽查」节）
