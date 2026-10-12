# Delta for agent-prompt-contracts

## MODIFIED Requirements

### Requirement: Trader 决策语义契约

trader 提示词 MUST 定义 action 各档位的语义与 position_size 档位含义，并为 confidence 提供分档锚点。sell 档位语义 MUST 按 `sell_type` 分型表述（update-sell-action-typing，issue #188）：**exit**（持有者减仓/退出敞口——MUST 申报 `exit_schedule` 减仓节奏，MUST NOT 套用建仓价位模板，重新介入条件写入 `reeval_triggers`）与 **short**（做空建仓——沿用 entry/stop/target 必填模板）。risk_judge 提示词 MUST 含同款 sell_type 语义与申报要求（终稿层与 Trader 同契约，避免两层语义漂移）。

提示词中关于未申报 `sell_type` 的兜底表述 MUST 为「打回后仍缺默认按 **exit** 处理并如实标注」（add-holdings-context：`exit` 对 A 股散户是可执行默认，`short` 需两融信用账户），MUST NOT 表述为默认按短模板处理。

(Previously: 提示词表述未申报 sell_type 的兜底为「打回后仍缺默认按 short 处理并如实标注」——与生产实际分型语义相反（生产 13/13 为持有者减仓话术），且把受众推向 A 股散户普遍不可执行的做空建仓模板。)

#### Scenario: Trader 输出带语义的决策

- **WHEN** 加载 trader 提示词
- **THEN** 模板中包含 action（buy/sell/hold/watch）各档位的行为语义描述
- **AND** 模板中包含 position_size 档位定义（如 light/moderate/heavy 对应仓位区间）
- **AND** 模板中包含 confidence 分档语义（如 0.7+ 高置信、0.4-0.7 中等、<0.4 低置信）

#### Scenario: sell 分型语义与申报要求

- **WHEN** 加载 trader 或 risk_judge 提示词
- **THEN** 模板中包含 sell_type 双型语义（exit=持有者减仓/退出敞口；short=做空建仓）与对应参数要求（exit 申报 exit_schedule、short 申报 entry/stop/target）
- **AND** 模板中说明 sell 决策 MUST 申报 sell_type（未申报将被校验打回，打回后仍缺默认按 exit 处理并如实标注）

## ADDED Requirements

### Requirement: 决策层受众可执行性约束

trader 与 risk_judge 提示词 MUST 包含受众可执行性约束段，声明受众约束与持仓三态下的方向表达纪律：

- **受众约束**：受众为 A 股普通证券账户投资者。做空建仓（`sell_type=short`）需融资融券信用账户，且标的须为两融标的、券源可得——MUST NOT 把 `short` 当作默认看空表达；`short` 仅在受众明确要求做空研究时使用。
- **持仓三态的方向表达**：受众已持有 → 看空表达为 `exit`（减仓/退出敞口，可执行）；受众未持有 → 看空表达为 `watch`（观望/回避），MUST NOT 输出 `exit`（无敞口可退）；受众持仓未申报 → 看空按 `exit` 表达，并在 `reasoning` 中说明未持有情形的对应处置。
- **结论可执行性**：决策结论 MUST 落到受众可执行的动作上（建仓买入 / 减仓卖出 / 观望），MUST NOT 以受众无法执行的动作作为唯一结论。

#### Scenario: 提示词含受众约束段

- **WHEN** 加载 trader 或 risk_judge 提示词
- **THEN** 模板中 SHALL 包含 A 股普通账户不可做空的受众约束声明
- **AND** 模板中 SHALL 包含持仓三态各自的方向表达纪律（已持有→exit、未持有→watch、未申报→exit 并说明未持有处置）

#### Scenario: 提示词合同测试拦截缺失

- **WHEN** 对 trader / risk_judge 提示词运行契约测试
- **THEN** 受众约束段缺失或三态纪律枚举不全时测试 SHALL 失败
