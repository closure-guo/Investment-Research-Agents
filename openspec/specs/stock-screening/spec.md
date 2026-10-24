# stock-screening Specification

## Purpose
TBD - created by archiving change add-screening-filters. Update Purpose after archive.
## Requirements
### Requirement: L1 确定性硬过滤

系统 SHALL 提供纯函数过滤层，对指定交易日的快照全量记录逐标的判定通过或剔除；剔除时 SHALL 返回结构化原因码，规则至少包含：`ST_FLAG`（ST/*ST）、`SUSPENDED`（停牌）、`IPO_RECENT`（上市 < 60 交易日）、`LIMIT_LOCKED`（一字涨停或一字跌停）、`ILLIQUID`（近 20 日日均成交额低于配置阈值，默认 5000 万元）。

#### Scenario: ST 标的剔除

- **GIVEN** 快照中标的 X 当日状态含 ST 标记
- **WHEN** 执行 L1 过滤
- **THEN** X 被剔除且原因码为 `ST_FLAG`

#### Scenario: 上市天数边界

- **GIVEN** 标的 A 上市满 59 个交易日、标的 B 上市满 60 个交易日
- **WHEN** 执行 L1 过滤
- **THEN** A 被剔除（原因码 `IPO_RECENT`），B 不被该规则剔除

### Requirement: 约束单一来源

过滤层使用的约束常量（上市天数阈值、成交额阈值等）SHALL 定义于 `screening/constraints.py`；后续交易执行层的风控门禁 SHALL import 该模块复用约束定义，禁止复制常量到执行层代码。

#### Scenario: 常量单点修改

- **GIVEN** 实现完成后的代码库
- **WHEN** 将 `constraints.py` 中流动性阈值从 5000 万改为 8000 万
- **THEN** 过滤层行为随之改变，且无需修改任何其他文件

### Requirement: L2 因子打分排序

系统 SHALL 对 L1 过滤通过的标的，基于因子库全部 active 因子做横截面打分并输出 Top-N（默认 100，可配置）候选列表；每个候选 SHALL 携带 `factor_contribs` 贡献分解，且全部贡献之和 SHALL 等于综合分（浮点容差 1e-9）。

可打分因子的权重 SHALL 按「方向 × 幅度」构成：方向 = 该因子 IC 均值的符号；幅度在近 60 个交易日 IC 样本数 ≥ 30（常量 `MIN_SAMPLES_FOR_PROPORTIONAL_WEIGHTS`）时正比于 |IC 均值|，样本数 < 30 时取等幅（所有低样本因子幅度相等）。权重 SHALL 按幅度之和归一；IC 均值为 0（含 |IC 均值| < 1e-12 的数值零——其符号无信息量）的因子权重为 0（不参与幅度归一分母）；样本数缺失（未提供 `ic_counts`）SHALL 视为 0（等幅）。非有限 IC 均值与幅度总和 < 1e-12 的拒绝语义维持不变。

#### Scenario: 贡献分解勾稽

- **GIVEN** 某日 L2 打分产出候选 C，综合分 1.823
- **WHEN** 汇总 C 的 factor_contribs
- **THEN** 各 contribution 之和与 1.823 的差 ≤ 1e-9
- **AND** 每条 contribution 含 factor_id、zscore、weight 三字段

#### Scenario: 低样本因子等幅

- **GIVEN** 两个可打分因子 A、B，IC 均值分别为 +0.10 与 -0.20，两者的近 60 交易日 IC 样本数均为 12（< 30）
- **WHEN** 计算权重
- **THEN** |w_A| == |w_B|（等幅），w_A > 0 且 w_B < 0（方向取 IC 均值符号）

#### Scenario: 高样本因子维持比例

- **GIVEN** 两个可打分因子 A、B，IC 均值分别为 +0.10 与 +0.20，两者的 IC 样本数均 ≥ 30
- **WHEN** 计算权重
- **THEN** w_A == 0.10 / 0.30 且 w_B == 0.20 / 0.30（|IC 均值| 比例与原式一致）

#### Scenario: 混合样本各自适用

- **GIVEN** 因子 A 样本数 40（IC 均值 +0.30）、因子 B 样本数 15（IC 均值 -0.05）
- **WHEN** 计算权重
- **THEN** 幅度为 A=0.30、B=1.0（B 等幅），按 1.30 归一后 w_A = 0.30/1.30、w_B = -1.0/1.30
- **AND** A 与 B 的样本数互换时（A=15、B=40），结果按同一规则随之互换

### Requirement: 打分确定性

同一交易日、同一因子库状态下的打分结果 SHALL 完全确定（无随机性、无 LLM 参与）；两次独立执行产出逐标的一致。

#### Scenario: 重复执行一致

- **GIVEN** 同一快照数据与因子库快照
- **WHEN** 连续执行两次 L2 打分
- **THEN** 两份 Top-N 列表的标的、排序、综合分逐项相等

### Requirement: 选股日批编排

系统 SHALL 在每个交易日快照更新成功后依次执行 L1 过滤 → L2 打分 → L3 粗筛 → 精选池落库（无可打分因子时 L2 SHALL 按「无可打分因子降级通道」显式跳过并披露，MUST NOT 静默等权）；任一步骤失败时该批次 SHALL 标记失败、不产出精选池，并触发前端横幅告警；L2 全量名单（含未入选标的）SHALL 每日完整落库（降级批次为降级排序名单，同一落库契约）。

#### Scenario: 中途失败不产残缺池

- **GIVEN** 某日 L3 粗筛因 LLM 网关整体不可用而失败
- **WHEN** 日批结束
- **THEN** 当日精选池为空且批次状态为 failed
- **AND** 前端选股页展示最近一个成功批次的精选池并标注其日期

### Requirement: 信号时效性校验

精选池标的在进入五层深度分析前，系统 SHALL 以其最新可得状态重新校验（停牌/一字涨跌停/新发重大事件）；校验不通过者 SHALL 从本次分析名单移除并记录移除原因。

#### Scenario: 盘前停牌移除

- **GIVEN** 精选池标的 Z 在深度分析触发前发布停牌公告
- **WHEN** 时效性校验执行
- **THEN** Z 被移除，原因码 `SUSPENDED`，其余标的不受影响

### Requirement: L3 LLM 排雷粗筛

系统 SHALL 对 L2 Top-N 候选逐只执行 LLM 粗筛（quick profile），输入含综合分、因子贡献摘要与最近事件；输出 SHALL 满足合同：`verdict ∈ {keep, drop}` 且 `reason ≤ 50 字`；单只调用失败 SHALL 按 keep 降级处理并记 trace warning，不阻断本批其他标的。

#### Scenario: 事件雷剔除

- **GIVEN** 候选 X 综合分排名第 5，但最近事件含「收到证监会立案告知书」
- **WHEN** 执行 L3 粗筛
- **THEN** X 的 verdict 为 drop，reason 提及立案
- **AND** 该 keep/drop 决策落库

#### Scenario: 单只失败降级

- **GIVEN** 一批 50 只候选，其中 1 只的 LLM 调用抛出 typed error
- **WHEN** 批次执行
- **THEN** 其余 49 只正常产出 verdict；失败标的按 keep 处理且 trace 含 warning

### Requirement: 在榜稳定性门槛

标的 SHALL 连续 3 个交易日（可配置）处于 L2 Top-N 内方可进入精选池；中间断档 SHALL 重新计数。

#### Scenario: 断档重新计数

- **GIVEN** 标的 Y 在第 1、2 日在榜，第 3 日跌出 Top-N，第 4、5 日重新在榜
- **WHEN** 第 5 日评估精选池资格
- **THEN** Y 的连续在榜计数为 2，不进入精选池

### Requirement: 日批排程与手动补跑

选股日批 SHALL 注册为统一日批任务（与判定 / 盯市 / 快照 / 结算链同一注册表），排程为工作日 15:40（Asia/Shanghai，晚于行情快照任务 15:30），并 SHALL 经统一通道提供运行历史（含 `source=scheduled/manual`）与手动补跑；快照未就绪时该批次 SHALL fail loud（批次标记 failed、不产出精选池），MUST NOT 以跳过或空池冒充成功。

#### Scenario: 排程注册可见

- **GIVEN** 调度器已启动
- **WHEN** 请求日批运行状态接口
- **THEN** 返回任务清单含选股任务的排程（工作日 15:40 Asia/Shanghai）与下次触发时间
- **AND** 其运行历史含每次触发的开始/结束时间与结果摘要（`l1_passed/l2_top/l3_keep/pool_size` 或错误信息）

#### Scenario: 快照未就绪 fail loud

- **GIVEN** 当日快照缺失（快照库为空或数据日期早于当日）
- **WHEN** 选股日批触发
- **THEN** 该批次状态为 failed、当日精选池零行
- **AND** 运行历史摘要含可判读的失败原因

#### Scenario: 手动补跑

- **GIVEN** 当日选股日批失败
- **WHEN** 运维经统一通道手动补跑
- **THEN** 以 `source=manual` 落新运行行并执行同一编排
- **AND** 成功批次恢复产出精选池

### Requirement: 批次历史查询

系统 SHALL 提供选股批次历史只读接口：按交易日期倒序返回近 N 批次，字段含 日期、状态（ok / failed）、各层计数（`l1_passed` / `l2_top` / `l3_keep`）、精选池数量（`pool_size`）、选择模式（`factor` / `fallback`）、错误信息与起止时间；库为空时 SHALL 返回空列表而非错误；接口 SHALL NOT 暴露写操作。

#### Scenario: 历史字段齐备

- **GIVEN** 库中含 1 条成功批次与 1 条失败批次
- **WHEN** 请求批次历史
- **THEN** 返回两条（日期倒序），成功批次含各层计数与池数、失败批次含错误信息

#### Scenario: 空库不报错

- **GIVEN** 尚无任何批次
- **WHEN** 请求批次历史
- **THEN** 返回 200 与空列表

### Requirement: 无可打分因子降级通道

系统 SHALL 在 L2 打分器拒绝打分的条件成立时（无 active 因子，或全部 active 因子无有效 IC 记录，
或全部 active 因子的 IC 样本不足以估计权重）进入显式降级通道：单因子在近 60 个交易日的 RankIC
记录数少于 **10** 条时 SHALL 不参与打分（权重按 0 处理），并在批次日志披露被排除的因子 id 与样本数；
全部因子均被排除时 L2 SHALL 被显式跳过（MUST NOT 静默等权、MUST NOT 伪造因子权重或 IC、MUST NOT
用 1–3 个样本估计权重）；系统 SHALL 以确定性流动性过渡排序产出 Top-N 候选——近 20 日日均成交额降序、
同额按标的代码升序——并照常落 L2 全量名单（在榜稳定性门槛照常累计）、照常执行 L3 粗筛与精选池落库；
批次 SHALL 标注选择模式 `selection_mode ∈ {factor, fallback}` 并经批次历史接口、池接口与选股页横幅
三处披露，披露文案 SHALL 明示该排序为流动性过渡规则、非因子信号。因子库恢复有可打分因子（含样本数
达标）后，后续批次 SHALL 自动回到 `factor` 路径（降级为死分支，无配置开关）。降级通道 MUST NOT
吞掉其他失败：L1/L3/池等其余任一步骤失败时批次仍 SHALL 记 failed、不产出精选池。

池接口 SHALL 同时返回 `factor_signal` 块：逐 active 因子给出 `{id, ic_samples, min_samples, eligible, expected_eligible_date}`，并给出批次级 `{selection_mode, reason, expected_factor_mode_date}`；`reason` SHALL 逐字说明被排除原因（如「IC 样本不足（门槛 10）」），`expected_factor_mode_date` SHALL 取各因子预计达标日的**最早值**（按工作日推算、不含节假日；已达标或无 IC 记录时为 `null`）。样本数与日期 MUST NOT 以估算值冒充精确读数——无 IC 记录时 `ic_samples=0`、日期为 `null`。

(Previously: 触发条件仅为「无 active 因子，或全部 active 因子无有效 IC 记录」；只要存在任意 IC 记录即视为可打分，3 个样本也会被用于估计权重；池接口只披露 selection_mode 与文案，不披露逐因子样本与预计恢复日期。)

#### Scenario: 库空降级产出

- **GIVEN** 因子库无 active 因子、当日快照就绪
- **WHEN** 选股日批运行
- **THEN** 批次状态为 ok 且 `selection_mode=fallback`
- **AND** L2 名单与精选池照常产出（候选按近 20 日日均成交额降序，`factor_contribs` 为空）

#### Scenario: IC 样本不足降级

- **GIVEN** 因子库有 1 个 active 因子，但近 60 个交易日只有 3 条 RankIC 记录
- **WHEN** 选股日批运行
- **THEN** 批次状态为 ok 且 `selection_mode=fallback`
- **AND** 日志披露该因子因样本不足被排除（含因子 id 与样本数）
- **AND** L2 名单与精选池照常产出，`factor_contribs` 为空

#### Scenario: 单因子样本不足被排除

- **GIVEN** 因子库有 2 个 active 因子，A 有 60 条 RankIC 记录、B 只有 3 条
- **WHEN** 选股日批运行
- **THEN** 批次 `selection_mode=factor`
- **AND** 综合分只含 A 的贡献分解，B 不出现在 `factor_contribs`

#### Scenario: 三处披露

- **GIVEN** 最近成功批次为降级批次
- **WHEN** 读取池接口与批次历史接口
- **THEN** 两处均含 `selection_mode=fallback`，池接口另含明示「非因子信号」的披露文案
- **AND** 选股页展示降级披露横幅

#### Scenario: 池接口披露逐因子就绪度

- **GIVEN** 3 个 active 因子分别有 4/3/0 条 IC 记录，门槛 10
- **WHEN** 读取池接口
- **THEN** `factor_signal.factors` 逐因子给出 `ic_samples` 与 `eligible=false`
- **AND** `expected_factor_mode_date` 为最早可达标日；`reason` 含「IC 样本不足」字样

#### Scenario: 恢复自动切回

- **GIVEN** 因子库已含可打分因子（active 且 IC 样本数 ≥ 10、Σ|IC 均值| > 0）
- **WHEN** 选股日批运行
- **THEN** 批次 `selection_mode=factor` 且 L2 走因子打分路径（Top-N 携带贡献分解）

#### Scenario: 降级不吞失败

- **GIVEN** 因子库无 active 因子，且 L3 粗筛因 LLM 网关整体不可用而失败
- **WHEN** 选股日批运行
- **THEN** 批次状态为 failed、当日精选池零行（fail-closed 语义不因降级通道放宽）

### Requirement: 因子榜与流动性榜秩相关披露

`selection_mode=factor` 的选股批次 SHALL 在批次 summary 中携带 `factor_liquidity_rank_corr`（对同一 L1 通过标的集，因子综合分排序与近 20 日日均成交额降序排序的 Spearman 秩相关，取值 [-1, 1]）与 `rank_corr_samples`（参与计算的标的数，等于该批 L1 通过数）；秩相关 SHALL 以秩变换 + Pearson 实现，MUST NOT 引入 scipy 依赖。`selection_mode=fallback` 的批次 MUST NOT 携带该字段。披露 MUST NOT 影响排序本身（只读读数，零回溯）。

#### Scenario: factor 批次披露读数

- **GIVEN** 某选股日批以 `selection_mode=factor` 完成且 L1 通过 3,986 只
- **WHEN** 读取该批次 summary
- **THEN** `factor_liquidity_rank_corr` 为 [-1, 1] 内有限浮点数，`rank_corr_samples` = 3986
- **AND** 批次历史接口返回同一对字段

#### Scenario: fallback 批次不携带

- **GIVEN** 某选股日批因无可打分因子降级为 `selection_mode=fallback`
- **WHEN** 读取该批次 summary
- **THEN** 不含 `factor_liquidity_rank_corr` 与 `rank_corr_samples` 字段

#### Scenario: 两榜完全一致时读数为 1

- **GIVEN** L1 通过集上因子综合分降序与近 20 日日均成交额降序给出完全相同的标的次序
- **WHEN** 计算披露读数
- **THEN** `factor_liquidity_rank_corr` = 1.0（浮点容差 1e-9）

