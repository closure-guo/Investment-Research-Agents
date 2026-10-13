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

