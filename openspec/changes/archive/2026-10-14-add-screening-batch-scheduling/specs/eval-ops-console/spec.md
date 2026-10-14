# eval-ops-console delta: add-screening-batch-scheduling

## MODIFIED Requirements

### Requirement: 日批运行状态与历史可观测

系统 SHALL 提供只读接口返回全部已注册日批任务的运行状态：任务标识、排程（工作日 + 时刻 + 时区）、下次触发时间、最近一次运行（开始/结束时间、结果摘要、错误信息）。每次任务触发 SHALL 落运行历史（含 cohort 开关关闭时的空转、失败与重试、手动补跑、配置变更审计行），使「某天跑没跑」可查而非只能翻日志。调度器未启动（TESTING=1 或显式禁用）时接口 SHALL 显式返回「未运行」状态，SHALL NOT 以 500 或空数据冒充正常。

#### Scenario: 五任务状态齐备

- **WHEN** 请求运行状态接口
- **THEN** SHALL 返回全部已注册任务（当前 9 个：判定 / 盯市 / 指标快照 / 完整性 / cohort / 行情快照 / 因子衰减 / 仿真结算 / 选股）各自的排程、下次触发时间与最近一次运行结果
- **AND** 每项 SHALL 含结果摘要（如 settle 的 settled/superseded/unresolvable 计数）或错误信息

#### Scenario: 调度器未启动显式态

- **GIVEN** TESTING=1 或 DECISION_SETTLE_ENABLED=0
- **WHEN** 请求运行状态接口
- **THEN** SHALL 返回 200 且带「调度器未运行」的显式标记
- **AND** SHALL NOT 返回 500 或以空列表冒充「一切正常」

#### Scenario: 空转与失败均留痕

- **GIVEN** cohort 开关关闭，或某任务执行失败
- **WHEN** 到达触发时刻
- **THEN** 运行历史 SHALL 各记一行（状态分别为 skipped-disabled / failed，含原因）
- **AND** 重试次数 SHALL 随失败行披露
