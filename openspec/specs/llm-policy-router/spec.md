# llm-policy-router Specification

## Purpose

定义 PolicyRouter 选择契约：输入 purpose、必需 capability、约束（成本/延迟/数据区域/上下文需求）与候选 profiles（registry + 请求级 + probe 事实），输出 primary profile 与 fallback_chain；fallback 链按 typed error 依序切换 profile 重试，链长有上限，禁止无限循环与静默降级。
## Requirements
### Requirement: PolicyRouter 选择 primary 与 fallback 链

系统 SHALL 提供 PolicyRouter：输入 purpose、必需 capability、约束（成本/延迟/数据区域/上下文需求）与候选 profiles（registry + 请求级 + probe 事实），输出 primary profile 与 fallback_chain。选择规则：过滤不满足硬性 capability 的 profile；按 purpose 策略排序（quick 低延迟、deep 质量、extract 稳定 JSON）；fallback_chain 成员能力 MUST 等价或高于 primary；链长 MUST 有上限。每次选择 MUST 写 trace（profile/provider/model/capability/fallback_chain）。

#### Scenario: 弱工具 provider 被过滤
- **WHEN** purpose=react 要求 tools!=none 且某候选 capability.tools=none 且未显式允许 action 降级
- **THEN** 该候选不进入 primary 与 fallback_chain

#### Scenario: fallback 链能力等价
- **WHEN** primary 支持并行工具调用而某候选仅支持单工具
- **THEN** 该候选不得进入 fallback_chain

### Requirement: fallback 链执行

OutputContract repair 重试耗尽、ContentFiltered、以及不可重试错误（AuthError/ModelNotFound/UnsupportedCapability）SHALL 按 fallback_chain 依序切换 profile 重试；无可用 fallback 或链耗尽时抛出原 typed error。每次切换 MUST 在 trace 记录 `fallback_from`。fallback 执行 MUST 有总次数上限，禁止无限循环。

#### Scenario: 结构化输出合同耗尽后切换
- **GIVEN** primary profile 的输出合同 repair 耗尽且 fallback_chain 非空
- **WHEN** 结构化节点发起下一次重试
- **THEN** 请求改用链中下一 profile，trace 记录 fallback_from=primary

#### Scenario: 链耗尽上抛
- **WHEN** fallback_chain 为空或已全部尝试失败
- **THEN** 抛出最后一个 typed error（不静默降级、不无限重试）

### Requirement: 因子提案用途注册

PolicyRouter SHALL 注册新用途 `factor_proposal`，能力要求为 `json_output`；fallback 链与 typed error 切换行为复用现有机智，且该用途的快速/深度两档分别解析到 quick 与 deep profile。

#### Scenario: 能力不满足时禁用

- **GIVEN** 当前激活 profile 的能力探测结果为 `json_output=False`
- **WHEN** 因子提案节点请求 LLM 调用
- **THEN** 按 fallback 链切换至满足能力的 profile；链耗尽时抛出 typed error，不发起裸调用

