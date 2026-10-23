# Delta for eval-ops-console

## MODIFIED Requirements

### Requirement: 日批运行状态与历史可观测

系统 SHALL 提供只读接口返回全部已注册日批任务的运行状态：任务标识、排程（日/周/月粒度 + 时刻 + 时区）、下次触发时间、最近一次运行（开始/结束时间、结果摘要、错误信息），以及 `missed_today` 标志。`missed_today` 的判定 SHALL 为确定性口径：该任务当天的计划触发时刻已过，且 `job_runs` 中当天没有任何运行行（`running` 亦算「有」）；未到计划时刻、当天不在排程日（工作日任务的周末、月批任务的非排程日）SHALL 恒为 `false`。`missed_today` 回答「压根没跑」，SHALL NOT 与「跑了但失败」混同——后者由 `last_run.status=failed` 表达。每次任务触发 SHALL 落运行历史（含 cohort 开关关闭时的空转、失败与重试、手动补跑、启动补跑、配置变更审计行），使「某天跑没跑」可查而非只能翻日志。调度器未启动（TESTING=1 或显式禁用）时接口 SHALL 显式返回「未运行」状态，SHALL NOT 以 500 或空数据冒充正常。

#### Scenario: 五任务状态齐备

- **WHEN** 请求运行状态接口
- **THEN** SHALL 返回全部已注册任务（当前 27 个：判定 / 盯市 / 指标快照 / 完整性 / cohort / 行情快照 / 因子衰减 / 仿真结算 / 选股 / 仿真执行 / 记忆提炼 / 因子提案等）各自的排程、下次触发时间与最近一次运行结果
- **AND** 每项 SHALL 含结果摘要（如 settle 的 settled/superseded/unresolvable 计数）或错误信息
- **AND** 月粒度任务的排程 SHALL 以「每月 N 日 HH:MM」形态可读呈现

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

#### Scenario: 计划时刻已过且当天无运行判为 missed_today

- **GIVEN** `factor_decay_monitor` 排程为工作日 15:45，当天 `job_runs` 无该任务任何行
- **WHEN** 在当天 16:10 请求运行状态接口
- **THEN** 该任务行 `missed_today` SHALL 为 `true`
- **AND** 其余当天已有运行行的任务 SHALL 为 `false`

#### Scenario: 未到点与失败不判为 missed_today

- **GIVEN** 当天 15:00（尚未到 15:45），或该任务当天已有一行 `failed`
- **WHEN** 请求运行状态接口
- **THEN** `missed_today` SHALL 为 `false`（未到点；或已有运行行，失败由 `last_run.status` 表达）

#### Scenario: 非排程日恒为 false

- **GIVEN** 周六（工作日任务）或某月非 1 日（月批任务）
- **WHEN** 请求运行状态接口
- **THEN** 相关任务 `missed_today` SHALL 为 `false`（当天本无计划触发点）

## ADDED Requirements

### Requirement: 错过的日批触发补偿

调度器启动时 SHALL 对「当天计划触发时刻已过、且当天无成功运行（`status=ok`）」的白名单日批任务补跑一次，用于进程重建/冻结导致触发点被永久丢弃的场景（`MemoryJobStore` 重启后不回溯错过的触发点）。补跑 SHALL 同时满足四道约束：

1. **白名单**：只覆盖幂等且当日补齐有价值的日批（行情快照 / 拥挤度 / 选股 / 因子衰减监控 / 内置因子注册 / 判定 / 盯市 / 指标快照 / 完整性校验）。账务与下单类（`paper_execute_daily`、`paper_settle_daily`、`event_settle_daily`）与 LLM 烧钱类（`cohort_batch`、`daily_brief_daily`、`factor_harvest_daily`、`earnings_triage_daily`、`auto_pool_analysis_daily`）SHALL NOT 自动重放，仍只经人工补跑。
2. **时间窗**：只在计划时刻后 **6 小时** 内补跑；超出窗口 SHALL 放弃（迟到过久对当日链无意义），并记一条 `skipped` 语义日志。
3. **单次上限**：每次启动最多补 **3** 个任务，按计划时刻升序取前 3（保证「快照 → 下游」的顺序不被截断时仍优先补最早的）。
4. **非交易日链中止**：`market_snapshot_daily` 的返回摘要含 `skipped == "non_trading_day"` 时，SHALL 中止后续全部补跑（休市日不产生一串下游 failed）。

补跑 SHALL 经统一入口 `run_job(source="catchup")` 执行并落运行历史（可区分于 `scheduled` / `manual`）；SHALL 在后台线程执行，SHALL NOT 阻塞 API 启动；任一补跑异常 SHALL 只记日志并继续/结束，SHALL NOT 向启动链路上抛。补跑与定时/手动共享单飞锁，命中「已在运行」SHALL 视为跳过而非失败。环境变量 `OPS_CATCHUP_ON_STARTUP=0` SHALL 整体关闭启动补跑（供 dev 反复重启时止血）。

#### Scenario: 重建窗口打掉的因子监控在启动时补跑

- **GIVEN** 15:45 触发点因进程重建被丢弃，当天 `factor_decay_monitor` 无运行行
- **WHEN** 17:29 后端重建完成、调度器启动（距计划时刻 1h44m，窗内）
- **THEN** SHALL 在后台补跑 `factor_decay_monitor` 一次并落一行历史（`source=catchup`）
- **AND** 补跑完成后 `factor_ic` SHALL 得到该交易日 IC（不再永久缺口）
- **AND** `missed_today` SHALL 由 `true` 转为 `false`

#### Scenario: 已成功运行的任务不重复补跑

- **GIVEN** 当天该任务已有 `status=ok` 的运行行
- **WHEN** 进程再次重启并触发启动补跑
- **THEN** SHALL NOT 重跑该任务
- **AND** SHALL NOT 产生任何新的 LLM 调用或订单

#### Scenario: 超出时间窗不补跑

- **GIVEN** `factor_decay_monitor` 计划 15:45，进程 23:00 才启动（7h15m）
- **WHEN** 调度器启动
- **THEN** SHALL NOT 补跑该任务（超 6h 窗）
- **AND** 运行历史 SHALL NOT 新增该任务行，缺口仍由人工补跑处置

#### Scenario: 单次上限按计划时刻升序截断

- **GIVEN** 当天 5 个白名单任务均未成功运行，且都在时间窗内
- **WHEN** 调度器启动
- **THEN** SHALL 只补跑计划时刻最早的 3 个
- **AND** 其余 2 个 SHALL 保持 `missed_today=true` 等待人工处置

#### Scenario: 休市日不产生下游补跑

- **GIVEN** 当天为交易所休市日，`market_snapshot_daily` 返回 `{"skipped": "non_trading_day"}`
- **WHEN** 启动补跑依次执行
- **THEN** 快照之后的选股/因子监控等 SHALL NOT 被补跑
- **AND** SHALL NOT 产生下游 `failed` 行

#### Scenario: 补跑失败不阻断启动

- **GIVEN** 补跑任务抛异常（数据源不可用等）
- **WHEN** 调度器启动
- **THEN** 该任务 SHALL 落一行 `failed` 历史并继续（或结束）后续补跑
- **AND** API SHALL 正常对外服务（SHALL NOT 因补跑失败而启动失败）
