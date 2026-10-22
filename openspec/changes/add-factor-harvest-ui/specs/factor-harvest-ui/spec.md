# Delta: factor-harvest-ui（add-factor-harvest-ui）

## ADDED Requirements

### Requirement: 收割链路只读端点

系统 SHALL 提供 `GET /api/factor-research/harvest`，返回 `available` / `job` / `progress` / `funnel` /
`recent_admitted` / `recent_rejected` 六块。端点 MUST 只读：不得写状态文件、候选文件、因子库或任何队列；
状态文件或候选文件缺失/不可解析时 SHALL 返回 `available=false` 且 `progress`/`funnel` 为 `null`，
MUST NOT 以 500 或编造数字冒充。

#### Scenario: 正常返回

- **GIVEN** 状态文件与候选文件均存在且合法
- **WHEN** 调用 `GET /api/factor-research/harvest`
- **THEN** 返回 `available=true`，`funnel.candidates` 等于候选数、`funnel.processed` 等于已处理数、
  `progress.pending` 等于候选文件中未处理数
- **AND** `funnel.reached_gates = admitted + rejected`，`rejected_by_gate` 只统计 `status=rejected`

#### Scenario: 文件缺失如实降级

- **GIVEN** 状态文件缺失
- **WHEN** 调用端点
- **THEN** 返回 200 + `available=false`、`progress=null`、`funnel=null`、`recent_admitted=[]`
- **AND** `job` 块仍尽力返回（不可得字段为 `null`）

#### Scenario: 运行窗口口径

- **GIVEN** `factor_harvest_daily` 最近一次运行 `started_at=T0`
- **WHEN** 调用端点
- **THEN** `progress.batches_done` 等于账本中 `created_at >= T0` 的不同 `batch_id` 数
- **AND** `progress.admitted_in_run` / `rejected_in_run` 只统计 $T0$ 之后的账本行
- **AND** `progress.eta_s` 按 `剩余 / (本轮处理数 / 已运行时长)` 估算；不足一批或时长为 0 → `null`

### Requirement: 前端收割链路区块
`/factor-research` 页 SHALL 新增「因子收割链路」区块，含：批次进度条（`batches_done/batch_limit`）、
已处理/剩余/本轮入库与拒收/预计完成、四段漏斗（候选/已处理/到闸/入库，含相对候选占比）、
按闸拒收分布、最近入库与最近拒收两列。区块 MUST 只读，不提供任何写操作入口。
区块取数失败 SHALL 仅降级本区块（显示错误与重试），MUST NOT 影响主监控区块。

#### Scenario: 渲染进度与漏斗

- **GIVEN** 端点返回 `available=true`、`progress.batches_done=2`、`funnel.candidates=820`、`funnel.admitted=7`
- **WHEN** 打开 `/factor-research`
- **THEN** 区块显示「批次 2/12」「剩余 <pending>」「候选 820」「入库 7」
- **AND** 渲染拒收分布与最近入库/拒收（无数据时显示「暂无」）

#### Scenario: 不可用空态

- **GIVEN** 端点返回 `available=false`
- **WHEN** 打开页面
- **THEN** 区块显示「收割数据未接入」空态
- **AND** 主监控区块仍正常渲染

#### Scenario: 运行中批内活性

- **GIVEN** `job.status=running`、`progress.started_at=T0`
- **WHEN** 打开页面并停留
- **THEN** 区块显示秒级时钟「本批/本轮进行中 · 已 X（进度按批落盘）」
- **AND** 若心跳文件新鲜（`in_batch` 且 `age_s < 600`），SHALL 显示「本批 n/total · 阶段 · 已 X」逐候选进度
- **AND** 时钟每秒更新，MUST NOT 因为批次数字未变而让区块看起来静止

#### Scenario: 区块级失败不拖垮整页

- **GIVEN** `/api/factor-research/harvest` 请求失败而主状态端点成功
- **WHEN** 打开页面
- **THEN** 主监控区块正常展示
- **AND** 收割区块显示加载失败与重试按钮
