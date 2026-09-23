# Delta for paper-trading-cohort

## ADDED Requirements

### Requirement: Cohort 标的池登记与版本化

系统 SHALL 维护 cohort 标的池登记文件：成分清单、分层依据（行业/市值）、抽样种子、版本号（universe_version）、生效日期。标的池 SHALL 自沪深300 成分分层抽样产生，抽样脚本 SHALL 可由种子复现。版本生效期内标的池 SHALL NOT 变更；换池 SHALL 以新版本号登记，跑批记账 SHALL 携带 universe_version，保证同版本内读数纵向可比。

#### Scenario: 登记文件字段完备

- **WHEN** 一个 universe_version 被跑批引用
- **THEN** 其登记文件 SHALL 含成分清单 / 分层依据 / 抽样种子 / 版本号 / 生效日期，缺一即校验失败
- **AND** 按种子重跑抽样脚本 SHALL 复现同一成分清单

#### Scenario: 版本期内换池被拒

- **GIVEN** 某 universe_version 处于生效期
- **WHEN** 尝试修改其成分清单
- **THEN** 系统 SHALL 拒绝（或强制生成新版本号）
- **AND** 已落记账行的 universe_version SHALL 不被改写

### Requirement: 定时跑批与记账

系统 SHALL 按配置频率（默认每交易日一次，盘后时移窗口）对标的池内每只标的串行执行 deep 分析，执行路径 SHALL 与 `/api/analyze` 真实代码路径一致（观点经既有挂点自然落 `predictions`，source_type=live），SHALL NOT 为 cohort 另建分析或落库路径。每轮跑批 SHALL 写入 `cohort_runs` 记账表：run_id / universe_version / ticker / session_id / langfuse_trace_id / 触发时间 / 状态（success/failure/skipped）/ 失败原因（如有）/ LLM 调用数与 token 成本。幂等键 = (universe_version, ticker, 交易日)：同日重复触发 SHALL 跳过，显式 force SHALL 以 run_seq 递增另记。单标的失败 SHALL 跳过并记账，SHALL NOT 阻塞本轮其余标的，SHALL NOT 在跑批层重试。

#### Scenario: 正常跑批记账完备

- **WHEN** 一轮 cohort 跑批完成
- **THEN** cohort_runs SHALL 为池内每只标的各记一行（success/failure/skipped）
- **AND** success 行 SHALL 可经 session_id 或 langfuse_trace_id join 到对应 predictions 观点行

#### Scenario: 同日重复触发幂等

- **GIVEN** 某 (universe_version, ticker, 交易日) 已有 success 记账
- **WHEN** 同日再次触发跑批
- **THEN** 该标的 SHALL 被跳过，不重复分析、不重复落观点
- **AND** 显式 force 时 SHALL 以 run_seq 递增新增记账行

#### Scenario: 单标失败不阻塞整批

- **GIVEN** 池内某标的数据源不可用
- **WHEN** 跑批处理该标的失败
- **THEN** 该行记账 status=failure 并含原因
- **AND** 本轮其余标的 SHALL 继续执行

#### Scenario: 观点走真实链路零特判

- **WHEN** cohort 跑批产出观点
- **THEN** 观点落库、结算、盯市、Score 上报 SHALL 与用户流量观点走完全相同链路
- **AND** 结算侧 SHALL 不存在针对 cohort 观点的特殊分支

### Requirement: 成本预算与运维开关

cohort 跑批 SHALL 由显式运维开关控制，默认关闭；开关关闭时调度器 SHALL NOT 触发任何跑批。每轮跑批的 LLM 调用数与 token 成本 SHALL 汇总落记账；单轮成本达到预算上限（配置项）时 SHALL 停止本轮后续标的、记账 skipped 并告警。跑批 SHALL 串行执行且置于可配置的时移窗口（默认盘后），SHALL NOT 与用户流量并发争抢（单 worker / StreamRegistry 约束）。

#### Scenario: 默认关闭

- **GIVEN** 运维开关未显式开启
- **WHEN** 到达配置的跑批时刻
- **THEN** SHALL NOT 触发跑批，SHALL NOT 产生任何 LLM 调用

#### Scenario: 预算超限熔断

- **GIVEN** 本轮已耗 token 达到单轮预算上限
- **WHEN** 处理下一标的前
- **THEN** SHALL 停止本轮，剩余标的记账 skipped（原因=预算熔断）并告警
- **AND** 已启动的分析 SHALL 正常完成落库，不中断丢弃

#### Scenario: 成本随轮落账

- **WHEN** 一轮跑批结束
- **THEN** 记账 SHALL 含本轮逐标的与汇总的 LLM 调用数 / token 成本（usage 真值，非估算）

### Requirement: Cohort 读数导出

系统 SHALL 提供评估侧导出能力：按 universe_version 与时间窗导出 cohort 观点清单及结算状态（cohort_runs join predictions），字段 SHALL 满足 outcome 收口健康检查所需（跑批成功率 / 观点落库率 / 结算状态分布 / failure 明细）。导出 SHALL 为只读操作，SHALL NOT 修改任何观点或记账行。

#### Scenario: 按版本与时间窗导出

- **WHEN** 评估侧请求某 universe_version 在某时间窗的 cohort 读数
- **THEN** SHALL 返回逐观点行（ticker / created_at / direction / confidence / 结算状态 / 收益字段）与批次级汇总（成功率 / 落库率 / failure 明细）

#### Scenario: 导出只读

- **WHEN** 导出执行
- **THEN** predictions 与 cohort_runs SHALL 无任何写入或状态变化
