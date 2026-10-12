# cache-management Specification

## Purpose
TBD - created by archiving change add-agent-settings-center. Update Purpose after archive.
## Requirements
### Requirement: 缓存统计
系统 SHALL 提供缓存统计能力，返回数据缓存（cache.db）与能力探测缓存（内存）的概览：总条目数、占用、已过期待清条目、最近命中时间，以及按数据类别聚合的条目数与最早过期时间。

#### Scenario: 返回数据缓存统计
- **GIVEN** 数据缓存中存在多个类别的条目
- **WHEN** 前端调用 `GET /api/cache/stats`
- **THEN** 响应 SHALL 包含总条目数、占用、已过期待清条目、最近命中时间
- **AND** 响应 SHALL 包含按类别聚合的明细（类别名、条目数、最早过期时间）

#### Scenario: 返回能力探测缓存统计
- **WHEN** 前端调用 `GET /api/cache/stats`
- **THEN** 响应 SHALL 包含能力探测缓存（内存）的条目数
- **AND** 能力探测缓存为空时条目数 SHALL 为 0

#### Scenario: 统计已过期待清条目
- **GIVEN** 数据缓存中存在已超过过期时间但尚未被惰性删除的条目
- **WHEN** 前端调用 `GET /api/cache/stats`
- **THEN** 响应 SHALL 将这类条目计入「已过期待清」数量

### Requirement: 按类别清空缓存
系统 SHALL 允许按数据类别清空缓存（如 stock_quote、kline、balance_sheet、income_statement、news 等），仅删除指定类别条目，其余类别保留。

#### Scenario: 清空指定类别
- **GIVEN** 数据缓存包含类别 A 与类别 B 的条目
- **WHEN** 用户对类别 A 执行「清空该类」
- **THEN** 类别 A 的全部条目 SHALL 被删除
- **AND** 类别 B 的条目 SHALL 保留

#### Scenario: 清空不存在的类别
- **WHEN** 用户对不存在条目的类别执行清空
- **THEN** 系统 SHALL 返回成功且不报错
- **AND** 删除条目数为 0

### Requirement: 按股票代码清空缓存
系统 SHALL 允许按股票代码清空该代码的全部缓存条目（该代码下所有类别），其余代码不受影响。

#### Scenario: 清空指定代码
- **GIVEN** 数据缓存包含代码 600519 与 000001 的条目
- **WHEN** 用户对代码 600519 执行「清空该代码全部缓存」
- **THEN** 代码 600519 的全部类别条目 SHALL 被删除
- **AND** 代码 000001 的条目 SHALL 保留

### Requirement: 全部清空数据缓存（高危确认）
系统 SHALL 提供全部清空数据缓存操作；该操作 SHALL 要求请求携带确认令牌（confirm=true），未携带时 SHALL 拒绝；前端 SHALL 要求用户输入「清空」二字作为确认后才发起请求。

#### Scenario: 未携带确认令牌拒绝
- **GIVEN** 数据缓存存在条目
- **WHEN** 请求全部清空但未携带确认令牌
- **THEN** 系统 SHALL 返回 4xx 拒绝
- **AND** 数据缓存条目 SHALL 保持不变

#### Scenario: 携带确认令牌全清
- **WHEN** 请求全部清空且携带确认令牌
- **THEN** 系统 SHALL 删除数据缓存全部条目
- **AND** 后续 `GET /api/cache/stats` 总条目数 SHALL 为 0

#### Scenario: 前端输入确认才能全清
- **GIVEN** 用户点击「全部清空」
- **THEN** 系统 SHALL 弹出确认框并要求输入「清空」二字
- **WHEN** 用户输入正确文本并确认
- **THEN** 前端 SHALL 携带确认令牌发起全部清空请求
- **AND** 输入不符或取消时 SHALL 不发起请求

### Requirement: 能力探测缓存一键清除
系统 SHALL 提供清除能力探测缓存（内存）的操作，清除后后续请求 SHALL 重新执行能力探测。

#### Scenario: 清除能力探测缓存
- **GIVEN** 能力探测缓存已存在条目
- **WHEN** 用户执行「清除能力探测缓存」
- **THEN** 能力探测缓存条目 SHALL 被清空
- **AND** 下次发起分析时系统 SHALL 重新执行能力探测

### Requirement: 前端缓存管理分区
系统 SHALL 在设置中心页提供「缓存管理」分区，展示汇总统计条（总条目/占用/已过期待清/最近命中）、按类别表格（每类条目数/最早过期/「清空该类」按钮）、按股票代码清理输入框、全部清空按钮（需输入「清空」确认），以及能力探测缓存一键清除入口。

#### Scenario: 展示汇总统计条
- **GIVEN** 用户进入设置中心页「缓存管理」分区
- **THEN** 分区 SHALL 展示总条目、占用、已过期待清、最近命中的汇总统计

#### Scenario: 表格按类别清空
- **GIVEN** 分区按类别表格已展示各类别条目
- **WHEN** 用户点击某类别的「清空该类」
- **THEN** 系统 SHALL 清空该类并刷新统计
- **AND** 其余类别条目 SHALL 保留

#### Scenario: 按代码清理
- **GIVEN** 用户在某类别行或独立输入框输入股票代码
- **WHEN** 用户点击「清空该代码全部缓存」
- **THEN** 系统 SHALL 删除该代码全部类别条目并刷新统计

#### Scenario: 全部清空交互
- **WHEN** 用户点击「全部清空」
- **THEN** 系统 SHALL 弹出确认框要求输入「清空」二字
- **AND** 确认后清空全部数据缓存并刷新统计

### Requirement: 快照表 TTL 豁免

行情快照表与历史成分表 SHALL 视为不可变历史数据，不适用现有行情缓存的 TTL 过期策略（TTL 至当日收盘）；两表数据一经写入 SHALL NOT 被缓存清理逻辑删除或更新。

#### Scenario: 缓存清理不影响快照

- **GIVEN** 快照表含 2025 年全年记录，行情缓存 TTL 到期触发清理
- **WHEN** 缓存清理任务执行
- **THEN** 快照表与成分表行数不变

