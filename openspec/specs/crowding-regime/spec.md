# crowding-regime Specification

## Purpose
TBD - created by archiving change add-crowding-regime-positioning. Update Purpose after archive.
## Requirements
### Requirement: 拥挤度指标日批

系统 SHALL 在每交易日全市场快照更新完成后计算并落库四项拥挤度指标：① **小市值组成交额占比**——按当日流通市值升序取前 30%（`SMALL_CAP_QUANTILE` 可配置）标的的成交额之和 ÷ 全市场成交额（停牌与零成交标的剔除；流通市值 SHALL 取真实市值口径，MUST NOT 以成交额等替代口径冒充市值分组；取数源 SHALL 按「快照同源 spot 优先 → 腾讯行情批量兜底（真实市值字段）」链式尝试，采用兜底源时 SHALL 在指标行留痕 `cap_source`）；② **涨跌家数**——全市场涨/跌/平家数（按当日涨跌幅符号）；③ **涨停/跌停家数**——按快照封板状态字段（`limit_up_sealed` / `limit_down_sealed`）计数；④ **全市场成交额**合计。每项指标 SHALL 附最近 250 个交易日（可配置）窗口内的历史分位；历史样本少于 60 个交易日时分位 SHALL 为 null 并注明 `insufficient_history`。落库 SHALL 以交易日为唯一键（同日重跑幂等覆盖）。快照不可用或指标不可计算时 SHALL NOT 产行并留痕原因，MUST NOT 以 0 或估算值冒充。

#### Scenario: 指标口径确定

- **GIVEN** 一份构造的最小快照（已知市值、成交额、涨跌幅与封板状态）
- **WHEN** 执行指标日批计算
- **THEN** 四指标与手算值一致（含小市值分组的成员判定）

#### Scenario: 分位样本不足如实置空

- **GIVEN** 历史样本仅 30 个交易日（门槛 60）
- **WHEN** 计算历史分位
- **THEN** 分位为 null 且注明 `insufficient_history`
- **AND** 指标当日值照常落库

#### Scenario: 同日重跑幂等

- **GIVEN** 当日已产出一行指标记录
- **WHEN** 同日重跑日批
- **THEN** 仍为单行，值被覆盖为最新一次计算结果

#### Scenario: 快照不可用不产行

- **GIVEN** 快照库为空或当日快照缺失
- **WHEN** 日批运行
- **THEN** 当日不产行且留痕原因（MUST NOT 以 0 值冒充）

#### Scenario: 市值兜底源留痕

- **GIVEN** 快照同源 spot 不含流通市值列而腾讯行情批量源可用
- **WHEN** 日批计算
- **THEN** 指标行照常产出，且该行 `cap_source` 留痕为 `tencent`
- **AND** 两源均不可用时当日不产行并留痕 `cap_data_missing`

### Requirement: regime 判定与总仓位系数

系统 SHALL 以确定性纯函数由最新指标记录与配置（`config/regime.json`：分位阈值 → 状态、状态 → 系数映射、陈旧口径）产出判定：输出 SHALL 为 `{state, multiplier, reasons}`，`state ∈ {normal, elevated, danger}`、`multiplier ∈ [0, 1]`、`reasons` SHALL 含触发指标名与读数。默认映射 `normal → 1.0 / elevated → 0.5 / danger → 0.0`；阈值与映射 SHALL 全部可配置。v1 触发规则：小市值组成交额占比分位或全市场成交额分位任一 ≥ 对应阈值即触发该状态（涨跌家数/涨停家数进 `reasons` 与展示，不参与 v1 状态触发）。配置加载 SHALL 与撮合配置同范式：缺文件回落内置默认并留 WARN（缺文件为部署常态）；文件存在但损坏（JSON 非法 / 缺键 / 类型不符 / 系数越界）fail loud。陈旧口径 SHALL 为：数据日期距判定时刻超过 7 个自然日即判陈旧（可配置）。判定 MUST NOT 调用 LLM；同输入 MUST 产出同输出。

#### Scenario: 过热判定

- **GIVEN** 最新指标的小市值成交占比分位 0.93（≥ danger 阈值 0.9），其余正常
- **WHEN** 执行判定
- **THEN** `state=danger`、`multiplier=0.0`
- **AND** `reasons` 含该指标名与读数

#### Scenario: 正常区间

- **GIVEN** 全部分位低于 elevated 阈值
- **WHEN** 执行判定
- **THEN** `state=normal`、`multiplier=1.0`

#### Scenario: 配置损坏 fail loud

- **GIVEN** `config/regime.json` 存在但 JSON 非法（或缺键/系数越界）
- **WHEN** 加载配置
- **THEN** 抛出错误（MUST NOT 静默回落默认值）

#### Scenario: 缺文件回落默认

- **GIVEN** `config/regime.json` 不存在
- **WHEN** 加载配置
- **THEN** 使用内置默认（1.0/0.5/0.0）并留 WARN

#### Scenario: 判定可复现

- **GIVEN** 同一指标记录与配置
- **WHEN** 连续两次执行判定
- **THEN** 输出逐字段一致

### Requirement: regime 只读端点

系统 SHALL 提供 `GET /api/regime` 只读端点：返回最新判定（`state` / `multiplier` / `reasons`）、四指标当日值与分位、近 250 交易日历史序列、`as_of` 与派生 `age_days`（自 `as_of` 至请求时刻的自然日）与 `stale`（`age_days > 7`）；无记录或记录不可解析时 SHALL 返回 200 + `available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 或伪数据冒充。端点 MUST NOT 写入或修改任何状态。

#### Scenario: 有记录时返回判定与历史

- **GIVEN** 存在最新指标记录与合法配置
- **WHEN** 请求端点
- **THEN** 返回 200、`available=true`、`state`/`multiplier`/`reasons` 与指标、历史序列
- **AND** `age_days` 与 `stale` 按口径派生

#### Scenario: 无记录如实返回

- **GIVEN** 指标库为空
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `missing`

