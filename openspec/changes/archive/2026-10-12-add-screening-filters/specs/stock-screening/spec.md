# stock-screening delta: add-screening-filters

## ADDED Requirements

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
