# decision-backtest delta: add-matching-engine

## MODIFIED Requirements

### Requirement: 历史离线回放

系统 SHALL 提供离线回放器：以指定历史日期为决策日，管线数据输入截断至该日之前的可得数据（含财报披露滞后近似），固定 prompt 版本与模型版本，产出当日 TradeDecision 并**按与生产 track-record 判定规则同源的实现结算收益**（horizon / 区间超额 / ±2% 中性带语义，SHALL NOT 保留第二套结算引擎）。回放器 SHALL 记录每次回放的"数据快照时点"，使前视偏差可审计。

回放入场成交 SHALL 由 `matching-engine` 的撮合函数在决策日**次一交易日**行情上判定（成交价含分档滑点；一字涨停买单 / 一字跌停卖单等不可成交情形记结构化原因码，且该笔不计入收益统计），替换原有「按决策日收盘价即视为成交」的简化假设；出场侧结算语义（涨跌停递延 / 停牌顺延 / 后复权 / 方向符号化）SHALL 保持不变，仍复用 `decision-outcome` 的 A 股异常结算规则。

产出 skill 结论的批次 SHALL 通过**干净窗口判定**：① 决策日距跑批日 ≥ 主评估窗口的交易日数（全部样本可完成结算）；② 泄漏探针读数随报告披露（见「知识泄漏探针」需求）。未通过干净窗口判定的批次 SHALL 在报告标注「通路验证」定位，SHALL NOT 产出 skill / 赚钱能力结论句。
(Previously: 仅规定回放结算与生产同源 + 干净窗口判定；成交判定无显式契约——实现为「决策日收盘价成交」，无涨跌停/滑点约束，执行损耗不可度量。)

#### Scenario: 时点截断

- **GIVEN** 回放决策日为 T
- **WHEN** 回放器构建管线输入
- **THEN** 所有行情、财报、宏观、新闻数据 SHALL 只含 T 日收盘前可得部分
- **AND** 财报数据 SHALL 按披露日（而非报告期）判断可得性

#### Scenario: 可审计快照

- **WHEN** 一次回放完成
- **THEN** 结果 SHALL 携带数据快照时点、prompt 版本、模型版本标识，支持事后审计前视偏差

#### Scenario: 结算同源

- **WHEN** 回放结算一条决策
- **THEN** 判定语义 SHALL 与生产 track-record 判定规则同源（同一实现或同一契约的离线调用）
- **AND** SHALL NOT 存在仅回测使用的止损/目标/超期第二引擎

#### Scenario: 干净窗口校验

- **GIVEN** 某批次的决策日距跑批日不足主评估窗口交易日数，或探针读数缺失
- **WHEN** 生成批次报告
- **THEN** 报告 SHALL 标注「通路验证」定位
- **AND** SHALL NOT 含 skill / 赚钱能力结论句

#### Scenario: 回放经过涨跌停约束

- **GIVEN** 一条历史买入决策，其次日为一字涨停
- **WHEN** 执行决策回放
- **THEN** 该笔记为未成交，原因码 `LIMIT_UP_UNFILLABLE`
- **AND** 该笔不计入收益统计（而非按涨停价记成交）
