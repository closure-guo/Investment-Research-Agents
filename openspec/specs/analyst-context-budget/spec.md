# analyst-context-budget Specification

## Purpose
TBD - created by archiving change improve-analyst-throughput. Update Purpose after archive.
## Requirements
### Requirement: 技术指标上下文裁剪

系统 SHALL 在构建 technical_analyst 的 LLM context 时，将每个技术指标序列（MA/MACD/RSI/BOLL/KDJ 等等长 list）裁剪为最近 60 期；K 线窗口不足 60 期时 SHALL 保持全部期数。裁剪 SHALL 附带窗口说明（序列为最近 N 期、更早历史已省略），使 LLM 不会将截断窗口误认为全部历史。

#### Scenario: 全窗口指标裁剪到最近 60 期

- GIVEN compute_metrics 产出 250 期等长指标序列（含前 N 期 null 预热段）
- WHEN 构建 technical_analyst 的 LLM context
- THEN 每个指标序列 SHALL 只包含最近 60 期
- AND context SHALL 包含窗口说明文本（最近 N 期、更早历史已省略）

#### Scenario: 短窗口保持完整

- GIVEN K 线窗口为 45 期
- WHEN 构建 technical_analyst 的 LLM context
- THEN 指标序列 SHALL 保持 45 期不裁剪

#### Scenario: 指标缺失时保持既有兜底

- GIVEN state 中无 technical_indicators
- WHEN 构建 technical_analyst 的 LLM context
- THEN context SHALL 不包含技术指标数据段（与既有行为一致）

### Requirement: 记忆块预算核算

记忆块（情景记忆 Top-5 + 语义条款）SHALL 计入分析师上下文预算；预算不足时 SHALL 按「语义条款优先于情景记忆、情景记忆按结算结果相关性降序截断」的顺序裁剪，并记录裁剪事件。

#### Scenario: 预算不足裁剪

- **GIVEN** 上下文预算剩余不足容纳全部 5 条情景记忆
- **WHEN** 上下文组装执行
- **THEN** 语义条款完整保留，情景记忆按相关性降序截断至预算内
- **AND** 裁剪事件记入 trace

