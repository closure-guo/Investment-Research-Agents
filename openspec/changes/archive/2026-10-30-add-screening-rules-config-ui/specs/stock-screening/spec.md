# Delta for stock-screening

## MODIFIED Requirements

### Requirement: 约束单一来源

过滤层约束的**默认值**（上市天数阈值、成交额阈值等）SHALL 定义于 `screening/constraints.py`，
作为无配置覆盖时的行为基准；主档日批的**生效值** SHALL 经「选股规则运行时配置」解析
（配置文件 > 环境变量 > 默认值，见该需求），未覆盖时与默认值逐位一致；小票通道档位
（`MICROCAP_THRESHOLDS`）SHALL 继续直接使用 `constraints.py` 定义、MUST NOT 读运行时配置；
后续交易执行层的风控门禁 SHALL import 该模块复用约束定义，禁止复制常量到执行层代码。

(Previously: 过滤层使用的约束常量（上市天数阈值、成交额阈值等）SHALL 定义于
`screening/constraints.py`；后续交易执行层的风控门禁 SHALL import 该模块复用约束定义，
禁止复制常量到执行层代码。)

#### Scenario: 常量单点修改

- **GIVEN** 实现完成后的代码库，且无选股规则配置文件覆盖
- **WHEN** 将 `constraints.py` 中流动性阈值默认值从 5000 万改为 8000 万
- **THEN** 主档日批过滤层行为随之改变，且无需修改任何其他文件
- **AND** 存在配置文件覆盖时，生效值以配置文件为准，默认值变化不影响已覆盖项

## ADDED Requirements

### Requirement: 选股规则运行时配置

系统 SHALL 提供选股规则运行时配置，可配置项恰为三项：

- `min_trading_days`：L1 上市交易日下限，整数，区间 [1, 250]，默认值 =
  `constraints.MIN_TRADING_DAYS`（60）；
- `min_avg_amount_20d`：L1 近 20 日日均成交额下限（元），数值，区间 [1e6, 1e9]，
  默认值 = `constraints.MIN_AVG_AMOUNT_20D`（5e7）；
- `l2_top_n`：L2 Top-N 候选数，整数，区间 [1, 500]，默认值 = `pipeline.L2_TOP_N`（100）。

每项 SHALL 按「配置文件 > 环境变量 > 默认值」独立解析来源；配置文件为
`data/settings/screening_rules.json`（路径 SHALL 可经 `SCREENING_RULES_CONFIG_PATH` 覆盖），
环境变量为 `SCREENING_MIN_TRADING_DAYS` / `SCREENING_MIN_AVG_AMOUNT_20D` / `SCREENING_L2_TOP_N`。

读取端点 `GET /api/settings/screening-rules` SHALL 返回逐项生效值、来源
（`file` / `env` / `default`）、合法区间、默认值与环境变量披露值；文件缺失、损坏或字段
非法时 SHALL 按项回退到环境变量或默认值，并如实披露文件状态（`missing` / `ok` /
`corrupt` / `invalid`），MUST NOT 返回 500，MUST NOT 以默认值冒充已保存设置。

保存端点 `PUT /api/settings/screening-rules` SHALL 对三项**整体**校验（类型、区间、布尔值
排除）：任一项非法 SHALL 返回 400 且文件与运行状态不变；全部合法 SHALL 原子写设置文件
（含 `updated_at`）并返回与读取同形的生效态。

生效时机：主档选股日批（排程触发与手动补跑同口径）SHALL 在批次开始时解析一次配置并全程
使用该值；运行中批次 MUST NOT 受保存影响，变更于下一批次自动应用。本配置 MUST NOT 影响：
小票通道档位、L3 粗筛行为、稳定性门槛口径、执行层风控门禁。无配置文件且无环境变量时，
主档日批行为 MUST 与引入本配置前逐位一致。

#### Scenario: 默认值与现状逐位一致

- **GIVEN** 无配置文件且未设置任何相关环境变量
- **WHEN** 读取选股规则配置
- **THEN** 三项生效值分别为 60、5e7 元、100，来源均为 `default`
- **AND** 主档日批的 L1 过滤与 L2 截断行为与既有常量口径一致

#### Scenario: 保存后下一批次生效

- **GIVEN** 当前生效值为默认（60 / 5e7 / 100）
- **WHEN** `PUT /api/settings/screening-rules` 提交 80 / 8e7 / 50
- **THEN** 端点返回生效值 80 / 8e7 / 50、来源 `file`
- **AND** 重新读取返回同一生效态
- **AND** 其后运行的第一个主档批次按新值过滤与截断

#### Scenario: 非法值整体拒绝

- **GIVEN** 配置文件已存在合法保存值
- **WHEN** `PUT` 提交中 `min_trading_days=0`（其余项合法）
- **THEN** 端点返回 400
- **AND** 配置文件内容保持不变，读取端点生效值不变

#### Scenario: 环境变量披露

- **GIVEN** 无配置文件且 `SCREENING_L2_TOP_N=50`
- **WHEN** 读取选股规则配置
- **THEN** `l2_top_n` 生效值为 50、来源 `env`
- **AND** 响应披露该环境变量值 50

#### Scenario: 损坏文件如实降级

- **GIVEN** 配置文件内容不可解析
- **WHEN** 读取选股规则配置
- **THEN** 系统回退到环境变量或默认值，文件状态披露为 `corrupt`
- **AND** MUST NOT 返回 500

#### Scenario: 运行中批次不受保存影响

- **GIVEN** 一个主档批次正在运行且使用生效值 Top-N=100
- **WHEN** 批次运行期间保存 Top-N=50
- **THEN** 该批次仍以 100 完成并落库
- **AND** 下一批次起以 50 运行

#### Scenario: 小票通道不受影响

- **GIVEN** 选股规则配置已保存 `min_avg_amount_20d=8e7`
- **WHEN** 小票通道批次运行
- **THEN** 其 L1 档位仍为 `MICROCAP_THRESHOLDS`（流动性下限 1000 万），不读配置值
