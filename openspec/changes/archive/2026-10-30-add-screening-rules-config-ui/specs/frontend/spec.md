# Delta for frontend

## ADDED Requirements

### Requirement: 选股规则配置分区

系统 SHALL 在设置中心（`/settings`）新增「选股规则」分区（`ModuleId="screening-rules"`），
左侧导航 SHALL 含该入口，一次仅显示该分区内容。分区 SHALL 复用
`GET /api/settings/screening-rules` 加载并展示三项规则，每项 SHALL 显示生效值、来源
（配置文件 / 环境变量 / 默认值）与合法区间：

1. **上市天数下限**（`min_trading_days`）：整数输入，区间 1–250；
2. **近 20 日日均成交额下限**（`min_avg_amount_20d`）：以**万元**为单位的非负整数输入，
   前端区间 100–100000 万元；展示与录入均为万元（后端元口径换算 1 万元 = 10⁴ 元，
   双向换算 SHALL 无舍入误差）；
3. **L2 候选 Top-N**（`l2_top_n`）：整数输入，区间 1–500。

分区 SHALL 提供「保存」动作调用 `PUT /api/settings/screening-rules`（体含三项完整值）：
成功 SHALL 展示「已保存」并以生效值与最新来源回显；越界或非整数（含万元输入非整数）
SHALL 前端拦截并提示，MUST NOT 发出请求；后端 400 SHALL 将原因原位展示，MUST NOT
静默丢弃。分区 SHALL 明示生效时机：「下一次选股日批或手动补跑开始时生效，运行中批次
不受影响」。加载失败 SHALL 在分区原位提示，MUST NOT 影响设置中心其他分区。分区 MUST NOT
暴露三项以外的任何写入面。

#### Scenario: 分区入口与默认显示

- **GIVEN** 用户打开设置中心
- **WHEN** 点击左侧「选股规则」
- **THEN** 分区展示三项规则及其生效值与来源（无配置时来源为「默认值」）
- **AND** 展示生效时机说明

#### Scenario: 万元口径展示与保存

- **GIVEN** 后端 `min_avg_amount_20d=5e7` 元
- **WHEN** 分区加载完成
- **THEN** 该项显示 5000（万元）
- **WHEN** 用户改为 8000 并点击保存
- **THEN** 发出 `PUT` 请求，体含 `min_avg_amount_20d=8e7`（元）及另两项当前值

#### Scenario: 非法输入前端拦截

- **GIVEN** 分区加载完成
- **WHEN** 用户将 L2 候选 Top-N 改为 0 并点击保存
- **THEN** 展示区间错误提示
- **AND** MUST NOT 发出 PUT 请求

#### Scenario: 保存成功反馈

- **GIVEN** 三项输入均合法
- **WHEN** 用户点击保存且后端返回 200
- **THEN** 展示「已保存」，三项来源徽章更新为「配置文件」并以生效值回显输入框

#### Scenario: 加载失败隔离

- **GIVEN** `GET /api/settings/screening-rules` 返回 500 或网络失败
- **WHEN** 打开「选股规则」分区
- **THEN** 分区原位展示加载失败提示
- **AND** 设置中心其他分区（如缓存管理）加载与操作不受影响
