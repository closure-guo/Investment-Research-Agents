# trade-execution Specification

## Purpose
TBD - created by archiving change add-signal-portfolio-risk-gate. Update Purpose after archive.
## Requirements
### Requirement: 标准化 Signal 契约

系统 SHALL 将审批通过的 TradeDecision 转换为 Signal 落库，字段含 `signal_id/symbol/direction/strength/horizon_days/source/decision_id/created_at`；`direction` 限枚举 `long/flat`；缺字段或非法枚举 SHALL 拒收。

#### Scenario: 非法 direction 拒收

- **GIVEN** 一条 direction 为 `short` 的转换输入
- **WHEN** 执行 Signal 转换
- **THEN** 转换失败并抛出合同错误，消息含「direction 非法值: short」

### Requirement: 组合仓位聚合

系统 SHALL 将同一交易日的 active Signal 聚合为目标持仓，支持等权与 strength 加权两档（可配置）；单票目标权重 SHALL 不超过单票上限（默认 20%），组合总仓位 SHALL 不超过总上限（默认 80%），超限部分 SHALL 按比例缩减并记录调整原因。

#### Scenario: 总仓位超限缩减

- **GIVEN** 6 条 long 信号按强度加权后合计目标仓位 96%
- **WHEN** 组合聚合执行
- **THEN** 输出合计 80%，各票等比例缩减，且调整记录含「TOTAL_CAP_SCALE_DOWN」

### Requirement: 确定性风控门禁

所有订单 SHALL 通过 `risk_gate.py` 纯代码门禁方可进入执行：检查项含单票仓位上限、组合总仓位上限、T+1、涨跌停/停牌/ST 状态（约束定义 import 自 `screening/constraints.py`）、100 股整数倍；门禁拒绝 SHALL 返回结构化原因码；门禁模块 SHALL NOT 发起或依赖任何 LLM 调用。

#### Scenario: 门禁无 LLM 依赖

- **GIVEN** 实现完成后的代码库
- **WHEN** 对 `execution/risk_gate.py` 做静态扫描
- **THEN** 无任何 LLM gateway/harness 的 import

#### Scenario: ST 状态门禁拒绝

- **GIVEN** 目标持仓调整后需买入标的 X，X 当日状态为 ST
- **WHEN** 订单进入风控门禁
- **THEN** 订单被拒绝，原因码 `ST_FLAG`，与 L1 过滤使用同一约束常量

