# Delta for stock-screening

## ADDED Requirements

### Requirement: 精选池与批次历史按档位查询

选股池读取接口与批次历史接口 SHALL 接受可选 `pool` 查询参数（`main` / `microcap`，即池档位），
缺省 `main`；缺省调用行为与既有契约逐字一致（既有前端、既有测试与运营页口径零变化）。
`pool` 参数取值超出 `main` / `microcap` 时接口 SHALL 返回 422，MUST NOT 静默回退到任一档位。
池接口响应 SHALL 新增批次级 `frontier_gate` 与 `exec_constraints` 字段，取自最近成功批次行：
main 档批次该两字段为 `null`（前端不渲染门禁披露）；microcap 档批次携带其落库值；无成功批次时
均为 `null`。microcap 档尚无成功批次时，池接口 SHALL 返回 `batch_date=null` + 空池列表 + 空态
提示语（不报错），批次历史接口返回空列表（不报错）。

#### Scenario: 缺省参数主档行为不变

- **GIVEN** 库中含 main 与 microcap 两档同日批次
- **WHEN** 不带 `pool` 参数请求池接口与批次历史接口
- **THEN** 池行与批次行均为 main 档数据，既有字段语义与变更前一致
- **AND** 新增的 `frontier_gate` / `exec_constraints` 在 main 档响应中为 `null`
- **AND** 批次历史不出现 microcap 行

#### Scenario: 按档位查询小市值池

- **GIVEN** 库中含 microcap 档成功批次（带 `frontier_gate` 与 `exec_constraints` 落库值）
- **WHEN** 以 `pool=microcap` 请求池接口与批次历史接口
- **THEN** 池行与批次行均为 microcap 档数据，池行字段（symbol、stock_name、composite_score、
  rank、consecutive_days、l3_verdict、l3_reason、factor_contribs）与主档契约一致
- **AND** 池响应 `frontier_gate` 与 `exec_constraints` 为该批次落库值

#### Scenario: 非法档位拒绝

- **GIVEN** 请求带 `pool=foo`
- **WHEN** 请求池接口或批次历史接口
- **THEN** 返回 422，不返回任何档位数据

#### Scenario: 小市值档空态不报错

- **GIVEN** 库中尚无 microcap 档成功批次
- **WHEN** 以 `pool=microcap` 请求池接口与批次历史接口
- **THEN** 池接口返回 200：`batch_date=null`、空池列表、空态提示语
- **AND** 批次历史接口返回 200 与空列表

### Requirement: 选股页池档位切换

选股页精选池区块 SHALL 提供主档池 / 小市值池两档切换，缺省展示主档池；切换 SHALL 加载对应档位
数据，切回主档时主档视图内容与切换前一致。小市值视图 SHALL 展示与主档同构的池表（标的、股票
名称、综合分、连续在榜天数、L3 判定、批次日期），并额外披露通道特有信息：

- **前置门禁横幅**：`frontier_gate=ready` 时明示「前置齐备，可执行候选（仍不自动产生订单）」；
  `observation_only` 时明示观察模式并列出缺失前置项（`delist_pool` / `regime_data` 映射为可读文案）；
  两态 MUST NOT 混淆；
- **执行约束披露**：展示 `exec_constraints` 的单票上限（`suggested_single_cap`）与滑点档位提升
  （`slippage_tier_bump`）；
- **观察通道声明**：明示该通道 v1 仅观察、不向执行链产生订单。

小市值视图 v1 SHALL NOT 提供深度分析触发（不渲染「对精选池跑深度分析」按钮）；主档视图的既有
按钮与队列徽标行为不变。小市值档无成功批次时 SHALL 展示空态（说明尚无小市值批次落库），不以
主档数据或报错冒充。

#### Scenario: 切换到小市值视图展示通道披露

- **GIVEN** 库中含 microcap 档成功批次且门禁为 `observation_only:missing=regime_data`
- **WHEN** 用户在选股页切换到小市值池
- **THEN** 池表展示 microcap 档池行（列同主档）
- **AND** 页面展示观察模式横幅，列出「拥挤度数据」缺失前置
- **AND** 页面展示执行约束（单票上限、滑点档位提升）与「仅观察、不产生订单」声明
- **AND** 页面不渲染深度分析触发按钮

#### Scenario: 门禁就绪两态可区分

- **GIVEN** 库中含 microcap 档成功批次且门禁为 `ready`
- **WHEN** 用户切换到小市值池
- **THEN** 横幅明示「前置齐备，可执行候选」而非观察模式
- **AND** 「不自动产生订单」语义仍然可见

#### Scenario: 切回主档数据不受影响

- **GIVEN** 用户已切换到小市值视图
- **WHEN** 用户切回主档池
- **THEN** 主档池表、批次日期与横幅内容与切换前一致

#### Scenario: 小市值档空态

- **GIVEN** 尚无 microcap 档成功批次
- **WHEN** 用户切换到小市值池
- **THEN** 展示空态提示（尚无小市值批次），不展示主档数据、不报错
