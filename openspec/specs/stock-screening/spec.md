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

#### Scenario: 贡献分解勾稽

- **GIVEN** 某日 L2 打分产出候选 C，综合分 1.823
- **WHEN** 汇总 C 的 factor_contribs
- **THEN** 各 contribution 之和与 1.823 的差 ≤ 1e-9
- **AND** 每条 contribution 含 factor_id、zscore、weight 三字段

### Requirement: 打分确定性

同一交易日、同一因子库状态下的打分结果 SHALL 完全确定（无随机性、无 LLM 参与）；两次独立执行产出逐标的一致。

#### Scenario: 重复执行一致

- **GIVEN** 同一快照数据与因子库快照
- **WHEN** 连续执行两次 L2 打分
- **THEN** 两份 Top-N 列表的标的、排序、综合分逐项相等

### Requirement: 选股日批编排

系统 SHALL 在每个交易日快照更新成功后依次执行 L1 过滤 → L2 打分 → L3 粗筛 → 精选池落库；任一步骤失败时该批次 SHALL 标记失败、不产出精选池，并触发前端横幅告警；L2 全量名单（含未入选标的）SHALL 每日完整落库。

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

