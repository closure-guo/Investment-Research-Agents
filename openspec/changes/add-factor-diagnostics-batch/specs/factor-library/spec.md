## ADDED Requirements

### Requirement: 诊断读数日批

系统 SHALL 提供因子诊断读数日批（作业 `factor_diagnostics_daily`，工作日 17:15、周末亦运行以覆盖当日收割入库因子）：对窗口 IC 点数 ≥ 30 且**读数缺失或 `as_of` 落后于最新快照日**的因子（任意状态），在准入面板上现算分层单调性观测读数与风格暴露披露并落库（每因子一行，含 `as_of` 数据截止交易日）。SHALL 逐因子失败隔离——单因子计算失败（面板不可得 / 表达式不可求值 / 数据异常）只跳过该因子并保留其既有读数，MUST NOT 中断其余因子；SHALL 按需构建面板——无任何待算因子时 MUST NOT 构建准入面板，单次运行至多构建一次并逐因子复用。落库读数为**只读诊断观测项**，SHALL NOT 参与任何准入裁决或处置（处置仍只经衰减监控路径）。重跑 SHALL 幂等：同一 `as_of` 重算覆盖同值。

#### Scenario: 存量因子首跑补算

- **GIVEN** 库中存在 3 条 active 因子，窗口 IC 点数分别为 60、60、4，`factor_diagnostics` 表为空
- **WHEN** 日批运行
- **THEN** 两条 ≥ 30 点的因子获得落库读数（`style_exposure` / `monotonicity` / `as_of`），4 点因子无读数记录
- **AND** 面板仅构建一次并被三条候选评估路径复用

#### Scenario: 读数已新鲜则零面板构建

- **GIVEN** 全部因子已有读数且 `as_of` 等于最新快照日
- **WHEN** 日批运行（如周末、快照无新交易日）
- **THEN** 无因子被重算，摘要记 `computed=0`
- **AND** MUST NOT 构建准入面板（运行在秒级完成）

#### Scenario: 单因子失败隔离

- **GIVEN** 两条待算因子，其一表达式在面板上不可求值（抛异常）
- **WHEN** 日批运行
- **THEN** 可求值因子正常落库读数，失败因子保留既有读数（无读数则维持无读数），失败计入摘要
- **AND** 运行不抛错上抛（作业记 ok）

#### Scenario: 新入库因子当日获得读数

- **GIVEN** 收割日批 16:00 新准入一条因子（无读数）
- **WHEN** 17:15 日批运行
- **THEN** 该因子获得落库读数，`as_of` 为最新快照交易日

## MODIFIED Requirements

### Requirement: 因子库只读查询

系统 SHALL 提供因子库只读接口：列表 SHALL 返回每个因子的名称、表达式、状态（`active` / `retired`）、来源（`builtin` / `llm`）、ICIR、最近闸门结论与衰减标记；详情 SHALL 返回完整 DSL 表达式、IC 序列与事件历史（准入 / 降级 / 查重等事件及时间）、**分层单调性观测读数**（`monotonicity`：分位序-收益 Spearman 与端点差；存量因子为观测项，不参与处置），并 SHALL 包含**风格暴露披露**（`style_exposure`：与 builtin 风格代理篮子各成员的 IC 序列相关、主导风格及其 |ρ|、n 日与阈值；|主导 ρ| ≥ 0.7 时给出「与标准风格高度重合」标注）——披露为**只读诊断**，SHALL NOT 参与任何准入裁决。诊断读数 SHALL 来自诊断日批落库（「诊断读数日批」requirement），并 SHALL 附带 `diagnostics_as_of`（读数数据截止交易日；无读数为 `null`）——读数反映日批运行时点的面板，非请求时点现算。接口**无条件** MUST NOT 构建准入面板（任何分支、任何 IC 点数下，详情响应不因快照库大小而阻塞）；读数未计算或不可计算时（日批未及 / 面板不可得 / 共同样本不足 / 序列常量）SHALL 返回 `null` 或逐风格 `null`，MUST NOT 以 0 或虚构读数冒充。接口 SHALL NOT 暴露任何写操作（因子入库、状态变更、参数调整仍只经五道闸与衰减监控路径）；库为空时 SHALL 返回空列表而非错误。

(Previously: 诊断读数在详情请求内于准入面板上现算——仅「窗口 IC < 30」分支短路跳过面板构建；≥ 30 点时请求内现算 500 日面板把详情接口拖成数十分钟（生产实测复发）。现改为日批预计算落库、接口纯读库。)

#### Scenario: 列表与详情字段齐备

- **GIVEN** 库中含一条 active/llm 来源因子与一条 retired/builtin 因子
- **WHEN** 请求列表与其中一条详情
- **THEN** 列表含两因子的状态/来源/ICIR/衰减标记；详情含完整表达式、IC 序列、单调性读数与事件历史

#### Scenario: 空库不报错

- **GIVEN** 因子库为空
- **WHEN** 请求列表
- **THEN** 返回 200 与空列表，MUST NOT 以 500 或伪数据冒充

#### Scenario: 风格暴露披露可计算与不可计算两态

- **GIVEN** 日批已为某因子落库读数（其与某风格代理的共同 IC ≥ 30 日），且主导 |ρ| ≥ 0.7
- **WHEN** 请求详情
- **THEN** `style_exposure` 含逐风格相关系数、主导风格与 |ρ|、`flagged` 为真，且 `diagnostics_as_of` 为日批数据截止交易日
- **AND** 日批未及（无落库读数）或面板不可得时 `style_exposure` 为 `null`（前端如实展示「数据不足」，MUST NOT 以 0 冒充）

#### Scenario: IC 就绪度披露

- **GIVEN** 某 active 因子有 4 条 IC 记录、最新 IC 日期为 2026-10-21、打分门槛为 10
- **WHEN** 请求列表或详情
- **THEN** 返回 `ic_points=4`、`min_samples_for_scoring=10`、`ic_window=60`
- **AND** `expected_factor_mode_date` 为按工作日推算的首个可见达标日（示例为 2026-11-02），并在字段说明中标注「按工作日推算、不含节假日」

#### Scenario: 任何分支都不构建准入面板

- **GIVEN** 因子窗口 IC 点数为 4（样本不足）或 60（样本充足但日批未及/已及）
- **WHEN** 请求详情
- **THEN** 响应正常返回（`style_exposure` / `monotonicity` 按落库读数或 `null`）
- **AND** 接口 MUST NOT 构建准入面板——无论 IC 点数与落库状态，详情路径零面板构建（响应不因快照库大小而阻塞）

#### Scenario: 分层单调性观测读数可算与不可算两态

- **GIVEN** 某 active 因子已由日批落库单调性读数
- **WHEN** 请求详情
- **THEN** `monotonicity` 含 `passed` / `spearman` / `q10_q1` / `decile_returns` / `valid_days` / `threshold`，为观测读数（不挂任何处置）
- **AND** 日批未及或表达式在日批面板上不可求值时 `monotonicity` 为 `null`（前端如实展示「数据不足」，MUST NOT 以 0 冒充）
