# Delta for convertible-bond-coverage（add-convertible-bond-coverage）

## ADDED Requirements

### Requirement: 可转债标的主表与行情落库

系统 SHALL 抓取并落库可转债标的与日行情：标的至少含 转债代码、名称、正股代码（可得时）、转股价、转股溢价率、剩余规模（**可得来源内**；来源不含该字段时如实为 null，MUST NOT 以其他规模口径冒充）；行情至少含 交易日、收盘价、成交额。写入 SHALL 幂等（标的按代码唯一；行情按 交易日 + 代码 唯一）。条款与行情要素取数 SHALL 遵守时点纪律（判断只使用公告日及之前可得数据）。抓取失败 SHALL 重试并如实留痕，MUST NOT 以空数据冒充成功。

#### Scenario: 幂等 upsert

- **GIVEN** 同一转债同一交易日的行情已存在
- **WHEN** 重复抓取写入
- **THEN** 仍为单条记录，字段更新为最新值

#### Scenario: 抓取失败如实留痕

- **GIVEN** 数据源请求失败
- **WHEN** 抓取流程结束
- **THEN** 留痕失败原因，MUST NOT 以零条新增冒充成功

### Requirement: 条款事件落库与状态推导

系统 SHALL 落库可转债条款事件（强赎 / 下修 / 回售公告）：字段至少含 转债代码、事件类型、公告日期（可得时点）、关键要素（如强赎触发条件与最后交易日、下修幅度）。系统 SHALL 以确定性规则推导事件状态（如强赎倒计时剩余交易日、是否已触发）；推导 MUST NOT 调用 LLM，同输入 MUST 产出同输出。公告要素缺失时状态 SHALL 如实标注不可判定，MUST NOT 猜测。

#### Scenario: 强赎倒计时推导

- **GIVEN** 一条强赎公告（含最后交易日）与交易日历
- **WHEN** 在公告后某交易日推导状态
- **THEN** 输出剩余交易日与是否已触发，与手算一致

#### Scenario: 要素缺失如实标注

- **GIVEN** 一条缺少最后交易日的强赎公告
- **WHEN** 推导状态
- **THEN** 状态为不可判定并注明缺失字段，MUST NOT 猜测数值

### Requirement: 条款事件接入事件观察清单

条款事件 SHALL 进入既有事件观察清单（与业绩预告共用清单存储与只读端点），条目 `source` 为 `cbb`，并 SHALL 附风险要素：现价、转股溢价率、剩余规模、强赎倒计时（如适用）。清单条目准入为规则化标注（MUST NOT 依赖 LLM 分诊）。清单按 `source` 过滤 SHALL 可用。

#### Scenario: 事件入清单

- **GIVEN** 一条强赎公告事件
- **WHEN** 清单构建运行
- **THEN** 清单出现该条目（`source=cbb`），含现价、溢价率、剩余规模与倒计时
- **AND** 不调用任何 LLM

#### Scenario: 来源过滤

- **GIVEN** 清单同时含业绩预告与转债条款两类条目
- **WHEN** 按 `source=cbb` 过滤请求
- **THEN** 仅返回转债条款条目
