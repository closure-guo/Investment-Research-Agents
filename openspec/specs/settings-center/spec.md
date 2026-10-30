# settings-center Specification

## Purpose
TBD - created by archiving change add-agent-settings-center. Update Purpose after archive.
## Requirements
### Requirement: 设置中心页路由与左侧垂直导航

系统 SHALL 提供 `/settings` 设置中心页，采用左侧垂直导航 + 右侧内容区布局，聚合九大分区：
LLM 配置、缓存管理、会话管理、运行信息、数据监控、因子研发配置、战绩展示偏好、评估运维、
选股规则；一次仅显示一个激活分区的内容。

(Previously: 聚合六大分区：LLM 配置、缓存管理、会话管理、运行信息、数据监控、战绩展示偏好；
一次仅显示一个激活分区的内容。——此前「因子研发配置」「评估运维」两分区已实现但未纳入
本条枚举，本变更一并修正为九大分区的事实枚举。)

#### Scenario: 访问 /settings 显示导航与默认分区
- **GIVEN** 用户导航到 `/settings`
- **THEN** 页面 SHALL 显示左侧垂直导航（含九大分区项）与右侧内容区
- **AND** 默认激活「LLM 配置」分区并显示其内容

#### Scenario: 点击导航切换分区
- **GIVEN** 用户在左侧垂直导航点击「缓存管理」
- **THEN** 右侧内容区 SHALL 切换到缓存管理分区内容
- **AND** 导航高亮 SHALL 同步到激活分区

#### Scenario: 各分区互不影响
- **GIVEN** 用户在「缓存管理」分区执行了清空操作
- **THEN** 切换回「LLM 配置」分区时 LLM 配置与 profile 状态 SHALL 保持不变

### Requirement: 设置入口迁移（弹窗 → 页面跳转）
系统 SHALL 将原 `SettingsModal` 的设置入口迁移为跳转 `/settings` 设置中心页：header 设置按钮、侧栏 gear 按钮、无 profile 强制配置三处入口 SHALL 改为跳转/定位到设置中心页；系统 SHALL 不再渲染 SettingsModal 弹窗。

#### Scenario: header 设置按钮跳转设置页
- **GIVEN** 用户点击 header 的「设置」按钮
- **THEN** 系统 SHALL 跳转到 `/settings` 设置中心页
- **AND** 不再弹出任何设置弹窗

#### Scenario: 侧栏 gear 按钮跳转设置页
- **GIVEN** 用户点击侧边栏的 gear 按钮
- **THEN** 系统 SHALL 跳转到 `/settings` 设置中心页

#### Scenario: 无 profile 强制配置跳转设置页
- **GIVEN** 用户尝试发起分析/聊天但当前无任何 LLM profile
- **THEN** 系统 SHALL 阻止发送并跳转到 `/settings` 设置中心页
- **AND** 定位到「LLM 配置」分区以引导用户配置

#### Scenario: SettingsModal 不再渲染
- **GIVEN** 应用任意状态下不存在 showSettings 弹窗状态
- **THEN** 系统 SHALL 不在任何位置渲染 SettingsModal 弹窗
- **AND** 所有设置能力通过 `/settings` 页面访问

### Requirement: 运行环境与关于分区
系统 SHALL 在设置中心页提供「运行信息」分区，只读展示后端默认 LLM 配置（model/base_url/thinking）、健康状态、Langfuse 地址、git 版本/commit；数据来自只读运行信息端点，响应 SHALL NOT 包含 apiKey。

#### Scenario: 获取运行信息
- **GIVEN** 用户进入设置中心页「运行信息」分区
- **THEN** 前端 SHALL 调用 `GET /api/run-info`
- **AND** 分区 SHALL 展示 model、base_url、thinking、健康状态、Langfuse 地址、git 版本/commit

#### Scenario: apiKey 绝不在响应中
- **GIVEN** 后端存在已配置的 LLM API Key
- **THEN** `GET /api/run-info` 响应 SHALL NOT 包含 apiKey 或任何密钥字段

### Requirement: 会话管理分区（清空全部会话）
系统 SHALL 在设置中心页提供「会话管理」分区，展示会话总数，并提供「清空全部会话」操作；操作 SHALL 经二次确认后调用批量删除端点，级联删除全部会话及其事件；清空后前端回到空态首页。单会话删除/重命名语义 SHALL 保持不变。

#### Scenario: 清空全部会话需二次确认
- **GIVEN** 用户点击「清空全部会话」
- **THEN** 系统 SHALL 弹出二次确认提示
- **WHEN** 用户确认
- **THEN** 系统 SHALL 调用批量删除端点删除全部会话及事件
- **AND** 前端会话列表 SHALL 清空并回到空态首页

#### Scenario: 取消清空不删除
- **GIVEN** 用户点击「清空全部会话」后取消二次确认
- **THEN** 系统 SHALL 不删除任何会话
- **AND** 会话列表 SHALL 保持不变

#### Scenario: 单会话操作不受影响
- **GIVEN** 系统存在多个会话
- **WHEN** 用户在侧边栏删除或重命名单个会话
- **THEN** 该操作行为 SHALL 与引入本功能前一致

### Requirement: 战绩展示偏好分区
系统 SHALL 在设置中心页提供「战绩展示偏好」分区，提供四项偏好：默认时间跨度、对比基准指数、回撤警示阈值、净值图默认形态；偏好 SHALL 持久化到浏览器 localStorage（key `fa_track_prefs`），战绩页（/track-record、校准页、详情页）SHALL 读取并应用。

#### Scenario: 修改并持久化偏好
- **GIVEN** 用户在战绩展示偏好分区将默认时间跨度选择为「近 6 月」
- **THEN** 系统 SHALL 将 `fa_track_prefs.timeSpan = "6m"` 写入 localStorage
- **AND** 页面刷新后重新进入分区 SHALL 恢复该值

#### Scenario: 战绩页应用默认时间跨度
- **GIVEN** 用户已设置默认时间跨度「近 6 月」
- **THEN** 进入战绩页时净值图与列表 SHALL 默认按该跨度展示

#### Scenario: 战绩页应用回撤警示阈值
- **GIVEN** 用户已设置回撤警示阈值 10%
- **THEN** 战绩卡中回撤幅度超过 10% 的项 SHALL 显示警示高亮
- **AND** 回撤未超阈值的项 SHALL 保持正常样式

#### Scenario: 战绩页应用净值图默认形态
- **GIVEN** 用户已设置净值图默认形态为「区间收益」
- **THEN** 战绩页净值图 SHALL 默认按区间收益口径渲染

#### Scenario: 未设置时使用默认值
- **GIVEN** 用户从未设置任何战绩展示偏好
- **THEN** 系统 SHALL 使用默认值（全部时间跨度、不对比基准、内置默认回撤阈值、累计净值形态）

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

