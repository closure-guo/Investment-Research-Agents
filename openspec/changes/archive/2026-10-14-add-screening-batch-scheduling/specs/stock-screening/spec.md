# stock-screening delta: add-screening-batch-scheduling

## ADDED Requirements

### Requirement: 日批排程与手动补跑

选股日批 SHALL 注册为统一日批任务（与判定 / 盯市 / 快照 / 结算链同一注册表），排程为工作日 15:40（Asia/Shanghai，晚于行情快照任务 15:30），并 SHALL 经统一通道提供运行历史（含 `source=scheduled/manual`）与手动补跑；快照未就绪时该批次 SHALL fail loud（批次标记 failed、不产出精选池），MUST NOT 以跳过或空池冒充成功。

#### Scenario: 排程注册可见

- **GIVEN** 调度器已启动
- **WHEN** 请求日批运行状态接口
- **THEN** 返回任务清单含选股任务的排程（工作日 15:40 Asia/Shanghai）与下次触发时间
- **AND** 其运行历史含每次触发的开始/结束时间与结果摘要（`l1_passed/l2_top/l3_keep/pool_size` 或错误信息）

#### Scenario: 快照未就绪 fail loud

- **GIVEN** 当日快照缺失（快照库为空或数据日期早于当日）
- **WHEN** 选股日批触发
- **THEN** 该批次状态为 failed、当日精选池零行
- **AND** 运行历史摘要含可判读的失败原因

#### Scenario: 手动补跑

- **GIVEN** 当日选股日批失败
- **WHEN** 运维经统一通道手动补跑
- **THEN** 以 `source=manual` 落新运行行并执行同一编排
- **AND** 成功批次恢复产出精选池
