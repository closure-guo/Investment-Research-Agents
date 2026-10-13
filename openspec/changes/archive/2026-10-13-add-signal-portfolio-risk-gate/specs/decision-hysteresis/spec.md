# decision-hysteresis delta: add-signal-portfolio-risk-gate

## MODIFIED Requirements

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
