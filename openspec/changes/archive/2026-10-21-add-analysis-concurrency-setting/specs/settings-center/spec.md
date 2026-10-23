# Delta for settings-center

## ADDED Requirements

### Requirement: 批量分析并发上限设置
系统 SHALL 在设置中心「会话管理」分区提供「批量分析最大并发数」设置项，允许用户在 1–20 之间调整批量深度分析的并发上限（默认 10）；设置 SHALL 持久化，并在保存后立即作用于当前进程的 `AnalyzeQueue`。

- 读取端点 SHALL 返回生效值、默认值、合法区间与来源（`file` / `env` / `default`）；来源 SHALL 如实披露，MUST NOT 以默认值冒充已保存设置。
- 保存端点 SHALL 拒绝非整数、布尔值与越界值（400）；非法值 MUST NOT 落盘、MUST NOT 改变运行中队列。
- 来源优先级 SHALL 为：设置文件 > 环境变量 `SCREENING_ANALYZE_CONCURRENCY` > 默认值；环境变量存在且与生效值不同时 SHALL 在响应中披露其值。
- 提高上限 SHALL 立即补位排队条目；降低上限 MUST NOT 终止已运行分析，仅影响后续补位。
- 配置文件损坏或字段非法时 SHALL 回退到环境变量或默认值并披露文件状态（`corrupt` / `invalid`），MUST NOT 返回 500。
- 设置项 SHALL 显示当前生效值与来源；保存成功 SHALL 给出「已保存」反馈。

#### Scenario: 默认值为 10
- **GIVEN** 无设置文件且未设置环境变量
- **WHEN** 读取并发设置
- **THEN** 生效值 SHALL 为 10
- **AND** 来源 SHALL 为 `default`

#### Scenario: 保存后立即生效并持久化
- **GIVEN** 当前生效值为 10
- **WHEN** 用户将设置项改为 7 并保存
- **THEN** 保存端点 SHALL 写配置文件并返回生效值 7
- **AND** 当前进程队列的并发上限 SHALL 立即变为 7
- **AND** 重新读取设置 SHALL 返回 7，来源为 `file`

#### Scenario: 非法值被拒绝
- **GIVEN** 用户输入 0、21 或非整数
- **WHEN** 提交保存
- **THEN** 端点 SHALL 返回 400
- **AND** 配置文件与当前队列上限 SHALL 保持不变

#### Scenario: 提高上限立即补位
- **GIVEN** 队列并发上限为 2，运行中 2 个、排队 2 个
- **WHEN** 将上限提高为 4
- **THEN** 排队中的前 2 个 SHALL 立即启动
- **AND** 运行中分析数 SHALL 不超过 4

#### Scenario: 降低上限不终止运行中分析
- **GIVEN** 队列并发上限为 4，运行中 4 个
- **WHEN** 将上限降低为 1
- **THEN** 已运行的 4 个分析 MUST NOT 被终止
- **AND** 新分析 SHALL 等待运行中分析结束、运行数低于 1 后再补位

#### Scenario: 环境变量回退与覆盖披露
- **GIVEN** 未保存设置且 `SCREENING_ANALYZE_CONCURRENCY=3`
- **WHEN** 读取并发设置
- **THEN** 生效值 SHALL 为 3
- **AND** 来源 SHALL 为 `env`
- **AND** 保存设置 7 后来源 SHALL 为 `file`，响应 SHALL 披露环境变量值 3

#### Scenario: 损坏文件如实降级
- **GIVEN** 配置文件内容不可解析或字段越界
- **WHEN** 读取并发设置
- **THEN** 系统 SHALL 回退到环境变量或默认值
- **AND** 响应 SHALL 披露文件状态（`corrupt` / `invalid`）
- **AND** MUST NOT 返回 500

#### Scenario: 设置项保存反馈
- **GIVEN** 用户进入「会话管理」分区
- **THEN** 分区 SHALL 显示当前生效值与来源
- **WHEN** 保存成功
- **THEN** SHALL 显示「已保存」并以生效值回显输入框
