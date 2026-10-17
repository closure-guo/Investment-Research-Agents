# Delta for factor-research-monitor（add-factor-research-monitor）

## ADDED Requirements

### Requirement: 状态快照采集契约

外部因子研发（RD-Agent 持续循环看护）的运行状态 SHALL 由 distro 侧采集器周期写入宿主可见的
JSON 快照（默认路径 `data/factor_research/status.json`，经环境变量可覆盖）：采集周期 SHALL ≤ 60 秒；
写入 SHALL 原子（临时文件 + 重命名，读取方 MUST NOT 观察到半写文件）；采集器 SHALL 只读观测
（MUST NOT 修改研发进程状态、MUST NOT 干预会话生命周期）。快照 SHALL 含：`collected_at`（ISO8601
带时区）与 `collected_at_epoch`（Unix 秒）、`supervisor`（`running` / `pid` / `started_at`）、
`current_session`（`log_file` / `started_at` / `duration_s` / `alive` / `loop_index` / `step_index` /
`step_name` / `steps_total` / `step_elapsed_s` / `step_eta_s` / `factors` / `log_tail`）、
`sessions`（历史列表，各含 `log_file` / `started_at` / `ended_at` / `duration_s`）、
`totals`（`sessions` / `candidates` / `workspace_tasks`）。看护停止后采集器 SHALL 写末次快照
（`supervisor.running=false`）后方可退出。日志中无可解析标记时（如会话刚启动），对应字段 SHALL
如实置空（`null` / 空数组），MUST NOT 编造进度数字。

#### Scenario: 周期快照原子落盘

- **GIVEN** 采集器在研发运行期间工作
- **WHEN** 任一采集周期结束
- **THEN** 快照文件含完整 JSON（含 `collected_at`、`collected_at_epoch` 与全部顶层字段）
- **AND** 读取方任一时刻读到的都是完整快照（无半写内容）

#### Scenario: 停止写末次快照

- **GIVEN** 看护进程被显式停止
- **WHEN** 停止流程收尾
- **THEN** 快照 `supervisor.running=false` 且 `collected_at_epoch` 为停止时刻
- **AND** 采集器随之退出

#### Scenario: 未知字段如实置空

- **GIVEN** 当前会话日志中尚无进度标记
- **WHEN** 采集器写快照
- **THEN** `loop_index` / `step_index` / `step_name` 为 null、`factors` 为空数组
- **AND** MUST NOT 以 0 或猜测值冒充已解析的进度

### Requirement: 只读查询端点

系统 SHALL 提供 `GET /api/factor-research/status` 只读端点：快照路径 SHALL 在调用期读取
`FACTOR_RESEARCH_STATUS_PATH`（默认 `data/factor_research/status.json`）；快照存在且可解析时 SHALL
返回快照全量字段并附加派生字段 `available=true`、`age_seconds`（自 `collected_at_epoch` 至请求
时刻的秒数）与 `stale`（`age_seconds > 180` 为真）；快照文件缺失或不可解析时 SHALL 返回 200 +
`available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 或伪快照冒充。端点 MUST NOT
写入或修改任何状态。

#### Scenario: 快照存在时透传与派生

- **GIVEN** 快照文件合法且 `collected_at_epoch` 距请求时刻 40 秒
- **WHEN** 请求端点
- **THEN** 返回 200、`available=true`、`age_seconds≈40`、`stale=false`
- **AND** 快照原始字段（supervisor / current_session / sessions / totals）逐字透传

#### Scenario: 陈旧快照如实标记

- **GIVEN** 快照文件合法但 `collected_at_epoch` 距请求时刻 400 秒
- **WHEN** 请求端点
- **THEN** 返回 `stale=true` 且 `age_seconds≈400`
- **AND** MUST NOT 因陈旧而拒绝返回快照内容

#### Scenario: 缺失与损坏如实区分

- **GIVEN** 快照文件不存在
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `reason=missing`

- **GIVEN** 快照文件存在但内容非合法 JSON
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `reason=corrupt`
- **AND** 前端可据此展示与缺失不同的空态文案
