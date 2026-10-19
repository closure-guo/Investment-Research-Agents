# factor-research-monitor Specification

## Purpose
外部因子研发（RD-Agent 持续循环看护）运行状态的可观测面：distro 侧采集器周期写 JSON 快照（经宿主
`data/` 桥），后端只读端点透传并派生陈旧标记，供 `/factor-research` 监控页呈现研发在不在跑、
跑到哪一步、产出了什么。created by archiving change add-factor-research-monitor.
## Requirements
### Requirement: 状态快照采集契约

外部因子研发（RD-Agent 持续循环看护）的运行状态 SHALL 由 distro 侧常驻**研发代理**周期写入宿主
可见的 JSON 快照（默认路径 `data/factor_research/status.json`，经环境变量可覆盖）：采集周期
SHALL ≤ 60 秒；写入 SHALL 原子（临时文件 + 重命名，读取方 MUST NOT 观察到半写文件）；采集路径
SHALL 只读观测（MUST NOT 修改研发进程状态、MUST NOT 干预会话生命周期）。快照 SHALL 含：
`collected_at`（ISO8601 带时区）与 `collected_at_epoch`（Unix 秒）、`supervisor`（`running` /
`pid` / `started_at`）、`current_session`（`log_file` / `started_at` / `duration_s` / `alive` /
`loop_index` / `step_index` / `step_name` / `steps_total` / `step_elapsed_s` / `step_eta_s` /
`factors` / `log_tail`）、`sessions`（历史列表，各含 `log_file` / `started_at` / `ended_at` /
`duration_s`）、`totals`（`sessions` / `candidates` / `workspace_tasks`）与 `agent`
（`last_action` / `last_action_at` / `last_error`）。`agent.last_action` SHALL 为代理最近一次
期望态调和动作，取值 `no_intent` / `noop` / `started` / `stopped` / `start-failed` /
`stop-failed`（控制文件缺失/损坏 → `no_intent`；期望与实态一致 → `noop`）；`last_action_at` SHALL 为该动作的 ISO8601 时刻（尚无动作 → `null`）；
`last_error` SHALL 在 `start-failed` / `stop-failed` 时为可判读的失败摘要（异常类型或首行，
MUST NOT 编造），成功动作后 SHALL 清空为 `null`。代理之外的独立采集路径（无调和动作可报）SHALL
写 `agent: null`，MUST NOT 伪造动作。代理 SHALL 独立于看护生命周期存活：看护停止（经控制通道或
手动脚本）后代理 MUST NOT 随之退出，SHALL 继续周期写快照（如实反映 `supervisor.running=false`）
直至操作者显式停止代理本身。日志中无可解析标记时（如会话刚启动），对应字段 SHALL 如实置空
（`null` / 空数组），MUST NOT 编造进度数字。

(Previously: 快照字段不含 `agent` 块；调和动作只进代理日志、不进快照。)

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

### Requirement: 只读查询端点

系统 SHALL 提供 `GET /api/factor-research/status` 只读端点：快照路径 SHALL 在调用期读取
`FACTOR_RESEARCH_STATUS_PATH`（默认 `data/factor_research/status.json`）；快照存在且可解析时 SHALL
返回快照全量字段并附加派生字段 `available=true`、`age_seconds`（自 `collected_at_epoch` 至请求
时刻的秒数）与 `stale`（`age_seconds > 180` 为真）；快照文件缺失或不可解析时 SHALL 返回 200 +
`available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 或伪快照冒充。响应 SHALL 另
含控制块 `control`：`desired_running`（布尔；控制文件缺失、不可解析或字段非法 → `null`）、
`requested_at`（ISO8601 或 `null`）、`pending`（期望态非空、快照 available 且非陈旧、且期望态与
快照实态不一致时为真，其余情形为假）、`honor`（枚举 `live` / `launcher` / `unavailable`）与
`honor_eta_seconds`（`0` / `180` / `null`）。`honor` 判定：快照 available 且非陈旧 → `live`
（`honor_eta_seconds=0`）；否则启动器心跳 available、`fresh` 且 `agent_ok=true` → `launcher`
（`honor_eta_seconds=180`）；其余 → `unavailable`（`honor_eta_seconds=null`）。控制块 SHALL 在
快照不可用/陈旧时同样返回（`pending` 为假——实态不可证）。响应 SHALL 另含启动器块 `launcher`：
路径 SHALL 在调用期读取 `FACTOR_RESEARCH_LAUNCHER_PATH`（默认
`data/factor_research/launcher.json`）；文件存在且可解析 → `available=true` 并透传 `checked_at` /
`checked_at_epoch` / `agent_pid` / `agent_ok` / `snapshot_age_s` / `detail`，附加派生 `age_seconds`
（自 `checked_at_epoch` 至请求时刻的秒数）与 `fresh`（`age_seconds ≤ 300` 为真）；文件缺失或
不可解析 → `available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 冒充。端点 MUST NOT
写入或修改任何状态。

(Previously: 响应无 `launcher` 块；`control` 块只有 desired_running / requested_at / pending。)

#### Scenario: 快照存在时透传与派生

- **GIVEN** 快照文件合法且 `collected_at_epoch` 距请求时刻 40 秒（`supervisor.running=true`）
- **WHEN** 请求端点
- **THEN** 返回 200、`available=true`、`age_seconds≈40`、`stale=false`
- **AND** 快照原始字段（supervisor / current_session / sessions / totals / agent）逐字透传
- **AND** `control` 块与控制文件一致（缺失 → `desired_running=null`），实态与期望不一致时 `pending=true`
- **AND** `control.honor="live"`、`honor_eta_seconds=0`

#### Scenario: 陈旧快照如实标记

- **GIVEN** 快照文件合法但 `collected_at_epoch` 距请求时刻 400 秒
- **WHEN** 请求端点
- **THEN** 返回 `stale=true` 且 `age_seconds≈400`
- **AND** MUST NOT 因陈旧而拒绝返回快照内容
- **AND** `control.pending` 为假（实态不可证，MUST NOT 据陈旧数据判「切换中」）

#### Scenario: 缺失与损坏如实区分

- **GIVEN** 快照文件不存在
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `reason=missing`

- **GIVEN** 快照文件存在但内容非合法 JSON
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `reason=corrupt`
- **AND** 前端可据此展示与缺失不同的空态文案

#### Scenario: 启动器心跳透传与派生

- **GIVEN** 快照陈旧（400 秒）且 `launcher.json` 合法、`checked_at_epoch` 距请求 60 秒、`agent_ok=true`
- **WHEN** 请求端点
- **THEN** `launcher.available=true`、`launcher.age_seconds≈60`、`launcher.fresh=true`
- **AND** `control.honor="launcher"`、`honor_eta_seconds=180`、`pending=false`

#### Scenario: 双无证据 honor 不可用

- **GIVEN** 快照缺失/损坏/陈旧，且 `launcher.json` 缺失/损坏/不新鲜或 `agent_ok=false`
- **WHEN** 请求端点
- **THEN** `control.honor="unavailable"`、`honor_eta_seconds=null`
- **AND** MUST NOT 以 `launcher` 冒充可受理（无启动器存活证据即无受理能力）

### Requirement: 看护控制通道

distro 侧研发代理 SHALL 提供看护启停控制通道：周期（≤ 15 秒）轮询宿主可见控制文件（默认
`data/factor_research/control.json`，经环境变量可覆盖），按**期望状态调和**语义执行——
`desired_running=true` 且看护未运行 → 复用既有启动流程拉起（会话循环与退避策略不变）；
`desired_running=false` 且看护运行中 → 复用既有停止语义停止（终止当前会话，与手动停止同一实现）；
期望与实态一致 → MUST NOT 动作（幂等）。控制文件 SHALL 含 `desired_running`（布尔）、
`requested_at`（ISO8601 带时区）、`requested_at_epoch`（Unix 秒）与 `request_id`（请求标识，供
留痕）；写入方 SHALL 原子写。控制文件缺失、不可解析或字段非法时，代理 MUST NOT 启停任何进程
（fail-safe，MUST NOT 猜测期望态），SHALL 留可判读日志。停止动作 MUST NOT 删除研发产物
（workspace 任务 / 候选文件 / 会话日志保持）；启动与停止动作 SHALL 由代理与手动脚本共享同一启停语义（代理为
唯一主实现；脚本仅在代理不可用时保留逐字兜底硬停）。代理 SHALL 以进程锁串行化控制与采集动作（守护轮询与手动触发不并发）。

#### Scenario: 期望开启拉起看护

- **GIVEN** 控制文件 `desired_running=true` 且看护未运行（pidfile 不存在/进程已死）
- **WHEN** 代理轮询（或手动触发调和）
- **THEN** 看护被拉起（与手动启动同一实现）
- **AND** 快照在 ≤ 60 秒内反映 `supervisor.running=true`

#### Scenario: 期望停止停看护

- **GIVEN** 控制文件 `desired_running=false` 且看护运行中（含进行中的会话）
- **WHEN** 代理轮询（或手动触发调和）
- **THEN** 看护与当前会话被停止（与手动停止同一语义）
- **AND** 快照反映 `supervisor.running=false`，代理本身仍在运行（继续周期快照与控制通道）

#### Scenario: 期望与实态一致不动作

- **GIVEN** 控制文件 `desired_running=false` 且看护已停止（或 `true` 且运行中）
- **WHEN** 代理轮询
- **THEN** 零启停动作（幂等，MUST NOT 重复拉起/重复杀进程）

#### Scenario: 无控制文件不干预

- **GIVEN** 控制文件不存在（首次部署 / 从未下过控制指令）
- **WHEN** 代理轮询
- **THEN** MUST NOT 启停看护（保持现状）

#### Scenario: 控制文件损坏不干预

- **GIVEN** 控制文件非合法 JSON 或 `desired_running` 字段缺失/非布尔
- **WHEN** 代理轮询
- **THEN** MUST NOT 启停看护
- **AND** 日志留可判读记录（MUST NOT 静默猜测期望态）

#### Scenario: 停止不清数据

- **GIVEN** 看护运行中且工作区已有候选实现与候选文件
- **WHEN** 期望停止的调和动作执行
- **THEN** workspace 任务目录 / 候选文件 / 会话日志保持原样（MUST NOT 删除）
- **AND** 重新开启后研发从既有工作区继续（不重置）

### Requirement: 控制请求端点

系统 SHALL 提供 `PUT /api/factor-research/control` 端点（body：`desired_running` 必填布尔），
原子写入控制文件（默认 `data/factor_research/control.json`；路径 SHALL 在调用期读取
`FACTOR_RESEARCH_CONTROL_PATH`），返回 `{"control": {...}}`（与状态端点 control 块同形状，含
`honor` / `honor_eta_seconds`）。请求体 MUST 仅接受布尔期望态（MUST NOT 引入任意命令/脚本/参数
字段——命令注入面为零）。受理规则：快照新鲜（`available=true` 且 `stale=false`）→ SHALL 受理
（`honor="live"`、`honor_eta_seconds=0`）；快照缺失、损坏或陈旧时，若启动器心跳 available、
`fresh` 且 `agent_ok=true` → SHALL 仍受理（`honor="launcher"`、`honor_eta_seconds=180`——请求
将在下一启动器节拍被拉起并按期望态调和）；两者皆不成立（启动器缺失/损坏/不新鲜或
`agent_ok=false`）→ SHALL 拒绝（409 + `control_unavailable`），MUST NOT 写入控制文件。期望态与
已存值相同时 SHALL 幂等成功（可更新 `requested_at` / `request_id` 留痕）。端点 MUST NOT 调用 LLM、
MUST NOT 直接触碰研发进程。

(Previously: 快照缺失/损坏/陈旧即 409 拒绝——无启动器存活证据维度。)

#### Scenario: 控制写入成功

- **GIVEN** 快照新鲜且 `supervisor.running=true`
- **WHEN** `PUT` `desired_running=false`
- **THEN** 控制文件原子落地（含 `desired_running` / `requested_at` / `requested_at_epoch` / `request_id`）
- **AND** 响应 200 且 `control.desired_running=false`、`control.pending=true`（期望与实态不一致）
- **AND** `control.honor="live"`

#### Scenario: 启动器可用时离线受理

- **GIVEN** 快照缺失/损坏/陈旧，但 `launcher.json` 新鲜（≤300 秒）且 `agent_ok=true`
- **WHEN** `PUT` `desired_running=true`
- **THEN** 返回 200，控制文件原子落地
- **AND** 响应 `control.honor="launcher"`、`honor_eta_seconds=180`
- **AND** 下一启动器节拍（≤2 分钟）拉起研究侧后，代理按该期望态调和并在 ≤3 分钟内写出首个快照

#### Scenario: 通道无证据拒绝

- **GIVEN** 快照缺失/损坏/陈旧，且启动器心跳缺失/损坏/不新鲜或 `agent_ok=false`
- **WHEN** `PUT` `desired_running=true`
- **THEN** 返回 409 + `control_unavailable`
- **AND** 控制文件未被写入（MUST NOT 留下无人受理的请求）

#### Scenario: 幂等重复

- **GIVEN** 控制文件已含 `desired_running=false`
- **WHEN** 再次 `PUT` `desired_running=false`
- **THEN** 返回 200（`requested_at` 可更新留痕）且不产生额外副作用

### Requirement: 研究侧自愈启动

研究侧 SHALL 具备宿主级自愈启动器：在用户登录时并以 ≤ 2 分钟的固定节拍，幂等调用 distro 入口
`ensure-factor-research.sh`（→ 研发代理 `--launcher-tick`）。该入口 SHALL：

1. 确保研发代理守护存活——已在运行 MUST NOT 重复拉起（复用既有单例锁；重复实例 SHALL 被安全
   拒绝且不终止既有实例）；
2. 原子写启动器心跳（默认 `data/factor_research/launcher.json`，路径 SHALL 在调用期读取
   `FACTOR_RESEARCH_LAUNCHER_PATH`），含 `checked_at`（ISO8601 带时区）、`checked_at_epoch`
   （Unix 秒）、`agent_pid`（整数或 `null`）、`agent_ok`（布尔）、`snapshot_age_s`（整数或
   `null`：最近状态快照距检查时刻的秒数）与 `detail`（可判读说明或 `null`）；
3. MUST NOT 写入或修改控制文件——用户意图的唯一写入面不变，启动器 MUST NOT 覆盖期望态；
4. MUST NOT 直接启停看护——看护启停只由代理按期望态调和（单一实现）；
5. MUST NOT 删除或修改任何研发产物（workspace / 候选文件 / 会话日志）。

心跳新鲜阈值 SHALL 为 300 秒（2× 节拍 + 余量），由后端派生 `fresh`。心跳写失败 MUST NOT 影响
代理存活保证；心跳缺失时后端 MUST NOT 声称启动器可用（fail-safe）。

#### Scenario: 代理被杀后自愈

- **GIVEN** 代理守护进程被外部终止（distro 仍在运行）
- **WHEN** 下一启动器节拍（≤2 分钟）
- **THEN** 代理守护被重新拉起，心跳 `agent_ok=true`
- **AND** 状态快照在 ≤3 分钟内恢复周期推进（不重置研发工作区）

#### Scenario: distro 停止后自愈

- **GIVEN** distro 被终止（虚拟机仍可用）
- **WHEN** 下一启动器节拍
- **THEN** distro 被启动、代理守护被拉起、心跳落地
- **AND** 若控制文件 `desired_running=true`，代理按期望态拉起看护并在 ≤3 分钟内写出首个快照

#### Scenario: 意图不被改写

- **GIVEN** 控制文件 `desired_running=false` 且看护已停止
- **WHEN** 启动器执行任意次节拍
- **THEN** 控制文件内容保持语义不变（MUST NOT 被启动器写入或覆盖）
- **AND** 看护保持停止（MUST NOT 被启动器直接拉起）

#### Scenario: 单例不重复拉起

- **GIVEN** 代理守护已在运行
- **WHEN** 启动器执行节拍
- **THEN** MUST NOT 启动第二个代理实例（单例锁安全拒绝）
- **AND** 心跳 `agent_pid` 与既有实例一致

#### Scenario: 心跳不可写不阻断

- **GIVEN** 宿主桥（`/mnt/d`）暂时不可写或读取缓慢
- **WHEN** 启动器执行节拍
- **THEN** 代理存活保证不受影响（MUST NOT 因心跳写失败而退出）
- **AND** 心跳缺失/过期时后端按 `launcher.available=false` / `fresh=false` 处理（fail-safe）

