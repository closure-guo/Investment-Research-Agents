# Delta for agent-prompt-contracts

## MODIFIED Requirements

### Requirement: Trader 决策语义契约

trader 提示词 MUST 定义 action 各档位的语义与 position_size 档位含义，并为 confidence 提供分档锚点。sell 档位语义 MUST 按 `sell_type` 分型表述（update-sell-action-typing，issue #188）：**exit**（持有者减仓/退出敞口——MUST 申报 `exit_schedule` 减仓节奏，MUST NOT 套用建仓价位模板，重新介入条件写入 `reeval_triggers`）与 **short**（做空建仓——沿用 entry/stop/target 必填模板）。risk_judge 提示词 MUST 含同款 sell_type 语义与申报要求（终稿层与 Trader 同契约，避免两层语义漂移）。

提示词中关于未申报 `sell_type` 的兜底表述 MUST 为「打回后仍缺默认按 **exit** 处理并如实标注」（add-holdings-context：`exit` 对 A 股散户是可执行默认，`short` 需两融信用账户），MUST NOT 表述为默认按短模板处理。

trader 与 risk_judge 提示词 MUST 含 **buy 入场形态申报契约**（update-conditional-entry-gate-waiver）：buy 决策 MUST 在 reasoning 中申报入场形态——**立即市价入场**或**条件入场**；条件入场以 `trigger_high`（上破确认位，MUST 严格高于最新收盘价）或 `trigger_low`（回踩承接位，MUST 严格低于最新收盘价）承载，语义与 watch 触发位同口径。技术面逆势（收盘价低于 MA20 与 MA60，中期均线结构未收复）或核心论点兑现期显著长于 T+20 考核窗口时，SHOULD 默认条件入场；选择立即市价入场 MUST 在 reasoning 中显式申报理由。

(Previously: buy 决策无入场形态申报要求——`trigger_high`/`trigger_low` 仅约束 watch；601066（2026-11-11）实证：技术逆势 + 窗口错配场景输出立即市价入场 buy 且 trigger 字段均为 null，回踩限价/突破确认类条件入场零使用。)

#### Scenario: Trader 输出带语义的决策

- **WHEN** 加载 trader 提示词
- **THEN** 模板中包含 action（buy/sell/hold/watch）各档位的行为语义描述
- **AND** 模板中包含 position_size 档位定义（如 light/moderate/heavy 对应仓位区间）
- **AND** 模板中包含 confidence 分档语义（如 0.7+ 高置信、0.4-0.7 中等、<0.4 低置信）

#### Scenario: sell 分型语义与申报要求

- **WHEN** 加载 trader 或 risk_judge 提示词
- **THEN** 模板中包含 sell_type 双型语义（exit=持有者减仓/退出敞口；short=做空建仓）与对应参数要求（exit 申报 exit_schedule、short 申报 entry/stop/target）
- **AND** 模板中说明 sell 决策 MUST 申报 sell_type（未申报将被校验打回，打回后仍缺默认按 exit 处理并如实标注）

#### Scenario: buy 入场形态申报契约

- **WHEN** 加载 trader 或 risk_judge 提示词
- **THEN** 模板中包含 buy 入场形态申报要求（立即市价 vs 条件入场，MUST 在 reasoning 申报）
- **AND** 模板中说明条件入场以 trigger_high（上破确认，严格高于收盘）或 trigger_low（回踩承接，严格低于收盘）承载，与 watch 触发位同口径
- **AND** 模板中包含技术逆势（收盘低于 MA20 与 MA60）与窗口错配（论点兑现期显著长于考核窗）场景 SHOULD 默认条件入场的指引
- **AND** 模板中说明选择立即市价入场 MUST 显式申报理由

### Requirement: 决策层否决纪律与参数协商

risk_judge 提示词 MUST 含**否决纪律**：方向否决（把 buy/sell 改为 hold/watch）仅限致命白名单——财务造假嫌疑、退市/面值退市风险、流动性枯竭无法成交、受众无法交易、关键数据被判定不可信；MUST NOT 以「止损窄于噪声」「赔率不足」「数据部分缺失」「无催化剂」作为方向否决理由。执行参数缺陷 MUST 二选一：①产出**联合修正方案**——放宽止损至 ≥2×日波动或最近结构位，并等比下调仓位档位（单笔风险预算不变；价位继承规则允许调整数值，修正后价位 MUST 落在价位参考带 sanity 范围内）；②维持方向并显式申报**可执行参数缺口**（缺口内容 = 满足可执行所需的止损宽度与仓位档位）。MUST NOT 既不修正也不申报缺口就输出**任何 action**——该二选一覆盖 buy/sell 在内的全部输出（update-conditional-entry-gate-waiver）；选择不修正参数而放行（如以仓位档位、T+20 复核机制承担缺口）时 MUST 归入②申报，MUST NOT 以「不构成缺口」「由仓位档位/复核机制承担」等表述替代申报。

(Previously: 禁令仅覆盖「输出 hold/watch」形态——输出 buy/sell 且带着未处置执行参数缺口不受该条款约束，601066（2026-11-11）实证由此绕开赔率闸。)

#### Scenario: 提示词含否决纪律与二选一义务

- **WHEN** 加载 risk_judge 提示词
- **THEN** 模板中包含方向否决致命白名单枚举
- **AND** 模板中包含执行参数缺陷二选一义务（联合修正 / 显式缺口申报）
- **AND** 模板中的「既不修正也不申报」禁令覆盖全部 action（含 buy/sell），而非仅 hold/watch
- **AND** 模板中说明弃权修正（以仓位档位/复核机制承担缺口）MUST 归入缺口申报，不得以「不构成缺口」话术绕开

### Requirement: 风控与审批评级量表

risk_judge、fund_manager 提示词 MUST 提供评级/决策选项的语义说明，指导在证据均衡或不足时如何取舍——证据均衡（多空对峙）只要求 reasoning 如实披露对峙点，MUST NOT 表述为「倾向 hold/watch」的方向裁决规则（方向裁决纪律见「决策层否决纪律与参数协商」）。fund_manager 提示词 MUST 额外定义审批理由的职责边界：approve 理由限定于风控结论一致性、论据矛盾处理与执行前提；MUST NOT 对标的作方向性投资判断（如「适合长期价值投资」——该类判断属研究层，FM 无分析师报告输入而无依据）。

fund_manager 提示词 MUST 含**审批前核查**条款（条件式表述——上下文出现对应段时才生效）：上下文含「终稿完整性标注」「估值完整性标注」「辩论锚点告警」任一段时，`reasoning` MUST 显式回应出现的每一段（为何放行、或作为 reject/return 的依据），MUST NOT 对出现的标注/告警保持沉默；上下文含「数据口径披露」段时，SHALL 将其作为决策数字与管线确定性计算的交叉核对基准，发现口径冲突时 MUST 在 reasoning 中说明。审批前核查条款 MUST NOT 禁止 approve（仲裁权保留）。
(Previously: risk_judge 评级量表含「当多空/风险论据证据均衡时，倾向 hold/watch 而非强行买卖」——该倾向与致命白名单外不得否决方向的新纪律冲突，2026-11-06 消融实证其参与构成恒 watch。)

fund_manager 提示词 MUST 含**方位采纳清单义务**（update-conditional-entry-gate-waiver）：`reasoning` 引用任何方位结论（研究经理评级、交易方案、激进/保守/中性方位、Risk Judge 裁决）时，MUST 逐项列明**采纳的具体主张**与**拒绝的具体主张**（拒绝项附理由），MUST NOT 仅复述总结论（如「采纳中性方框架」）；未逐项处置的采纳声明 MUST NOT 作为放行执行参数争议的依据（601066 案实证：声称采纳中性方框架但丢弃其入场压价与目标分档两项建议，选择性采纳无法从 reasoning 复盘）。

#### Scenario: Risk Judge 输出评级量表决策

- **WHEN** 加载 risk_judge 提示词
- **THEN** 模板中包含 buy/sell/hold/watch 的语义与取舍指导
- **AND** 模板中不包含「证据均衡时倾向 hold/watch」类方向裁决倾向表述

#### Scenario: Risk Judge 论据引用可标注风险层来源

- **WHEN** Risk Judge 采纳或校准了来自激进/保守/中性方风险辩论或风控指标的论据
- **THEN** 提示词 SHALL 允许 evidence_refs 的 source 取 `risk_aggressive`/`risk_conservative`/`risk_neutral`/`risk_metrics`（`RISK_EVIDENCE_SOURCES` ⊇ `TRADE_EVIDENCE_SOURCES`），且 SHALL 要求这类论据列入 evidence_refs 而非只出现在 reasoning 中
- **AND** trader 提示词的来源集保持 `TRADE_EVIDENCE_SOURCES`（Trader 在风险辩论之前，不得引用风险方）

#### Scenario: Fund Manager 审批决策语义

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含 approve/reject/return 三种决策的适用语义说明
- **AND** 模板中包含 approve 时输出 action（操作定性，枚举同交易方案）与 confidence（0-1）的格式要求及语义说明（action 是对最终方案的操作定性，非对裁决的赞成/否决票）

#### Scenario: Fund Manager 审批理由职责边界

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含 approve/return/reject 理由的职责边界条款（限定于风控结论一致性、论据矛盾处理、执行前提）
- **AND** 模板中包含禁止方向性投资判断的反例表述（如不得出现「适合长期价值投资/买入」类背书）

#### Scenario: Fund Manager 审批前核查条款

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含审批前核查条款：上下文出现完整性标注/估值完整性标注/辩论锚点告警时 reasoning MUST 显式回应，出现数据口径披露段时作为交叉核对基准
- **AND** 条款 MUST NOT 出现「禁止 approve」类表述（仲裁权保留）

#### Scenario: Fund Manager 方位采纳清单义务

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含方位采纳逐项清单要求（引用方位结论时 MUST 列明采纳与拒绝的具体主张，拒绝项附理由）
- **AND** 模板中说明仅复述总结论（如「采纳中性方框架」）不构成完整采纳声明
- **AND** 模板中说明未逐项处置的采纳声明 MUST NOT 作为放行执行参数争议的依据
