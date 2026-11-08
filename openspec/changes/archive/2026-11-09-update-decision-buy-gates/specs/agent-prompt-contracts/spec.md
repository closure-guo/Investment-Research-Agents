# Delta for agent-prompt-contracts

## MODIFIED Requirements

### Requirement: 决策层周期语义契约

trader、risk_judge、fund_manager 提示词 MUST 携带同源的周期契约段（共享片段注入，三层字面一致，沿用 sell_type「终稿层与 Trader 同契约」先例，避免层间语义漂移）。契约段 MUST 声明决策按 **T+20 个交易日相对沪深300 超额收益（±2% 中性带）** 结算考核、与 decision-outcome 判定口径一致，且该窗口是**结算口径而非决策门槛**：buy 决策 MUST NOT 被要求证明「20 个交易日内存在催化剂/兑现路径」；兑现期超过考核窗口的长线论据（如估值回归、产能释放）SHALL 可作为买入依据，reasoning SHALL 披露论据兑现窗口与考核窗口的错配风险；催化剂缺失本身 MUST NOT 作为不买理由。watch/hold/sell 各档位语义不变。契约段字面的「20」与配置项 `OUTCOME_DEFAULT_HORIZON_DAYS` 联动——配置变更时 MUST 同步更新契约段。
(Previously: 契约段要求 buy 决策说明催化剂为何能在 20 个交易日内兑现、兑现期超过 20 个交易日的长线论据 MUST NOT 作为买入主因（仅可作安全垫或否决项）——两条款在财报真空期构成结构性禁止买入，2026-11-06 消融实证生产臂因此恒 watch。)

#### Scenario: 决策层三 prompt 携带同源周期契约段

- **WHEN** 加载 trader、risk_judge、fund_manager 提示词
- **THEN** 三个模板渲染后均包含同一周期契约段（契约测试断言片段同源）
- **AND** 契约段包含 T+20 结算口径语义（20 个交易日、相对沪深300、±2% 中性带）

#### Scenario: 买入决策的兑现路径约束

- **WHEN** 审查周期契约段文案
- **THEN** 不包含「buy 决策须说明催化剂在 20 个交易日内兑现」的指令
- **AND** 不包含「长线论据（>20 个交易日）不得作买入主因」的指令
- **AND** 包含「长线论据可作买入依据、须披露窗口错配风险」的指令

#### Scenario: 周期字面值与配置联动声明

- **WHEN** 审查周期契约段或其注释说明
- **THEN** 存在「字面 20 与 OUTCOME_DEFAULT_HORIZON_DAYS 联动、配置变更须同步更新」的声明

### Requirement: 风控与审批评级量表

risk_judge、fund_manager 提示词 MUST 提供评级/决策选项的语义说明，指导在证据均衡或不足时如何取舍——证据均衡（多空对峙）只要求 reasoning 如实披露对峙点，MUST NOT 表述为「倾向 hold/watch」的方向裁决规则（方向裁决纪律见「决策层否决纪律与参数协商」）。fund_manager 提示词 MUST 额外定义审批理由的职责边界：approve 理由限定于风控结论一致性、论据矛盾处理与执行前提；MUST NOT 对标的作方向性投资判断（如「适合长期价值投资」——该类判断属研究层，FM 无分析师报告输入而无依据）。

fund_manager 提示词 MUST 含**审批前核查**条款（条件式表述——上下文出现对应段时才生效）：上下文含「终稿完整性标注」「估值完整性标注」「辩论锚点告警」任一段时，`reasoning` MUST 显式回应出现的每一段（为何放行、或作为 reject/return 的依据），MUST NOT 对出现的标注/告警保持沉默；上下文含「数据口径披露」段时，SHALL 将其作为决策数字与管线确定性计算的交叉核对基准，发现口径冲突时 MUST 在 reasoning 中说明。审批前核查条款 MUST NOT 禁止 approve（仲裁权保留）。
(Previously: risk_judge 评级量表含「当多空/风险论据证据均衡时，倾向 hold/watch 而非强行买卖」——该倾向与致命白名单外不得否决方向的新纪律冲突，2026-11-06 消融实证其参与构成恒 watch。)

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

### Requirement: 决策层 confidence 分档判据

决策层三个节点（trader / risk_judge / fund_manager）的 prompt SHALL 给出可执行的 confidence 分档判据与三档示例（高/中/低），MUST NOT 只给「≥0.7 高、0.4–0.7 中、<0.4 低」的区间描述而示例值全部落在中段。中等档示例 MUST NOT 使用单一中段数值（0.5–0.6 区间单值，如 0.55/0.6）——SHALL 改用区间化表述（如「0.45–0.65 按证据组合取值」）并附**反锚定纪律**：输出 MUST NOT 向示例值收敛，同一证据组合在不同标的上应产生不同置信度，reasoning MUST 引用支撑所选档位的具体证据组合。
(Previously: 三档示例为 0.85/0.55/0.30——中档单一示例值 0.55 与生产 confidence 扎堆 0.50–0.60 强相关（metrics.md 待决策 #4，2026-11-06 消融三轮复现）。)

#### Scenario: 高置信必须有证据组合

- **GIVEN** 决策证据包含多源一致的数字锚点、且风险辩论中的主要矛盾已处理
- **WHEN** 节点输出 confidence
- **THEN** confidence SHALL ≥ 0.70
- **AND** reasoning SHALL 引用支撑该强度的具体证据（锚点或证据条数）

#### Scenario: 低置信必须有明确触发

- **GIVEN** 关键数据缺失、或风险辩论存在未决矛盾
- **WHEN** 节点输出 confidence
- **THEN** confidence SHALL < 0.40，并在 reasoning 说明缺失/矛盾项

#### Scenario: 中等档示例反锚定

- **WHEN** 审查决策层三 prompt 的 confidence 分档判据段
- **THEN** 中等档不含单一中段数值示例（0.5–0.6 区间单值）
- **AND** 包含「MUST NOT 向示例值收敛 / 按证据组合取值」的反锚定纪律表述

#### Scenario: 校准观测进实验收口

- **GIVEN** 一次 hosted 全量实验完成
- **WHEN** 收口报告生成
- **THEN** SHALL 登记 confidence 分布（按 action 分桶的直方图与 ≥0.70 / <0.40 尾部占比）
- **AND** 该观测 MUST NOT 以新增 Langfuse Score 的形式进入 judge 口径

### Requirement: 分析师论据周期标注

全部 claim（含 entity/event 型，舆情事件正是 short 档主力）的分析师 prompt——实现期核实覆盖范围为基础/技术/舆情/宏观四个分析师（即含 claim 登记章节的全部分析师；辩论者 prompt 无 claim 登记章节，不在范围内）——SHALL 要求登记 claim 时申报 `horizon_hint` 字段，三档取值 `short`（预期 ≤5 个交易日兑现）/ `mid`（6–20 个交易日）/ `long`（>20 个交易日），语义为「该论据的预期兑现窗口」；prompt SHALL 提供与 direction 同级的申报示例。无法判断或不确定时 horizon_hint 置 null——不判 FAIL，严禁编造档位。`citation.py` 的 Claim 模型 SHALL 增加同名可选字段以保留申报值；校验器 SHALL NOT 消费 horizon_hint：不校验、不为其新增 FAIL 桶、不计入 coverage_gap（coverage_gap 是术语/期次/方向未申报的既有 bool 语义，把 horizon_hint 并入会把全部存量 claim 翻成缺口——未申报率由 eval 侧统计）。prompt 变更后 SHALL 经 `scripts/deploy_prompts.py` 发布方可进入 eval 门禁（prompt-deploy-consistency 契约不变）。

分析师 prompt MUST NOT 在 horizon_hint 申报说明中附带决策层判语（如「决策层按 T+20 个交易日考核，long 档论据不能支撑买入主因」）——论据消费语义由决策层契约约束，原料层判语会在论据进入决策前先行压制（2026-11-06 消融实证：仅删除该括注即使 RM 评级由中性转看多）。
(Previously: 四分析师 horizon_hint 说明括注「决策层按 T+20 个交易日考核，long 档论据不能支撑买入主因」，与决策层周期语义契约旧条款联动构成全链锁。)

#### Scenario: prompt 含 horizon_hint 申报说明与示例

- **WHEN** 审查四个分析师 prompt 任一的 claim 登记章节
- **THEN** 存在 horizon_hint 三档定义（short/mid/long 及对应交易日区间）、null 降级说明（不判 FAIL）与申报示例
- **AND** 不包含「不能支撑买入主因」类决策判语

#### Scenario: Claim 模型保留 horizon_hint 申报值

- **WHEN** 解析含 `"horizon_hint": "mid"` 的 claim JSON
- **THEN** Claim 模型保留该字段值且解析成功
- **AND** claim JSON 缺失该字段或为 null 时解析同样成功（向后兼容存量格式）

#### Scenario: horizon_hint 不参与校验裁决

- **WHEN** 校验一条 horizon_hint 为 null 或任意档位的 claim
- **THEN** 校验流程不读取该字段，status / coverage_gap / bucket 与该字段不存在时完全一致（无新 FAIL 桶、coverage_gap 语义不变）
- **AND** 未申报率由 eval 侧从 claims 统计，校验器不承担

## ADDED Requirements

### Requirement: 决策层否决纪律与参数协商

trader 提示词 MUST 含**止损锚定与仓位预算**规则：buy/short 型方案的 stop_loss MUST 取「max(2×日波动, 最近结构位)」——日波动以代码派生指标为准（context 含代码算出的止损距离与赔率时直接引用）；止损参考带上沿若窄于 1.5×日波动，MUST 下移止损至 ≥2×日波动或最近结构位，MUST NOT 在参考带内无视噪声自由选边；position_size MUST 与止损距离预算联动（止损越窄仓位越轻），reasoning SHALL 给出「止损距离 → 仓位档位」的预算依据。方向非看空且基本面/估值/趋势条件成立时，trader MUST 产出可执行价位方案（entry/stop/target 三件套 + 仓位档位）；「无催化剂」「止损窄」「数据部分缺失」MUST NOT 直接导向 watch——分别按周期契约披露窗口错配、按止损锚定规则放宽、按置信度分档降档处理；仅当条件确实不满足时才输出 watch，且 inaction_reason MUST 指名具体未满足条件。

risk_judge 提示词 MUST 含**否决纪律**：方向否决（把 buy/sell 改为 hold/watch）仅限致命白名单——财务造假嫌疑、退市/面值退市风险、流动性枯竭无法成交、受众无法交易、关键数据被判定不可信；MUST NOT 以「止损窄于噪声」「赔率不足」「数据部分缺失」「无催化剂」作为方向否决理由。执行参数缺陷 MUST 二选一：①产出**联合修正方案**——放宽止损至 ≥2×日波动或最近结构位，并等比下调仓位档位（单笔风险预算不变；价位继承规则允许调整数值，修正后价位 MUST 落在价位参考带 sanity 范围内）；②维持方向并显式申报**可执行参数缺口**（缺口内容 = 满足可执行所需的止损宽度与仓位档位）。MUST NOT 既不修正也不申报缺口就输出 hold/watch。

#### Scenario: trader 止损锚定规则入契约

- **WHEN** 加载 trader 提示词
- **THEN** 模板包含「止损 = max(2×日波动, 最近结构位)」锚定规则与「参考带内不得无视噪声自由选边」的禁止表述
- **AND** 模板包含 position_size 与止损距离的预算联动表述

#### Scenario: risk_judge 禁止参数型方向否决

- **WHEN** 加载 risk_judge 提示词
- **THEN** 模板包含致命白名单枚举（造假/退市/流动性枯竭/无法交易/关键数据不可信）
- **AND** 模板包含「放宽止损 × 等比缩仓」联合修正与「可执行参数缺口」显式申报的二选一义务
- **AND** 模板包含禁止以止损宽度/赔率/数据缺失/催化剂缺失否决方向的表述

#### Scenario: 参数缺陷走修正而非观望

- **GIVEN** trader 方案方向为 buy、止损距离窄于 1.5×日波动
- **WHEN** risk_judge 裁决
- **THEN** 输出为 buy（止损放宽 + 仓位等比下调）或 buy + 显式可执行参数缺口
- **AND** 仅当放宽后止损仍超出风险预算或仓位低于 light 档下限时，才可输出 watch 且 inaction_reason 指名缺口
