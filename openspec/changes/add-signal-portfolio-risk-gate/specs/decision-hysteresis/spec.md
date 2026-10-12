# decision-hysteresis delta: add-signal-portfolio-risk-gate

## MODIFIED Requirements

### Requirement: 滞回机制适用路径

决策滞回（证据均衡带 + 方向滞回）SHALL 同时作用于落库决策与信号生成路径：信号方向翻转 SHALL 经过与决策方向翻转相同的滞回判定，吸收 LLM 采样抖动。

#### Scenario: 抖动信号被吸收

- **GIVEN** 标的 M 前一日信号为 long，今日裸输出翻转为 flat 但证据处于均衡带内
- **WHEN** 信号生成执行
- **THEN** 今日信号维持 long，滞回判定记录落库
