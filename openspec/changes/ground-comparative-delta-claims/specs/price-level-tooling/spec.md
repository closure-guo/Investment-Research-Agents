## MODIFIED Requirements

### Requirement: 分析师派生值预生成

系统 SHALL 由工具预计算常用派生值——5/20/60 日区间涨跌幅、距 250 日高点回撤、距 250 日低点反弹，**以及均线间差幅**（`ma_spread_5_20_pct`、`ma_spread_20_60_pct`、`close_vs_ma20_pct`、`close_vs_ma60_pct`，百分比，正 = 前者高于后者）——并与 technical_indicators 并列注入技术面分析师 context；分析师对上述派生值的陈述 SHALL 引用预生成值而非自行计算。

派生值 SHALL 存放于 `AnalysisState` 中**已声明**的 channel `derived_series`（未声明键被图合并静默丢弃——incident 027），该键 SHALL 出现在图通道契约测试的断言集合中。context 中的派生值块 SHALL 标注**真实可引用**的 field_ref 前缀 `derived_series.`，SHALL NOT 标注与 state 键不一致的前缀；SHALL NOT 以别名映射代替真实键（沿「上下文与校验单一词表」）。

`derived_series` SHALL 注册进计算型 claim 重算注册表（以同一份 `calc_derived_series(kline)` 重算，配独立重算 fixture 测试）与有符号量根键集合（`direction` 申报参与符号比对）。均线差幅 SHALL 复用既有技术指标的均线计算口径，SHALL NOT 另算一份均线。任一操作数缺失或分母为 0 的派生项 SHALL 为 None 并在 context 如实标注缺失，SHALL NOT 伪造。

派生值预生成 SHALL 有端到端验收：经编译后的真实图执行一次，技术面分析师 context SHALL 实际包含派生值块，且以 `derived_series.<字段>` 为 field_ref 的 numerical claim SHALL 经引用校验 PASS。
(Previously: 派生值清单为区间涨跌幅与距高低点回撤/反弹五项；「随 technical_indicators 注入」但未钉死存放键与可引用前缀；实现将其写入未声明的 `derived_series` 键且 context 标注前缀 `derived.`，导致在真实图中从未注入且不可引用；无重算注册、无有符号量注册、无端到端验收)

#### Scenario: 派生值注入

- **WHEN** 技术面分析师 context 构建
- **THEN** SHALL 包含派生值表（带 field_ref 可引用）
- **AND** 数据不足的派生项 SHALL 如实标注缺失

#### Scenario: 真实图中派生值可见且可引用

- **WHEN** 以编译后的 5 层图执行一次 deep 全流程（或消融变体图）
- **THEN** 技术面分析师 context SHALL 含「常用派生值」块且前缀标注为 `derived_series.`
- **AND** 以 `derived_series.chg_5d` 为 field_ref、stated_value 等于预生成值的 numerical claim 经 `verify_claims` SHALL 判 PASS
- **AND** `build_5layer_graph().builder.channels` SHALL 含 `derived_series`

#### Scenario: 均线差幅预生成

- **GIVEN** K 线 ≥ 60 期，MA5 / MA20 / MA60 最新值分别为 10.30 / 10.00 / 9.50，收盘 10.20
- **WHEN** 计算派生值
- **THEN** `ma_spread_5_20_pct` SHALL 为 3.0、`ma_spread_20_60_pct` SHALL 约 5.263、`close_vs_ma20_pct` SHALL 为 2.0、`close_vs_ma60_pct` SHALL 约 7.368（相对容差 0.5% 内）
- **AND** 均线取值 SHALL 与 `technical_indicators.MA.<w>.-1` 一致（同一计算口径）

#### Scenario: 数据不足如实标缺

- **GIVEN** K 线仅 30 期（MA60 不可得）
- **WHEN** 计算派生值
- **THEN** `ma_spread_20_60_pct` 与 `close_vs_ma60_pct` SHALL 为 None，context 渲染「数据不足」
- **AND** `ma_spread_5_20_pct`、`close_vs_ma20_pct` SHALL 正常计算

#### Scenario: 派生值计算型 claim 可重算

- **WHEN** 报告含 computational claim `field_ref=derived_series.ma_spread_5_20_pct`
- **THEN** 校验器 SHALL 以 `calc_derived_series(state["kline"])` 重算 ground truth 并按相对容差判 PASS/FAIL，SHALL NOT 判 UNVERIFIABLE

#### Scenario: 派生值方向申报参与符号比对

- **WHEN** claim `field_ref=derived_series.ma_spread_5_20_pct`，`stated_value=2.3`，`direction="negative"`，真值 -2.3
- **THEN** 校验 SHALL 判 PASS（有符号量，direction 修饰后符号一致）
- **WHEN** 同 claim `direction="positive"`
- **THEN** 校验 SHALL 判 FAIL，bucket `direction_mismatch`
