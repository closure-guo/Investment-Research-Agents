## Why

输入框下方目前只有一行静态免责声明（`AI 生成仅供参考，不构成投资建议`），占用了本可以持续展示运行健康度的位置。用户需要在不打开设置/日志的前提下，随时看到本次会话的轮次与步骤、真实 token 消耗、以及最近一次分析占用了多少上下文窗口。DeepSeek Harness 的开源实现（MIT）已用同一位置的三枚计量控件验证了该信息密度。

## What Changes

- 在聊天输入框下方新增一行「计量条」，由三个可点开的控件组成：
  - **会话统计**：显示「N 轮 M 步」；点开面板展示最近一次分析的管线总用时、步骤数、轮次数。
  - **Token 用量**：显示会话累计 token（输入+输出）；点开面板展示输入、输出与合计的精确值。
  - **上下文已用**：14px 环形进度 + 百分比；点开面板展示 `~已用 / 窗口` 与占用条。窗口取当前激活 profile 的 `contextLength`，缺省回退后端静态默认。
- 计量条替换输入框下方的免责声明；免责声明只在报告页脚保留（合规文案不丢失）。
- 后端新增会话级用量记账：deep 分析结束时把 provider 真实 usage（prompt/completion/total）与单次请求峰值 prompt tokens 落库到 `sessions`。
- 新增 `GET /api/sessions/{session_id}/stats` 聚合端点，返回轮次、步骤、耗用与上下文峰值，供前端计量条读取。
- 三个控件在数据缺失时**各自隐藏**（不渲染占位、不显示 0 冒充真值），与 DSH 的「无投影不渲染」语义一致。

## Capabilities

### New Capabilities

- `composer-stat-pills`: 输入框下方计量条的三控件 UI 契约、数据端点契约、缺失数据隐藏规则、免责声明迁移规则。

### Modified Capabilities

（无：现有 `session-persistence` 只约定会话读写，不约定计量字段；本次以新能力承载。）

## Impact

- 后端：`src/finance_agent/session_store.py`（新增列与迁移、`update_session_usage`）、`src/finance_agent/agent_factory.py`（deep 分析线程内激活 usage 收集并落库）、`src/finance_agent/nodes/_llm_utils.py`（累加器记录单次峰值）、`src/finance_agent/api.py`（stats 端点）。
- 前端：新增 `frontend/src/components/ComposerStats.tsx`；`frontend/src/App.tsx` 的 `ChatInputBar` 挂载计量条并移除免责声明；`frontend/src/types.ts` 增加 stats 类型。
- 数据：SQLite `sessions` 表新增 `tokens_prompt`、`tokens_completion`、`tokens_total`、`context_peak_tokens` 四列（幂等迁移，旧库自动补齐）。
- 不影响：五层管线节点契约、SSE 事件结构、既有会话字段语义。
