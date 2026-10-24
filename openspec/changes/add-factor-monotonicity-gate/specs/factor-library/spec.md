## MODIFIED Requirements

### Requirement: 五道闸准入管线

候选因子 SHALL 依次通过以下闸口方可置为 `active`，任一闸口失败即拒收并记录失败闸口与原因：(1) 可计算性——在快照全市场上因子值缺失率 ≤ 20%；(2) 单因子检验——|RankIC| 均值显著且 |ICIR| ≥ 0.3，**且分层单调性达标**：在准入面板上按因子值十分位构建等权组合（依据面板前复权 close 逐日收益；值因子在其有值票上构建分位），|分位序与分位累计收益的 Spearman| ≥ `MIN_DECILE_MONOTONICITY`（0.6）**且** Spearman 符号与 IC 均值一致（负向因子按其方向单调同样通过——方向由 IC 符号锚定；端点差 Q10 − Q1 记入 `detail` 作披露字段）——不达标者拒收，原因码 `WEAK_MONOTONICITY`（仍记 `rejected_gate=2`），读数（`spearman` / `q10_q1`）记入 `detail.monotonicity`；(3) 多重检验校正——同批次候选做置换检验并以 BH-FDR（q=0.1）控制；(4) 池级组合增量——加入候选后 active 池**符号归一化等权组合**的样本外 IR 增量 > 0，且与池内任一因子 |IC 相关性| ≤ 0.7；其中「符号归一化」= 组合内各因子（池因子与候选同规则）SHALL 按**训练段 RankIC 符号**定向后等权参与（训练段与闸⑤同一时点切分与边界剔除口径；符号不可定的池因子 SHALL 不参与组合，符号不可定的候选按原向参与并由闸②⑤兜底）；定向符号 SHALL 记入闸④ `detail` 供审计（候选符号各拒绝路径皆记；池定向清单在增量路径记录）；(5) 样本外复核——训练/验证按时间切分，切分口径与 decision-backtest 的时点截断契约一致。单调性判据 SHALL 只对**新候选**生效：库内存量 active 因子不回溯翻案，其详情接口 SHALL 展示单调性读数为观测项（MUST NOT 触发任何处置；处置仍走衰减监控）。

#### Scenario: 噪声因子拦截

- **GIVEN** 一批 50 个由随机数生成的无意义因子表达式
- **WHEN** 执行准入管线（固定随机种子）
- **THEN** 入库（置为 active）数量 < 5
- **AND** 每个被拒因子记录被拒闸口编号

#### Scenario: 冗余因子拦截

- **GIVEN** active 池已含因子 A，候选因子 B 与 A 的 IC 相关性为 0.92 且 B 单因子 ICIR = 0.5
- **WHEN** 执行闸④
- **THEN** B 被拒收，原因码 `REDUNDANT_WITH_POOL`，并记录相关对象 A

#### Scenario: 稳定负向因子按训练段符号定向准入

- **GIVEN** 候选因子在训练段与验证段的 RankIC 均值同为负且 |ICIR| 均 ≥ 0.3（强反转形态），空池
- **WHEN** 执行闸④
- **THEN** 该因子按训练段符号（−1）定向参与组合，验证段增量 > 0 → 闸④通过（`detail.orientation == -1`）
- **AND** 若训练段符号与验证段符号相反，闸④增量 ≤ 0 以 `NO_POOL_INCREMENT` 先拒（空池下定向增量 = 训练符号 × 验证段 IR；闸⑤的「两段同号」门槛原样保留，对经闸④者继续兜底）

#### Scenario: 中段倒挂因子被单调性拒收

- **GIVEN** 候选因子 |ICIR| = 0.42（过阈值），但十分位累计收益仅端点两档方向正确、中段乱序，分位序-收益 Spearman = 0.31
- **WHEN** 执行闸②
- **THEN** 候选被拒收，原因码 `WEAK_MONOTONICITY`（`rejected_gate=2`），`detail.monotonicity` 含 `spearman=0.31` 与端点差读数

#### Scenario: 端点同向且分位单调的因子通过

- **GIVEN** 候选因子 |ICIR| = 0.35、IC 均值为正、十分位收益沿分位递增（Spearman = 0.78，Q10 − Q1 > 0）
- **WHEN** 执行闸②
- **THEN** 闸②通过，`detail.monotonicity` 记录读数供审计

#### Scenario: 负向因子按其方向单调同样通过

- **GIVEN** 候选因子 IC 均值为负、十分位收益沿分位递减（Spearman = −0.95，与 IC 同向）
- **WHEN** 执行闸②
- **THEN** 闸②通过（|Spearman| ≥ 0.6 且方向与 IC 一致；量价反转族的生产实测形态——2026-10-24 sanity 锚 7/10 因子即此形态，按旧「正相关」口径曾被整体误杀）

#### Scenario: 存量 active 不回溯

- **GIVEN** 库内既有 active 因子按旧口径（无单调性判据）入库
- **WHEN** 本判据上线后执行任意日批
- **THEN** 存量因子状态不变（MUST NOT 回溯翻案），详情接口展示其单调性观测读数（不挂任何处置）

### Requirement: 因子库只读查询

系统 SHALL 提供因子库只读接口：列表 SHALL 返回每个因子的名称、表达式、状态（`active` / `retired`）、来源（`builtin` / `llm`）、ICIR、最近闸门结论与衰减标记；详情 SHALL 返回完整 DSL 表达式、IC 序列与事件历史（准入 / 降级 / 查重等事件及时间）、**分层单调性观测读数**（`monotonicity`：分位序-收益 Spearman 与端点差；存量因子为观测项，不参与处置），并 SHALL 包含**风格暴露披露**（`style_exposure`：与 builtin 风格代理篮子各成员的 IC 序列相关、主导风格及其 |ρ|、n 日与阈值；|主导 ρ| ≥ 0.7 时给出「与标准风格高度重合」标注）——披露为**只读诊断**，SHALL NOT 参与任何准入裁决；不可计算时（面板不可得 / 共同样本不足 / 序列常量）SHALL 返回 `null` 或逐风格 `null`，MUST NOT 以 0 或虚构读数冒充。接口 SHALL NOT 暴露任何写操作（因子入库、状态变更、参数调整仍只经五道闸与衰减监控路径）；库为空时 SHALL 返回空列表而非错误。

(Previously: 详情不含分层单调性读数。)

#### Scenario: 列表与详情字段齐备

- **GIVEN** 库中含一条 active/llm 来源因子与一条 retired/builtin 因子
- **WHEN** 请求列表与其中一条详情
- **THEN** 列表含两因子的状态/来源/ICIR/衰减标记；详情含完整表达式、IC 序列、单调性读数与事件历史

#### Scenario: 空库不报错

- **GIVEN** 因子库为空
- **WHEN** 请求列表
- **THEN** 返回 200 与空列表，MUST NOT 以 500 或伪数据冒充

#### Scenario: 风格暴露披露可计算与不可计算两态

- **GIVEN** 详情请求的因子可在准入面板上求值，且与某风格代理的 IC 序列有 ≥ 30 个共同交易日
- **WHEN** 请求详情
- **THEN** `style_exposure` 含逐风格相关系数、主导风格与 |ρ|；|ρ| ≥ 0.7 时 `flagged` 为真
- **AND** 面板不可得（如快照库为空）时 `style_exposure` 为 `null`（前端如实展示「数据不足」，MUST NOT 以 0 冒充）
