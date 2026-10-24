## MODIFIED Requirements

### Requirement: 因子库只读查询

系统 SHALL 提供因子库只读接口：列表 SHALL 返回每个因子的名称、表达式、状态（`active` / `retired`）、来源（`builtin` / `llm`）、ICIR、**IC 样本数与就绪度**（`ic_points`、`min_samples_for_scoring`、`ic_window`、`expected_factor_mode_date`）、最近闸门结论与衰减标记；详情 SHALL 返回完整 DSL 表达式、IC 序列、上述就绪度字段与事件历史（准入 / 降级 / 查重等事件及时间），并 SHALL 包含**风格暴露披露**（`style_exposure`：与 builtin 风格代理篮子各成员的 IC 序列相关、主导风格及其 |ρ|、n 日与阈值；|主导 ρ| ≥ 0.7 时给出「与标准风格高度重合」标注）——披露为**只读诊断**，SHALL NOT 参与任何准入裁决；不可计算时（面板不可得 / 共同样本不足 / 序列常量）SHALL 返回 `null` 或逐风格 `null`，MUST NOT 以 0 或虚构读数冒充。`expected_factor_mode_date` SHALL 由「当前样本数 + 最新 IC 日期」按工作日推算（跳过周末，不含法定节假日），样本已达门槛时 SHALL 为 `null`，无任何 IC 记录时 SHALL 为 `null`（不得臆造日期）。当因子 IC 点数不足以达到风格暴露所需共同样本下限（30）时，详情接口 SHALL 直接返回 `style_exposure: null`，MUST NOT 为此构建准入面板（500 日 × 全市场面板构建会把详情接口拖成分钟级超时）。接口 SHALL NOT 暴露任何写操作（因子入库、状态变更、参数调整仍只经五道闸与衰减监控路径）；库为空时 SHALL 返回空列表而非错误。

(Previously: 列表/详情不含 IC 就绪度字段；详情接口无条件构建准入面板计算风格暴露，IC 点数不足时仍付出分钟级面板构建成本。)

#### Scenario: 列表与详情字段齐备

- **GIVEN** 库中含一条 active/llm 来源因子与一条 retired/builtin 因子
- **WHEN** 请求列表与其中一条详情
- **THEN** 列表含两因子的状态/来源/ICIR/样本数/预计恢复日期/衰减标记；详情含完整表达式、IC 序列与事件历史

#### Scenario: 空库不报错

- **GIVEN** 因子库为空
- **WHEN** 请求列表
- **THEN** 返回 200 与空列表，MUST NOT 以 500 或伪数据冒充

#### Scenario: 风格暴露披露可计算与不可计算两态

- **GIVEN** 详情请求的因子可在准入面板上求值，且与某风格代理的 IC 序列有 ≥ 30 个共同交易日
- **WHEN** 请求详情
- **THEN** `style_exposure` 含逐风格相关系数、主导风格与 |ρ|；|ρ| ≥ 0.7 时 `flagged` 为真
- **AND** 面板不可得（如快照库为空）时 `style_exposure` 为 `null`（前端如实展示「数据不足」，MUST NOT 以 0 冒充）

#### Scenario: IC 就绪度披露

- **GIVEN** 某 active 因子有 4 条 IC 记录、最新 IC 日期为 2026-10-21、打分门槛为 10
- **WHEN** 请求列表或详情
- **THEN** 返回 `ic_points=4`、`min_samples_for_scoring=10`、`ic_window=60`
- **AND** `expected_factor_mode_date` 为按工作日推算的首个可见达标日（示例为 2026-11-02），并在字段说明中标注「按工作日推算、不含节假日」

#### Scenario: 样本不足不触发风格暴露面板

- **GIVEN** 因子仅有 4 条 IC 记录（< 30）
- **WHEN** 请求详情
- **THEN** `style_exposure` 为 `null`
- **AND** 接口 MUST NOT 构建准入面板（详情响应不因快照库大小而阻塞）
