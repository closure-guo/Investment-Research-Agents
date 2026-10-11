# Delta for agent-prompt-contracts

## ADDED Requirements

### Requirement: 决策层周期语义契约

trader、risk_judge、fund_manager 提示词 MUST 携带同源的周期契约段（共享片段注入，三层字面一致，沿用 sell_type「终稿层与 Trader 同契约」先例，避免层间语义漂移）。契约段 MUST 声明：决策按 **T+20 个交易日相对沪深300 超额收益（±2% 中性带）** 考核，与 decision-outcome 判定口径一致；buy 决策 MUST 说明催化剂/兑现路径为何在 20 个交易日内兑现；兑现期超过 20 个交易日的长线论据（如估值回归、产能释放）MUST NOT 作为买入主因，仅可作安全垫或否决项。watch/hold/sell 各档位语义不变。契约段字面的「20」与配置项 `OUTCOME_DEFAULT_HORIZON_DAYS` 联动——配置变更时 MUST 同步更新契约段。

#### Scenario: 决策层三 prompt 携带同源周期契约段

- **WHEN** 加载 trader、risk_judge、fund_manager 提示词
- **THEN** 三个模板渲染后均包含同一周期契约段（契约测试断言片段同源）
- **AND** 契约段包含 T+20 考核语义（20 个交易日、相对沪深300、±2% 中性带）

#### Scenario: 买入决策的兑现路径约束

- **WHEN** 审查周期契约段文案
- **THEN** 包含「buy 决策须说明催化剂在 20 个交易日内兑现」的指令
- **AND** 包含「长线论据（>20 个交易日）不得作买入主因、仅作安全垫或否决项」的指令

#### Scenario: 周期字面值与配置联动声明

- **WHEN** 审查周期契约段或其注释说明
- **THEN** 存在「字面 20 与 OUTCOME_DEFAULT_HORIZON_DAYS 联动、配置变更须同步更新」的声明

### Requirement: 分析师论据周期标注

全部 claim（含 entity/event 型，舆情事件正是 short 档主力）的分析师 prompt——实现期核实覆盖范围为基础/技术/舆情/宏观四个分析师（即含 claim 登记章节的全部分析师；辩论者 prompt 无 claim 登记章节，不在范围内）——SHALL 要求登记 claim 时申报 `horizon_hint` 字段，三档取值 `short`（预期 ≤5 个交易日兑现）/ `mid`（6–20 个交易日）/ `long`（>20 个交易日），语义为「该论据的预期兑现窗口」；prompt SHALL 提供与 direction 同级的申报示例。无法判断或不确定时 horizon_hint 置 null——不判 FAIL，严禁编造档位。`citation.py` 的 Claim 模型 SHALL 增加同名可选字段以保留申报值；校验器 SHALL NOT 消费 horizon_hint：不校验、不为其新增 FAIL 桶、不计入 coverage_gap（coverage_gap 是术语/期次/方向未申报的既有 bool 语义，把 horizon_hint 并入会把全部存量 claim 翻成缺口——未申报率由 eval 侧统计）。prompt 变更后 SHALL 经 `scripts/deploy_prompts.py` 发布方可进入 eval 门禁（prompt-deploy-consistency 契约不变）。

#### Scenario: prompt 含 horizon_hint 申报说明与示例

- **WHEN** 审查四个分析师 prompt 任一的 claim 登记章节
- **THEN** 存在 horizon_hint 三档定义（short/mid/long 及对应交易日区间）、null 降级说明（不判 FAIL）与申报示例

#### Scenario: Claim 模型保留 horizon_hint 申报值

- **WHEN** 解析含 `"horizon_hint": "mid"` 的 claim JSON
- **THEN** Claim 模型保留该字段值且解析成功
- **AND** claim JSON 缺失该字段或为 null 时解析同样成功（向后兼容存量格式）

#### Scenario: horizon_hint 不参与校验裁决

- **WHEN** 校验一条 horizon_hint 为 null 或任意档位的 claim
- **THEN** 校验流程不读取该字段，status / coverage_gap / bucket 与该字段不存在时完全一致（无新 FAIL 桶、coverage_gap 语义不变）
- **AND** 未申报率由 eval 侧从 claims 统计，校验器不承担
