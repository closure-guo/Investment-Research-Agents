## Why

LLM 配置分区目前是一张平铺长表单：上下文长度排在 API Key 之前，已保存密钥会回填进输入框，校验错误只在底部一次性提示。DeepSeek Harness（MIT）的 Models 页用「分组卡片 + 只写密钥 + 行内状态」把同类字段组织得更易扫读，且从不把已存密钥读回输入框。

## What Changes

- 表单重排为三段：**连接**（Provider 预设卡片、API Base URL、API Key、测试连接）、**模型**（模型名称与发现、上下文窗口、最大输出、思考模式）、**高级**（API 形式、配置管理）。
- API Key 改为**只写**：已保存时输入框为空并提示「已保存（不回显）」，输入新值即替换；带显示/隐藏眼睛按钮。
- 新增行内状态点：密钥已配置为绿、未配置为灰；测试连接成功/失败就近显示。
- 字段级校验：API Base URL 必须是 `http(s)://`；上下文长度必须是正整数；非法时字段下红字并阻止保存。
- 「最大输出」只读展示 probe 得到的 `capability.max_output`（未探测时显示「测试连接后探测」）。
- 既有持久化（`fa_llm_profiles`）、旧 key 迁移、占位符默认值、多 profile 管理语义保持不变。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `llm-config`: 「前端设置面板支持自定义 LLM 配置」的分组、密钥只写与字段级校验行为变更。

## Impact

- 前端：`frontend/src/pages/settings/panes/LlmConfigPane.tsx` 重写渲染层（props 不变）；`frontend/src/test/settingsProfileSwitch.test.tsx` 断言改为「密钥不回显」；新增组件测试。
- 不涉及后端、API 契约与存储结构。
