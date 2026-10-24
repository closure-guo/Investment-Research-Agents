# factor-harvest-ui Specification

## Purpose
TBD - created by archiving change add-factor-harvest-ui. Update Purpose after archive.
## Requirements
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
- **AND** `progress.eta_s` 仅在最近一次运行 `status=running` 时按 `剩余 / (本轮处理数 / 已运行时长)`
  估算；运行已收尾（`ok` / `failed` / `skipped-disabled`）、不足一批或时长为 0 → `null`

#### Scenario: 运行结束后不冒充预计

- **GIVEN** 最近一次运行 `status=ok`、`finished_at=T1`，候选文件中仍存在 `T1` 之后新增的未处理候选
- **WHEN** 调用端点
- **THEN** `progress.eta_s=null`（已收尾的运行 MUST NOT 外推剩余时间：本轮处理数已冻结、时长仍在增长，
  外推结果只增不减）
- **AND** `progress.pending` 仍如实返回队列未处理数，该数值属于**下一次**运行，不是本次运行的尾巴

### Requirement: 前端收割链路区块
`/factor-research` 页 SHALL 新增「因子收割链路」区块，含：队列消化进度条（`funnel.processed` /
`funnel.candidates`，仅运行中）、已处理/剩余/本轮入库与拒收/预计完成（仅运行中）、四段漏斗
（候选/已处理/到闸/入库，含相对候选占比）、按闸拒收分布、最近入库与最近拒收两列。区块 MUST 只读，
不提供任何写操作入口。区块取数失败 SHALL 仅降级本区块（显示错误与重试），MUST NOT 影响主监控区块。

进度条口径 MUST 与运行态绑定：候选文件由 RD-Agent 持续追加，`processed / candidates` 是**全局队列
消化率**而非单轮完成度——收尾后新候选入场会让该比值回落，此时继续渲染即读成「没跑完」，与「已完成」
徽标自相矛盾（同 T7 去掉 `/batch_limit` 分母的判据）。

区块 SHALL 区分「运行中」与「已收尾」两种口径，MUST NOT 把本轮窗口统计与全局队列并列成同一句进度：

- 运行中（`job.status=running`）：队列消化进度条（`processed / candidates`）、批次进度
  （`batches_done/batch_limit`）、本轮入库与拒收、`eta_s` 非空时的「预计还需」、秒级活性时钟。
- 已收尾（`job.status` 为 `ok`/`failed` 等）：本轮按批量与入库/拒收汇总（批量 MUST NOT 带
  `/batch_limit` 分母——该值是单次运行上限，不是待办总数，带上会被读成「没跑完」）；队列剩余
  SHALL 表述为待**下一次**自动跑的读数并给出 `job.next_fire_time`；MUST NOT 渲染「预计还需」，
  即使端点仍给出 `eta_s`；MUST NOT 渲染进度条——队列消化率是全局读数（收尾后仍随 RD-Agent
  新候选入场回落），渲染即读成「没跑完」。

#### Scenario: 渲染进度与漏斗

- **GIVEN** 端点返回 `available=true`、`job.status=running`、`progress.batches_done=2`、`funnel.candidates=820`、`funnel.admitted=7`
- **WHEN** 打开 `/factor-research`
- **THEN** 区块显示「批次 2/12」「剩余 <pending>」「候选 820」「入库 7」
- **AND** 渲染拒收分布与最近入库/拒收（无数据时显示「暂无」）

#### Scenario: 运行中渲染队列消化进度条

- **GIVEN** `job.status=running`、`funnel.candidates=950`、`funnel.processed=893`
- **WHEN** 打开 `/factor-research`
- **THEN** 渲染进度条（`factor-research-harvest-bar`），宽度为 `processed / candidates`（94%）

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

#### Scenario: 已收尾不渲染预计还需

- **GIVEN** `job.status=ok`、`finished_at` 非空、`progress.pending=36`、`progress.eta_s=2310`、
  `progress.batches_done=5`、`job.next_fire_time` 非空
- **WHEN** 打开 `/factor-research`
- **THEN** 区块显示「已完成」与「本轮 5 批 · 入库 x · 拒收 y」，且 MUST NOT 出现 `/12` 形式的上限分母
- **AND** 队列剩余显示为待**下一次**自动跑的读数（含 `next_fire_time` 时刻）
- **AND** MUST NOT 出现「预计还需」字样（端点给出 `eta_s` 也不渲染）

#### Scenario: 已收尾不渲染进度条

- **GIVEN** `job.status=ok`、`funnel.candidates=950`、`funnel.processed=896`（收尾后 RD-Agent 又追加 54 条候选）
- **WHEN** 打开 `/factor-research`
- **THEN** 区块 MUST NOT 渲染进度条（`factor-research-harvest-bar` 不存在）——94% 的全局读数配「已完成」徽标即读成「没跑完」
- **AND** 队列未消化读数由「已处理 896」与「队列剩余 54 · 待下次 …自动跑」如实承担

#### Scenario: 区块级失败不拖垮整页

- **GIVEN** `/api/factor-research/harvest` 请求失败而主状态端点成功
- **WHEN** 打开页面
- **THEN** 主监控区块正常展示
- **AND** 收割区块显示加载失败与重试按钮

