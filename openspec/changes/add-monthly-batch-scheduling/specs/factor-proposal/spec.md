# factor-proposal delta: add-monthly-batch-scheduling

## ADDED Requirements

### Requirement: 提案月批排程

系统 SHALL 将因子提案注册为统一日批通道的**月批任务**（每月 1 日 10:00 Asia/Shanghai）：按跨桶采样运行 LLM 提案批，候选 SHALL 逐条经五道闸准入——过闸者入因子库（active）、未过者留事件史（原因码），逐条裁决（单条失败不阻断整批）；运行历史 SHALL 含 `{proposed, admitted, rejected, errors}` 计数与手动补跑通道；LLM 网关不可用时任务 SHALL fail loud（不静默、不伪造候选）。

#### Scenario: 提案过闸入库

- **GIVEN** 提案批产出 3 条候选、其中 1 条通过五道闸
- **WHEN** 月批任务运行
- **THEN** 1 条入因子库（active）、2 条留事件史（含未过闸原因）
- **AND** 摘要 `{proposed: 3, admitted: 1, rejected: 2}`

#### Scenario: 网关不可用 fail loud

- **GIVEN** LLM 网关整体不可用
- **WHEN** 月批任务运行
- **THEN** 运行历史记 failed 且错误可判读
- **AND** 因子库零新增（MUST NOT 以 builtin 顶替或静默跳过）
