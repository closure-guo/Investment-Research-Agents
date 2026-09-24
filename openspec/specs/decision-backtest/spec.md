# decision-backtest Specification

## Purpose
TBD - created by archiving change harden-evaluation-rigor. Update Purpose after archive.
## Requirements
### Requirement: 历史离线回放

系统 SHALL 提供离线回放器：以指定历史日期为决策日，管线数据输入截断至该日之前的可得数据（含财报披露滞后近似），固定 prompt 版本与模型版本，产出当日 TradeDecision 并**按与生产 track-record 判定规则同源的实现结算收益**（horizon / 区间超额 / ±2% 中性带语义，SHALL NOT 保留第二套结算引擎）。回放器 SHALL 记录每次回放的"数据快照时点"，使前视偏差可审计。

产出 skill 结论的批次 SHALL 通过**干净窗口判定**：① 决策日距跑批日 ≥ 主评估窗口的交易日数（全部样本可完成结算）；② 泄漏探针读数随报告披露（见「知识泄漏探针」需求）。未通过干净窗口判定的批次 SHALL 在报告标注「通路验证」定位，SHALL NOT 产出 skill / 赚钱能力结论句。
(Previously: 结算表述为「按持仓语义结算收益」且实现走旧 settle.py 止损/目标/超期引擎；无干净窗口判定，深历史批次与可下结论批次无区分。)

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

### Requirement: 绩效指标与基线对比

回放评估 SHALL 输出绩效四指标：累计收益（CR）、年化收益（ARR）、夏普比率（Sharpe）、最大回撤（MDD），并与基线策略对比：Buy-and-Hold、MACD、KDJ、RSI 规则策略（复用 `metrics/technical.py` 既有实现）。结算语义（涨跌停递延、停牌顺延、**后复权**、方向符号化）SHALL 复用 `decision-outcome` 的 A 股异常结算规则，SHALL NOT 另造一套；区间收益计算的日 K SHALL 采用**后复权（adj）口径**（历史价格 = 决策时点真实可见价格，保障 as-of 保真），与 `decision-outcome`「复权处理」条款一致。
(Previously: 结算语义括注与 Scenario 均写「前复权日 K」——与 decision-outcome 主规范的后复权条款互相矛盾，且前复权追溯重基破坏 as-of 保真。)

#### Scenario: 四指标对比报告

- **WHEN** 一批回放完成
- **THEN** 报告 SHALL 给出系统与各基线的 CR/ARR/Sharpe/MDD 对照表

#### Scenario: 结算语义一致

- **WHEN** 回放结算遇到涨跌停、停牌、分红除权
- **THEN** 处理规则 SHALL 与 `decision-outcome` 的在线结算规则一致（一字板递延、停牌期不计入持仓、**后复权日 K**）

#### Scenario: 复权口径核对

- **WHEN** 结算或回测拉取日 K 计算区间收益
- **THEN** 取数路径 SHALL 使用后复权序列
- **AND** 生产结算链路的实际复权口径 SHALL 与本条一致（不一致属存量漂移，随本变更修正并登记切点）

### Requirement: 分层市场状态抽样

回放样本 SHALL 按市场状态分层：至少覆盖单边上涨、单边下跌、震荡三种 regime，且至少含一段下跌市；每种 regime SHALL 抽样 ≥10 只标的。SHALL NOT 仅在单边行情样本上汇报绩效结论。**当干净窗口约束与 regime 覆盖冲突时，SHALL 以干净窗口优先**：报告 SHALL 披露本批实际覆盖的 regime 范围，结论 SHALL 限定于已覆盖 regime，SHALL NOT 外推至未覆盖 regime；跨 regime 完整拼图由 forward 腿随日历积累补足。
(Previously: 无干净窗口冲突条款——三 regime 全覆盖要求与近端窗口回测在结构上不可同时满足。)

#### Scenario: 分层覆盖

- **WHEN** 构建回放样本池
- **THEN** 样本 SHALL 标注所属 regime，三种 regime 均有 ≥10 只标的
- **AND** 报告 SHALL 分 regime 展示绩效，SHALL NOT 只给全池汇总

#### Scenario: 干净窗口下的覆盖限定

- **GIVEN** 某干净窗口批次仅覆盖震荡 regime
- **WHEN** 生成报告
- **THEN** 报告 SHALL 披露「仅覆盖震荡市」
- **AND** 结论句 SHALL 限定于震荡市，SHALL NOT 出现全 regime 赚钱能力表述

### Requirement: 统计显著性与不确定性报告

绩效对比 SHALL 使用 block bootstrap（B≥1,000，按交易日块重采样，默认块长 20 交易日）报告 Sharpe 与超额收益的置信区间，并附块长敏感性说明。SHALL NOT 只报告点估计。Sharpe > 3 的批次 SHALL 附 sanity check 说明（样本期、回撤构成、换手假设），否则该批次结果标记为无效。

#### Scenario: 置信区间报告

- **WHEN** 生成绩效报告
- **THEN** 系统相对最佳基线的 Sharpe 差 SHALL 带 95% CI
- **AND** CI 含 0 时结论 SHALL 为"无显著差异"

#### Scenario: 异常夏普拦截

- **WHEN** 某批次 Sharpe > 3
- **THEN** 报告 SHALL 强制附 sanity check 段落；缺失时该批次标记 `invalid`，不得用于任何对比结论

### Requirement: 决策一致性测量

同一标的在同一决策日 SHALL 重复回放 n=3 次，报告决策方向一致率（3 次 action 同向的比例）。一致性 SHALL 作为决策质量的独立维度披露，SHALL NOT 被绩效指标掩盖——高绩效低一致性提示结果不可复现。

#### Scenario: 一致率报告

- **WHEN** 回放批次完成
- **THEN** 报告 SHALL 含各标的方向一致率与全池均值
- **AND** 一致率 < 2/3 的标的 SHALL 在绩效汇总中剔除或单独标注

### Requirement: 知识泄漏探针

正式回测批开跑前，系统 SHALL 对候选窗口的抽样标的（每批 ≥10 只 × 3–5 问）执行记忆探测：在**无 as-of 快照输入**的裸问条件下，询问模型决策日后主窗口内的实际涨跌方向（主指标题）、幅度桶（±2% 带同源分桶）与重大事件（辅证题），对照 akshare 后复权真值与既有新闻源计算**记忆命中率**。探针读数 SHALL 随批次报告披露；命中率超过预登记阈值（默认 60%，owner 可调）时，该批结论 SHALL 降级为「泄漏污染下的上界证据（真实 skill ≤ 读数）」句式，SHALL NOT 单独作为赚钱能力主张。探针失败（拒答/不可解析/事件真值不可得）SHALL 计入未知占比并披露，SHALL NOT 按答错或答对静默处理。

#### Scenario: 探针执行与披露

- **WHEN** 一个正式回测批完成
- **THEN** 报告 SHALL 含探针段：抽样标的数 / 题目构成 / 记忆命中率 / 未知占比
- **AND** 命中率主指标 SHALL 基于方向题

#### Scenario: 超阈降级句式

- **GIVEN** 某批探针记忆命中率超过预登记阈值
- **WHEN** 撰写批次结论
- **THEN** 结论 SHALL 采用「泄漏污染下的上界证据」句式
- **AND** SHALL NOT 出现无条件的赚钱能力主张

#### Scenario: 探针失败不静默

- **WHEN** 部分探针题目拒答、不可解析或事件真值不可得
- **THEN** 该部分 SHALL 计入未知占比随报告披露
- **AND** SHALL NOT 被折算为答对或答错

### Requirement: 回测批次预登记与报告生命周期

正式回测批（非通路验证）SHALL 持有效预登记（沿用 `evals/ablation/preregister/` 的 outcome 门禁字段 `OUTCOME_REQUIRED_FIELDS`：主指标 / MDE / 决策阈值 / 样本量依据 / 停止规则 / 成本分型 / 泄漏控制；决策窗口与探针阈值在「泄漏控制」字段内声明），无预登记 SHALL NOT 开跑正式批。批次报告 SHALL 落 `evals/backtest/results/` 并带生命周期 status 头（`active` 或 `superseded-by: <路径>`，复用 `causal_ablation/report_status.py` 校验，指针须可解析），并纳入 `docs/evals/README.md` 索引渲染。深历史批次（决策日早于干净窗口可及范围）SHALL 永久标注「通路验证 + 泄漏风险」定位；存量报告（pilot-2023-shock.md）SHALL 就地补 status 头与定位标注，原文与数字保留。

#### Scenario: 无预登记拒跑正式批

- **GIVEN** 一个未持有效预登记的回测批请求以正式批身份开跑
- **WHEN** 跑批入口校验
- **THEN** SHALL 拒绝正式批身份（可降级为通路验证批执行）
- **AND** 校验失败原因 SHALL 可查

#### Scenario: 报告 status 头校验

- **WHEN** 回测批次报告落盘
- **THEN** 报告头 SHALL 含可解析的生命周期字段
- **AND** 缺失或指针不可解析 SHALL 校验失败（与结论注册表同款测试护栏）

#### Scenario: 深历史批次定位标注

- **GIVEN** 某批次决策日早于干净窗口可及范围（如 2023 年样本）
- **WHEN** 生成或更新其报告
- **THEN** 报告 SHALL 标注「通路验证 + 泄漏风险」定位
- **AND** 存量 pilot-2023-shock.md SHALL 就地补标注，原文与数字 SHALL 保留不改写

