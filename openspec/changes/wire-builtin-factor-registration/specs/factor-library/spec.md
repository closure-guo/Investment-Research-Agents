# Delta: factor-library（wire-builtin-factor-registration）

## MODIFIED Requirements

### Requirement: builtin 因子注册路径

系统 SHALL 内置手工因子基线包（20 日动量、5 日反转、换手率、20 日波动率、市值代理），以 `category=builtin` 注册入因子库；builtin 因子免 LLM 提案环节，但 SHALL 同样通过五道闸方可置为 active。注册 SHALL 由日批任务「内置因子注册」（工作日 15:50，快照与因子衰减监控之后、结算链之前）承载，走统一日批通道（调度 / 运行历史 / 手动补跑 / 单飞锁）；库内已存在同表达式条目（任意状态，含 retired/degraded）SHALL 跳过（MUST NOT 复活 retired 条目、MUST NOT 重复入库）；存在缺失条目时 SHALL 以快照库最近面板逐条过闸，面板不可得 SHALL 上抛（任务记 failed，MUST NOT 以空面板放行注册）；摘要 SHALL 记录 `{registered, rejected, skipped, reasons}`（未过闸者逐条原因码）。

#### Scenario: builtin 因子受同等准入约束

- **GIVEN** builtin 反转因子在准入评估中 ICIR = 0.12
- **WHEN** 执行准入管线
- **THEN** 该因子被拒收于闸②，与 LLM 提案因子适用同一阈值

#### Scenario: 日批注册入口幂等（含 retired 不回填）

- **GIVEN** 因子库中已存在全部五条 builtin 条目（任意状态，含 retired）
- **WHEN** 内置因子注册日批执行
- **THEN** 五条均被跳过（摘要 `skipped=5 · registered=0 · rejected=0`），MUST NOT 复活 retired 条目、MUST NOT 重复入库
- **AND** 跳过路径 SHALL NOT 构造面板（快照库不可达亦不失败）

#### Scenario: 缺失条目过闸注册

- **GIVEN** 因子库存在缺失条目且快照面板可用
- **WHEN** 内置因子注册日批执行
- **THEN** 缺失条目逐条经五道闸：过闸者以 `category=builtin` / `source=builtin` 入库为 active，未过者摘要 `reasons` 记录逐条原因码
- **AND** 已存在条目（任意状态）计入 `skipped`，MUST NOT 重复提交准入

#### Scenario: 面板不可得 fail loud

- **GIVEN** 因子库存在缺失条目且快照库为空 / 历史不足
- **WHEN** 内置因子注册日批执行
- **THEN** 任务上抛（运行历史记 failed），MUST NOT 以空面板放行注册
