# Delta for settings-center

## MODIFIED Requirements

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
