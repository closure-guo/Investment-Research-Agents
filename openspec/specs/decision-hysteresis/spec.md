# decision-hysteresis Specification

## Purpose
TBD - created by archiving change add-decision-hysteresis. Update Purpose after archive.
## Requirements
### Requirement: 证据均衡带识别与默认观望

系统 SHALL 在决策层（risk_judge 前置）识别「证据均衡带」：Research Manager 评级为中性，或多空辩论两轮后关键论据呈对峙态势且无新增量事实（判据由 RM 结构化输出与辩论锚点统计确定性计算）。均衡带成立时，决策上下文 SHALL 显式携带均衡带标志，均衡带标志的效力收窄为**要求 risk_judge 的 reasoning 如实披露对峙点**——MUST NOT 对执行动作（buy/sell）施加增量申报义务：均衡带内的首次建仓（近窗无历史决策）与维持近窗前向方向的执行动作 SHALL 以价位逻辑与既有证据为充分依据放行，正常进入后续门禁。增量申报与打回降级机制仅适用于**方向翻转**（与近窗前向方向不同的执行动作），归「方向滞回与增量事实申报」约束。`decision_hysteresis` state 键（balanced_zone 等判定要素）保留供 trace 观测。
(Previously: 均衡带内 risk_judge 的执行动作（buy/sell）SHALL 以证据增量支撑为前提——无增量引用的执行动作被打回重申一次，重申仍无增量降级为 watch/hold 并标注「证据均衡无增量，默认观望」——该机制对首次建仓构成「无新披露即不得买」，财报真空期与首建场景结构性禁止买入，2026-11-06 消融实证生产臂 RM 中性时因此恒 watch。旧场景名「均衡带内 sell 无增量被降级」保留为归档漂移守卫的连续命名锚点，行为以本 requirement 修订为准。)

#### Scenario: 均衡带内 sell 无增量被降级

- **GIVEN** RM 评级中性、辩论锚点统计多空对峙（均衡带成立），近窗无该标的历史决策或近窗前向方向与 sell 一致，risk_judge 输出 sell 且 reasoning 披露了对峙点但未申报新增量事实
- **WHEN** 均衡带复核执行
- **THEN** 执行动作 SHALL 放行，不触发打回降级
- **AND** 「无增量即降级」机制自本 requirement 修订（update-decision-buy-gates，2026-11-07）起移除：均衡带标志的效力收窄为要求 reasoning 如实披露对峙点，打回降级仅适用于方向翻转

#### Scenario: 均衡带内 sell 携带真增量放行

- **GIVEN** 均衡带成立，risk_judge 输出 sell 且 reasoning 引用「今日放量跌破 MA60 收盘确认」类技术形态破位增量
- **THEN** 执行动作 SHALL 放行（增量事实显式申报），正常进入后续门禁

#### Scenario: 非均衡带不受影响

- **GIVEN** RM 评级明确看多或看空（非中性）且辩论一方锚点占优
- **THEN** 均衡带约束 SHALL NOT 生效，决策路径与现状一致

#### Scenario: 均衡带内首次建仓无增量放行

- **GIVEN** RM 评级中性、辩论锚点统计多空对峙（均衡带成立），近窗无该标的历史决策（首次建仓）
- **WHEN** risk_judge 输出 buy 且 reasoning 披露了对峙点但未申报新增量事实
- **THEN** 执行动作 SHALL 放行，不触发打回，正常进入后续门禁

#### Scenario: 均衡带内方向翻转仍受滞回约束

- **GIVEN** 均衡带成立，近窗前向方向为 watch
- **WHEN** risk_judge 输出 buy 且 reasoning 未申报触发翻转的增量事实
- **THEN** 按「方向滞回与增量事实申报」处理：打回重申一次，重申仍无增量维持前向方向

#### Scenario: 均衡带标志仍落 state

- **WHEN** 均衡带判定执行
- **THEN** `decision_hysteresis` state 键保留 balanced_zone 等判定要素（trace 观测语义不变）
- **AND** 均衡带成立时 risk_judge 上下文仍携带均衡带标志（供 reasoning 披露对峙点）

### Requirement: 方向滞回与增量事实申报

同标的存在窗口期内（默认 5 个交易日，可配置）的已批准历史决策时，系统 SHALL 从 decision-outcome predictions 库读取近窗方向序列注入决策上下文（方向、日期、置信度），risk_judge 翻转方向（watch/hold ↔ 执行动作，或 buy ↔ sell）时 reasoning MUST 显式申报触发翻转的增量事实；未申报增量的翻转输出 SHALL 被打回重申一次，重申仍无 → 输出维持前向方向（窗口内最近一次已批准方向）并如实标注「维持前判（无证据增量）」。无历史决策（新标的/窗口外）时滞回 SHALL NOT 生效。滞回判定要素（前向方向/窗口历史/增量申报）SHALL 落 state 键 `decision_hysteresis` 供 trace 观测，报告渲染不新增标注行。

滞回判定 SHALL 以共享纯函数实现并被**落库决策路径与信号生成路径**共同调用（禁止两处各写一套判定）；信号生成路径中，方向翻转 SHALL 经过与决策方向翻转相同的滞回判定（吸收 LLM 采样抖动），判定被应用时 SHALL 落库审计记录（含原始方向、维持/降级结果与来源）。

(Previously: 仅规定决策路径的滞回判定（注入上下文 + 打回重申 + 维持前判）；信号生成路径无滞回约束，采样抖动可直接翻转信号方向。)

#### Scenario: 无增量翻转被滞回维持

- **GIVEN** 688072 窗口内最近已批准方向为 watch，本次 risk_judge 输出 sell 且 reasoning 未申报增量事实
- **WHEN** 滞回复核执行（打回重申一次后仍无申报）
- **THEN** 最终输出 SHALL 为 watch，标注「维持前判（无证据增量）」落 state；reasoning 如实保留
- **AND** pass@k 型采样翻转（同输入多跑分裂）因无增量事实 SHALL 被滞回吸收

#### Scenario: 携带增量的翻转放行

- **GIVEN** 窗口内最近方向为 watch，本次输出 sell 且 reasoning 申报「三季报增速回落至个位数」增量
- **THEN** 翻转 SHALL 放行（增量显式申报），正常进入后续门禁与审批

#### Scenario: 窗口外或无历史不生效

- **GIVEN** 标的首次分析，或窗口内无已批准决策
- **THEN** 滞回 SHALL NOT 生效，决策路径与现状一致

#### Scenario: 多空直接翻转从严

- **GIVEN** 窗口内最近方向为 buy，本次输出 sell
- **THEN** 多空直接翻转 SHALL 视同翻转申报要求（增量事实必需），历史数据显示该形态零发生——约束生效面极小但语义必须完备

#### Scenario: 抖动信号被吸收

- **GIVEN** 标的 M 前一日信号为 long，今日裸输出翻转为 flat 但证据处于均衡带内
- **WHEN** 信号生成执行
- **THEN** 今日信号维持 long，滞回判定记录落库

