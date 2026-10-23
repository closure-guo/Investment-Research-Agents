## MODIFIED Requirements

### Requirement: 状态快照采集契约

外部因子研发（RD-Agent 持续循环看护）的运行状态 SHALL 由 distro 侧常驻**研发代理**周期写入宿主
可见的 JSON 快照（默认路径 `data/factor_research/status.json`，经环境变量可覆盖）：采集周期
SHALL ≤ 60 秒；写入 SHALL 原子（临时文件 + 重命名，读取方 MUST NOT 观察到半写文件）；采集路径
SHALL 只读观测（MUST NOT 修改研发进程状态、MUST NOT 干预会话生命周期）。快照 SHALL 含：
`collected_at`（ISO8601 带时区）与 `collected_at_epoch`（Unix 秒）、`supervisor`（`running` /
`pid` / `started_at`）、`current_session`（`log_file` / `started_at` / `duration_s` / `alive` /
`loop_index` / `step_index` / `step_name` / `steps_total` / `step_elapsed_s` / `step_eta_s` /
`factors` / `log_tail` / `log_age_s`）、`sessions`（历史列表，各含 `log_file` / `started_at` /
`ended_at` / `duration_s`）、`totals`（`sessions` / `candidates` / `workspace_tasks`）与 `agent`
（`last_action` / `last_action_at` / `last_error`）。`agent.last_action` SHALL 为代理最近一次
期望态调和动作，取值 `no_intent` / `noop` / `started` / `stopped` / `start-failed` /
`stop-failed`（控制文件缺失/损坏 → `no_intent`；期望与实态一致 → `noop`）；`last_action_at` SHALL 为该动作的 ISO8601 时刻（尚无动作 → `null`）；
`last_error` SHALL 在 `start-failed` / `stop-failed` 时为可判读的失败摘要（异常类型或首行，
MUST NOT 编造），成功动作后 SHALL 清空为 `null`。代理之外的独立采集路径（无调和动作可报）SHALL
写 `agent: null`，MUST NOT 伪造动作。代理 SHALL 独立于看护生命周期存活：看护停止（经控制通道或
手动脚本）后代理 MUST NOT 随之退出，SHALL 继续周期写快照（如实反映 `supervisor.running=false`）
直至操作者显式停止代理本身。日志中无可解析标记时（如会话刚启动），对应字段 SHALL 如实置空
（`null` / 空数组），MUST NOT 编造进度数字。

`current_session.log_age_s` SHALL 为当前会话日志文件 mtime 距采集时刻的秒数（非负整数）；
无当前会话日志文件或 stat 失败 → `null`。该字段 SHALL 只报**原始读数**——采集器 MUST NOT 自行
判定「僵死」（判定与阈值归只读查询端点单源），MUST NOT 以 0 或猜测值冒充已知新鲜度。

(Previously: `current_session` 不含 `log_age_s`；进度新鲜度不可观测——`alive` 只反映进程存在，
`step_elapsed_s` / `step_eta_s` 取自日志末行，日志冻结后读数定格，僵死会话与健康会话不可区分。)

#### Scenario: 周期快照原子落盘

- **GIVEN** 代理在研发运行期间工作
- **WHEN** 任一采集周期结束
- **THEN** 快照文件含完整 JSON（含 `collected_at`、`collected_at_epoch` 与全部顶层字段）
- **AND** 读取方任一时刻读到的都是完整快照（无半写内容）

#### Scenario: 停止写末次快照

- **GIVEN** 看护进程被显式停止（控制通道调和或手动脚本）
- **WHEN** 停止流程收尾
- **THEN** 快照 `supervisor.running=false` 且 `collected_at_epoch` 为停止时刻
  （其后由代理续写推进——「末次」语义由代理接管，不再以退出收尾）
- **AND** 代理进程仍在运行（MUST NOT 因看护停止而退出）

#### Scenario: 未知字段如实置空

- **GIVEN** 当前会话日志中尚无进度标记
- **WHEN** 代理写快照
- **THEN** `loop_index` / `step_index` / `step_name` 为 null、`factors` 为空数组
- **AND** MUST NOT 以 0 或猜测值冒充已解析的进度

#### Scenario: 调和动作留痕

- **GIVEN** 控制文件 `desired_running=true` 且看护未运行
- **WHEN** 代理完成一次调和并写快照
- **THEN** `agent.last_action="started"`、`agent.last_action_at` 为该次动作时刻、`agent.last_error=null`
- **AND** 下一次期望与实态一致时 `last_action` 回落到 `"noop"`

#### Scenario: 启动失败可读

- **GIVEN** 控制文件 `desired_running=true` 但启动流程失败（脚本缺失/权限/环境异常）
- **WHEN** 代理写快照
- **THEN** `agent.last_action="start-failed"` 且 `agent.last_error` 为非空可判读摘要
- **AND** MUST NOT 把失败写成 `noop` 或以猜测原因冒充

#### Scenario: 独立采集器如实置空

- **GIVEN** 独立调用采集器（无代理调和动作可报）
- **WHEN** 采集器写快照
- **THEN** 快照 `agent=null`
- **AND** MUST NOT 伪造 `last_action` 或动作时刻

#### Scenario: 日志新鲜度原始读数

- **GIVEN** 当前会话日志文件在 42 秒前被写入
- **WHEN** 采集器写快照
- **THEN** `current_session.log_age_s=42`（±1 秒）
- **AND** 采集器 MUST NOT 因该读数大而改写任何进程状态或跳过采集

- **GIVEN** 无当前会话日志文件（看护从未启动或日志目录为空）
- **WHEN** 采集器写快照
- **THEN** `current_session` 为 `null`（既有语义不变）且无 `log_age_s` 可报
- **AND** MUST NOT 以 0 冒充「刚刚写入」

## ADDED Requirements

### Requirement: 会话进度僵死派生

`GET /api/factor-research/status` 响应 SHALL 附加派生块 `progress`：`log_age_seconds`
（整数或 `null`，取自快照 `current_session.log_age_s`；快照不可用或字段缺失 → `null`）与
`stalled`（布尔）。`stalled` 判定 SHALL 为：快照 `available=true`、`supervisor.running=true`、
`current_session.alive=true` 且 `log_age_seconds > FACTOR_RESEARCH_STALL_AFTER_S` → `true`；
其余情形（快照不可用/陈旧、看护未运行、会话非存活、`log_age_seconds` 为 `null`）→ `false`。

阈值 SHALL 单源为后端常量 `FACTOR_RESEARCH_STALL_AFTER_S = 900`（秒）。依据：历史 14 个会话日志
实测最大静默 602 秒（4 次，疑为上游 600 秒超时）、中位 88 秒 ⇒ 取观测上界 1.5 倍，既覆盖正常
长操作又不误报，同时把本次 4h07m 级停摆在 15 分钟内暴露。该阈值 MUST NOT 由采集器或前端另行
定义（MUST NOT 出现第二份阈值）。

`progress` 块 SHALL 在快照不可用/陈旧时同样返回（`log_age_seconds=null`、`stalled=false`）——
实态不可证时 MUST NOT 报僵死，亦 MUST NOT 声称进度正常。端点 MUST NOT 因 `stalled=true` 而
自动采取任何处置动作（不重启、不写控制文件、不触碰研发进程）——本能力只负责**如实披露**。

#### Scenario: 日志静默超阈值判僵死

- **GIVEN** 快照 `available=true`、`stale=false`、`supervisor.running=true`、
  `current_session.alive=true`、`current_session.log_age_s=3600`
- **WHEN** 请求状态端点
- **THEN** `progress.log_age_seconds=3600`、`progress.stalled=true`
- **AND** 响应其余字段逐字透传（MUST NOT 因僵死而改写 `actual_state`）

#### Scenario: 静默在阈值内不告警

- **GIVEN** 同上，但 `current_session.log_age_s=602`（历史观测上界）
- **WHEN** 请求状态端点
- **THEN** `progress.stalled=false`
- **AND** MUST NOT 无端告警

#### Scenario: 无会话不判僵死

- **GIVEN** 快照新鲜但 `supervisor.running=false`（看护已停止）且 `current_session.log_age_s`
  为 `null` 或很大
- **WHEN** 请求状态端点
- **THEN** `progress.stalled=false`
- **AND** MUST NOT 把「看护没在跑」报成「会话僵死」

#### Scenario: 快照不可用时不判僵死

- **GIVEN** 快照缺失或损坏
- **WHEN** 请求状态端点
- **THEN** `available=false` 且 `progress.log_age_seconds=null`、`progress.stalled=false`

#### Scenario: 旧版采集器未上报新鲜度

- **GIVEN** 快照新鲜且会话存活，但 `current_session` 不含 `log_age_s`（旧版采集器）
- **WHEN** 请求状态端点
- **THEN** `progress.log_age_seconds=null`、`progress.stalled=false`
- **AND** 前端可据此不展示僵死提示（MUST NOT 把「未上报」当成「进度正常」的断言）

#### Scenario: 僵死不触发任何处置

- **GIVEN** `progress.stalled=true`
- **WHEN** 请求状态端点（可重复请求）
- **THEN** 控制文件、研发进程状态、快照内容均不被修改
- **AND** 解除僵死只能经既有控制通道（`PUT /control` / `POST /restart`）由人发起
