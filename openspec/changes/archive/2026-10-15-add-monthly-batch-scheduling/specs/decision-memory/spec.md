# decision-memory delta: add-monthly-batch-scheduling

## ADDED Requirements

### Requirement: 月批提炼排程

系统 SHALL 将语义记忆候选条款提炼注册为统一日批通道的**月批任务**（每月 1 日 09:00 Asia/Shanghai），以已结算决策为源、按近窗口提炼候选条款并落**候选态**；提炼产物 SHALL 经人工确认后方可注入（未确认不注入的既有契约不变）；任务 SHALL 提供运行历史（候选数/来源窗口/LLM 或规则兜底路径）与手动补跑；无可用已结算决策时 SHALL 记录 `candidates=0` 的空摘要，MUST NOT 伪造候选。

#### Scenario: 月批提炼落候选态

- **GIVEN** 近窗口存在 5 条已结算决策
- **WHEN** 月批任务运行
- **THEN** 提炼产物以候选态落库（`/memory` 待确认列表可见）
- **AND** 注入集合不变（未确认不生效）

#### Scenario: 无素材如实空摘要

- **GIVEN** 近窗口无已结算决策
- **WHEN** 月批任务运行
- **THEN** 运行历史记成功且摘要 `candidates=0`
- **AND** MUST NOT 以编造条款冒充提炼产物

#### Scenario: 手动补跑

- **WHEN** 运维经统一通道手动补跑该任务
- **THEN** 以 `source=manual` 落新运行行并执行同一提炼编排
