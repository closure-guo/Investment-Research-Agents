## ADDED Requirements

### Requirement: 因子研发监控页僵死披露

`/factor-research` 监控页 SHALL 如实披露「采集正常但会话无进展」这一状态：当端点
`progress.stalled=true` 时，页面 SHALL 在进度区展示醒目提示
（`data-testid="factor-research-stall-banner"`），文案 SHALL 含无进展时长（由
`progress.log_age_seconds` 经既有 `formatDurationS` 口径格式化，如 3600 秒 → 「1小时0分」）；
同时页面 MUST NOT 把快照里的步骤进度读数当作**当前**进度展示——`step_elapsed_s` / `step_eta_s`
取自日志末行，日志冻结后即为旧值，故 SHALL 改为标注该读数的时刻（如「进度读数来自 N 分钟前的
日志」），MUST NOT 继续渲染「预计剩余 X」冒充进展。

当 `progress.stalled=false` 时，页面 MUST NOT 展示该提示（不无端告警）。当
`progress.log_age_seconds=null`（旧版采集器未上报新鲜度）时，页面 MUST NOT 展示该提示，
亦 MUST NOT 以「进度正常」等断言冒充已验证——未上报只能表现为「不可判定」。

僵死提示与既有陈旧快照提示语义互斥且 MUST NOT 互相替代：`stale=true`（采集器/代理停摆）时
按既有陈旧披露展示，此时 `stalled=false`（实态不可证）；`stalled=true` 专指采集链路正常、
会话进程存活但日志停更。僵死提示 MUST NOT 提供任何可点击的处置入口（零命令面不变），解除僵死
只能经既有控制通道由人发起。

#### Scenario: 僵死提示可见且进度不冒充

- **GIVEN** 端点返回 `available=true`、`stale=false`、`actual_state="running"`、
  `progress.log_age_seconds=3600`、`progress.stalled=true`，且快照含
  `step_elapsed_s=603`、`step_eta_s=905`
- **WHEN** 打开监控页
- **THEN** 展示 `factor-research-stall-banner`，文案含「无进展 1小时0分」（由 3600 秒经 `formatDurationS` 格式化）
- **AND** 步骤进度读数被标注为来自 60 分钟前的日志
- **AND** MUST NOT 继续展示「预计剩余 15 分钟」这类当前进度断言
- **AND** 横幅内无可点击的处置入口

#### Scenario: 未僵死不提示

- **GIVEN** 端点返回 `progress.stalled=false`（`log_age_seconds=42`）
- **WHEN** 打开监控页
- **THEN** MUST NOT 展示 `factor-research-stall-banner`
- **AND** 进度区照常展示步骤进度

#### Scenario: 旧版采集器未上报新鲜度

- **GIVEN** 端点返回 `progress.log_age_seconds=null`、`progress.stalled=false`，且会话存活
- **WHEN** 打开监控页
- **THEN** MUST NOT 展示 `factor-research-stall-banner`
- **AND** MUST NOT 展示「进度正常」等已验证断言（未上报 = 不可判定）

#### Scenario: 与陈旧快照披露互不替代

- **GIVEN** 端点返回 `stale=true`（快照陈旧）且 `progress.stalled=false`
- **WHEN** 打开监控页
- **THEN** 展示既有「数据已陈旧」提示
- **AND** MUST NOT 以僵死提示替代或遮蔽陈旧提示
