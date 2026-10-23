## ADDED Requirements

### Requirement: 研究侧 distro 探活与不可用自愈

宿主级自愈启动器 SHALL 在每次节拍（≤2 分钟）先做一次 **distro 可执行性探活**：
`wsl -d <distro> -e /bin/true`（超时 SHALL ≤20 秒），并把结果原子写入宿主侧探活文件（默认
`data/factor_research/distro_probe.json`，路径 SHALL 在调用期读取
`FACTOR_RESEARCH_DISTRO_PROBE_PATH`），字段 SHALL 含：`probed_at`（ISO8601 带时区）、
`probed_at_epoch`（Unix 秒）、`distro`（distro 名）、`ok`（布尔）、`exit_code`（整数或 `null`）、
`detail`（可判读摘要或 `null`，MUST NOT 编造）、`consecutive_failures`（连续探活失败次数，整数）、
`terminated_at`（ISO8601 或 `null`）、`terminate_count`（整数）。

探活 SHALL 与既有 `ensure-factor-research.sh` 调用相互独立：探活失败 MUST NOT 阻止启动器按既有语义
继续尝试拉起代理；探活路径 MUST NOT 修改研发产物（workspace / 候选文件 / 会话日志）与任何控制文件。
探活文件写失败 MUST NOT 影响代理存活保证（fail-safe）。

**不可用自愈**：下列三条件**必须同时成立**，任一不成立 MUST NOT 执行终止：

1. 探活失败且 `consecutive_failures ≥ 15`（2 分钟节拍下 ≈ 持续失败 ≥30 分钟）；
2. 状态快照 `collected_at_epoch` 距当前 ≥ 900 秒（采集链路同时停摆）；
3. 距上一次自愈动作 ≥ 1800 秒（限流）。

三条件成立时 SHALL 执行一次 `wsl --terminate <distro>`（使下一次节拍重新拉起 distro），并在探活
文件记录 `terminated_at` / `terminate_count`（自增）。

**判据依据（MUST 遵守的理由）**：历史 839 次节拍中 611 次（72.8%）以非零退出（WSL relay 超时），
而同期管线实际在跑 ⇒ 单次探活失败 MUST NOT 被当作「distro 已死」。条件 2 与条件 1 互斥于健康态
（健康时快照必然新鲜），故二者叠加才构成「不可用」证据，避免反复杀掉在跑的研发会话。

#### Scenario: 探活成功照常拉起

- **GIVEN** distro 可执行（`wsl -d rdagent -e /bin/true` 退出 0）
- **WHEN** 启动器节拍
- **THEN** 探活文件 `ok=true`、`exit_code=0`、`consecutive_failures=0`
- **AND** 既有 ensure 流程照常执行（行为不变），控制文件与研发产物未被触碰

#### Scenario: 探活失败但快照新鲜 → 只报告不终止

- **GIVEN** 探活连续失败 30 次，但快照 `collected_at_epoch` 距当前 60 秒（采集链路正常）
- **WHEN** 启动器节拍
- **THEN** 探活文件如实记录 `ok=false` 与 `consecutive_failures`
- **AND** MUST NOT 执行 `wsl --terminate`（健康证据优先，避免杀掉在跑的会话）

#### Scenario: 探活失败且快照同时停摆 → 自愈一次

- **GIVEN** 探活连续失败 ≥15 次、快照距当前 ≥900 秒、距上次自愈 ≥1800 秒
- **WHEN** 启动器节拍
- **THEN** 执行一次 `wsl --terminate <distro>`，探活文件记录 `terminated_at` 与自增的 `terminate_count`
- **AND** MUST NOT 触碰控制文件与研发产物

#### Scenario: 自愈限流

- **GIVEN** 上一次自愈发生在 300 秒前，其余条件仍成立
- **WHEN** 启动器节拍
- **THEN** MUST NOT 再次执行终止（`terminate_count` 不变）

#### Scenario: 探活文件不可写不阻断

- **GIVEN** 宿主 `data/` 桥暂时不可写
- **WHEN** 启动器节拍
- **THEN** 代理存活保证与既有 ensure 流程不受影响（MUST NOT 因探活写失败而中断）
- **AND** 后端按 `distro.available=false` 处理（fail-safe，MUST NOT 声称探活结论）

### Requirement: distro 探活只读派生

`GET /api/factor-research/status` 响应 SHALL 附加派生块 `distro`：探活文件路径 SHALL 在调用期读取
`FACTOR_RESEARCH_DISTRO_PROBE_PATH`（默认 `data/factor_research/distro_probe.json`）；文件存在且可
解析 → `available=true` 并透传 `probed_at` / `probed_at_epoch` / `distro` / `ok` / `exit_code` /
`detail` / `consecutive_failures` / `terminated_at` / `terminate_count`，附加派生 `age_seconds`
（自 `probed_at_epoch` 至请求时刻的秒数）与 `fresh`（`age_seconds ≤ 300`）；文件缺失或不可解析 →
`available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 或伪结论冒充。端点 MUST NOT 因
`distro.ok=false` 改变既有 `actual_state` / `control.honor` 判定，亦 MUST NOT 采取任何处置动作
（只披露，不重启、不终止、不写控制文件）。

#### Scenario: 探活文件透传与派生

- **GIVEN** 探活文件合法、`probed_at_epoch` 距请求 40 秒、`ok=false`、`consecutive_failures=3`
- **WHEN** 请求状态端点
- **THEN** `distro.available=true`、`distro.age_seconds≈40`、`distro.fresh=true`、`distro.ok=false`
- **AND** `actual_state` 与 `control.honor` 的判定不受影响

#### Scenario: 缺失与损坏如实区分

- **GIVEN** 探活文件不存在
- **WHEN** 请求状态端点
- **THEN** `distro.available=false` + `reason=missing`

- **GIVEN** 探活文件存在但内容非合法 JSON
- **WHEN** 请求状态端点
- **THEN** `distro.available=false` + `reason=corrupt`

#### Scenario: 探活陈旧不冒充结论

- **GIVEN** 探活文件合法但 `probed_at_epoch` 距请求 900 秒
- **WHEN** 请求状态端点
- **THEN** `distro.available=true` 且 `distro.fresh=false`
- **AND** 前端 MUST NOT 据陈旧探活展示「distro 不可用」结论
