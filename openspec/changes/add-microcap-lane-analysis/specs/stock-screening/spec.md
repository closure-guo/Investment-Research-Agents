## ADDED Requirements

### Requirement: 小票池每日自动分析

系统 SHALL 在每个交易日小票通道批次落库成功后，于 16:47 自动对 microcap 档池按 rank
取前 N（`AUTO_POOL_MICROCAP_MAX_SYMBOLS`，默认 10，上限 100）执行与主档自动分析同构的
五层深度分析编排：

- 读取 `latest_successful_pool(pool='microcap')`，池新鲜度规则与主档自动分析一致
  （当日或前一交易日补跑池可接受，更旧 `skipped_stale_pool`）；
- 与主档自动批次同日重叠标的 SHALL 跳过并顺延补位：去重以 `(trade_date, symbol)`
  全局幂等键为准（不按 pool 过滤）——当日已有 queued/completed 运行行的标的不重复
  建会话、不重复入队，取 rank 下一名补足 N；补位不足 N 时 SHALL 如实披露实际数量，
  MUST NOT 重复分析凑数；
- 时效性重校验（停牌/一字板盘前移除）与失败重派语义（重派建全新会话、旧会话保留审计）
  SHALL 与主档自动分析一致；
- 会话事件 SHALL 携带 `source="auto_pool_microcap"`，会话标题以「小票池自动分析」
  后缀与主档批次区分；
- 入队 SHALL 复用既有 AnalyzeQueue 与并发上限，MUST NOT 提高并发；
- 开关 `AUTO_POOL_MICROCAP_ENABLED`（默认开启），独立于主档开关；
- 重叠标的处置 SHALL 为「跳过 + 顺延补位」，SHALL NOT 复用主档批次的分析结果
  （裁定与重开条件见设计文档 §1.2：同日日主判定 lane 无感，复用行会被
  `duplicate_of_day` 关闭或反向污染主档记录；16:47 时主档分析多在途，无结果可复）。
- 本需求 SHALL NOT 改变「观察通道声明」的通道语义：选股通道本身仍不产生订单；
  小票纸面账本的持仓与订单属执行通道（trade-execution），经独立账本披露，页面文案
  更新留待后续 UI delta。

#### Scenario: 跨池重叠跳过并顺延补位

- **GIVEN** microcap 池 rank 1–10 中 4 只已在当日主档自动批次产生 queued/completed 运行行
- **WHEN** 小票自动分析编排执行
- **THEN** 这 4 只被跳过（不建会话、不入队），从 rank 11 起顺延取 4 只补足 10
- **AND** 摘要 `selected=10` 且跳过数如实披露

#### Scenario: 补位不足如实披露

- **GIVEN** microcap 池仅 6 只、其中 2 只当日已被（任意批次）分析
- **WHEN** 编排执行
- **THEN** 新建 4 只会话并如实披露 `selected=4`
- **AND** MUST NOT 重复分析已分析标的凑数

#### Scenario: 池陈旧跳过

- **GIVEN** microcap 档最近成功批次日期早于前一交易日
- **WHEN** 编排执行
- **THEN** 返回 `skipped_stale_pool`，不建会话、不入队

#### Scenario: 幂等与失败重派同主档

- **GIVEN** 小票批某标的运行行已为 queued/completed
- **WHEN** 编排重跑
- **THEN** 跳过该标的，不重复建会话、不重复烧分析
- **AND** failed/dispatch_failed/session_failed 行重派时创建全新会话入队，旧会话原样保留

### Requirement: pool_analysis_runs 池归属列

`pool_analysis_runs` SHALL 增加 `pool TEXT NOT NULL DEFAULT 'main'` 归属列（幂等
ALTER TABLE，存量行自动 main，无需回填）；列只作归属标注，MUST NOT 进主键。

- 主档自动分析批次写行 SHALL 落 `pool='main'`（默认或显式）；小票自动分析批次写行
  SHALL 显式落 `pool='microcap'`；
- 同日同标的跨池去重 SHALL 以 `(trade_date, symbol)` 全局键为准（pool 不参与主键）；
- 终态回写与对账 SHALL 覆盖两池行（lane 无关，既有行为扩展）。

#### Scenario: 小票批写行带池归属

- **WHEN** 小票自动分析为标的 X 建运行行
- **THEN** 该行 `pool='microcap'`
- **AND** 同日主档批次行为 `pool='main'`

#### Scenario: 幂等键不含 pool

- **GIVEN** 当日主档批次已为 (D, S) 建行
- **WHEN** 小票批处理同一 (D, S)
- **THEN** 按 (trade_date, symbol) 命中既有行并跳过
- **AND** MUST NOT 因 pool 不同而二次建行
