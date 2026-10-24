## MODIFIED Requirements

### Requirement: 五道闸准入管线

候选因子 SHALL 依次通过以下闸口方可置为 `active`，任一闸口失败即拒收并记录失败闸口与原因：(1) 可计算性——在快照全市场上因子值缺失率 ≤ 20%（**研究面值因子除外**：缺失率与 IC 广度按 `analyst-revision-factors` 规定的事件人口口径计——缺失率对因子宣称覆盖的事件人群计、单日有值票数低于广度下限的交易日不计入 IC 样本，MUST NOT 以全市场口径结构性错杀事件因子）；(2) 单因子检验——|RankIC| 均值显著且 |ICIR| ≥ 0.3；(3) 多重检验校正——同批次候选做置换检验并以 BH-FDR（q=0.1）控制；(4) 池级组合增量——加入候选后 active 池**符号归一化等权组合**的样本外 IR 增量 > 0，且与池内任一因子 |IC 相关性| ≤ 0.7；其中「符号归一化」= 组合内各因子（池因子与候选同规则）SHALL 按**训练段 RankIC 符号**定向后等权参与（训练段与闸⑤同一时点切分与边界剔除口径；符号不可定的池因子 SHALL 不参与组合，符号不可定的候选按原向参与并由闸②⑤兜底）；定向符号 SHALL 记入闸④ `detail` 供审计（候选符号各拒绝路径皆记；池定向清单在增量路径记录）；(5) 样本外复核——训练/验证按时间切分，切分口径与 decision-backtest 的时点截断契约一致。研究面值因子（`source="research"`）SHALL 经同一编排（`admit_candidates` 单点）进入本管线，闸②③④⑤逐字适用。

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

#### Scenario: 稀疏事件因子按事件人群计闸①

- **GIVEN** 研究面值因子 `rating_chg_1m` 全市场截面覆盖率约 5%、事件人群内缺失率 8%
- **WHEN** 闸①可计算性评估
- **THEN** 按事件人群口径 8% < 20% 通过，MUST NOT 因全市场 95% 无事件而拒收

### Requirement: 双层查重

入库前 SHALL 执行结构层查重与数值层查重（IC 相关性）；结构层查重对 DSL 因子 SHALL 做 AST 子树同构检测（与库内 candidate/active/degraded 因子比对，retired 允许重提），对研究面值因子（无 AST）SHALL 以查重键 `revision:<feature_key>` 比对（同键存在于库内 candidate/active/degraded 即同构，retired 允许重提）；结构同构者直接拒收，原因码 `STRUCTURAL_DUPLICATE`。

#### Scenario: 换皮因子拒收

- **GIVEN** 库内含 `ts_mean(close, 10)`，新提案为 `ts_mean(close, 11)`
- **WHEN** 执行查重
- **THEN** 提案被拒收，原因码 `STRUCTURAL_DUPLICATE`

#### Scenario: 值因子同键拒收

- **GIVEN** 库内含 active 的 `revision:eps_rev_1m`，研究面日批再次提交同 feature_key 候选
- **WHEN** 执行查重
- **THEN** 候选被拒收，原因码 `STRUCTURAL_DUPLICATE`（retired 状态除外——允许数据积累后重提）

### Requirement: 因子库只读查询

系统 SHALL 提供因子库只读接口：列表 SHALL 返回每个因子的名称、表达式、状态（`active` / `retired`）、来源（`builtin` / `llm` / `external` / `research`）、ICIR、最近闸门结论与衰减标记；详情 SHALL 返回完整 DSL 表达式、IC 序列与事件历史（准入 / 降级 / 查重等事件及时间），并 SHALL 包含**风格暴露披露**（`style_exposure`：与 builtin 风格代理篮子各成员的 IC 序列相关、主导风格及其 |ρ|、n 日与阈值；|主导 ρ| ≥ 0.7 时给出「与标准风格高度重合」标注）——披露为**只读诊断**，SHALL NOT 参与任何准入裁决；不可计算时（面板不可得 / 共同样本不足 / 序列常量）SHALL 返回 `null` 或逐风格 `null`，MUST NOT 以 0 或虚构读数冒充。研究面值因子的「表达式」SHALL 展示其派生定义标识（`revision:<feature_key>` 与一句派生式摘要），MUST NOT 展示为 DSL 表达式或留空。接口 SHALL NOT 暴露任何写操作（因子入库、状态变更、参数调整仍只经五道闸与衰减监控路径）；库为空时 SHALL 返回空列表而非错误。

(Previously: 来源枚举仅 `builtin` / `llm`（与库内实际存在的 `external` 值已经不符）；值因子无展示语义。)

#### Scenario: 列表与详情字段齐备

- **GIVEN** 库中含一条 active/llm 来源因子与一条 retired/builtin 因子
- **WHEN** 请求列表与其中一条详情
- **THEN** 列表含两因子的状态/来源/ICIR/衰减标记；详情含完整表达式、IC 序列与事件历史

#### Scenario: 空库不报错

- **GIVEN** 因子库为空
- **WHEN** 请求列表
- **THEN** 返回 200 与空列表，MUST NOT 以 500 或伪数据冒充

#### Scenario: 风格暴露披露可计算与不可计算两态

- **GIVEN** 详情请求的因子可在准入面板上求值，且与某风格代理的 IC 序列有 ≥ 30 个共同交易日
- **WHEN** 请求详情
- **THEN** `style_exposure` 含逐风格相关系数、主导风格与 |ρ|；|ρ| ≥ 0.7 时 `flagged` 为真
- **AND** 面板不可得（如快照库为空）时 `style_exposure` 为 `null`（前端如实展示「数据不足」，MUST NOT 以 0 冒充）

#### Scenario: 值因子展示派生定义

- **GIVEN** 库中含一条 active 的 `revision:eps_rev_1m` 值因子
- **WHEN** 请求列表与详情
- **THEN** 来源显示 `research`，「表达式」位置展示派生定义标识与摘要，MUST NOT 显示为 DSL 表达式或空白

### Requirement: 跨批次候选检验账本

系统 SHALL 在因子库中维护候选检验账本（`factor_admission_ledger`），逐候选记录其五道闸评估事实：批次标识与来源（monthly_broad/monthly_deep/harvest/builtin/external/research）、表达式、闸③置换检验读数（`p_value`/同批 BH 校正 `p_adj`/同批口径 `q`/同批族大小 `n_hypotheses`）、准入结果（是否入库、`factor_id`、拒收闸口 reason code）与评估时刻。写入 SHALL 由批量准入编排（`admit_candidates`）单点承担——任免入口（月批/收割/builtin/外部导入/研究面）不得各自接线遗漏；未达闸③的候选（预检失败除外）SHALL 记账但 p 值为 NULL。研究面候选的「表达式」列 SHALL 记其查重键（`revision:<feature_key>`）。

账本 SHALL 提供只读读数：库级汇总（总行数 / 已验证数（p 非空）/ 已入库数 / 分批明细）与**全局 BH 反事实预览**（把账本中全部非空 p 值作为一个跨批次家族做一次 BH 校正，输出按给定 q 会拒掉多少——回答「全局账本执行后今日读数会怎样」）。读数 MUST NOT 改变任何准入结论（本 requirement 只保证数据可查询，不执行跨批次校正）。

账本 SHALL NOT 影响既有闸门口径：闸③ 的批内 BH-FDR 校正语义、单批族定义（同批候选数 K）与幂等重跑行为保持不变。库复位（`reset_factor_library`）SHALL 一并清空账本。

#### Scenario: 单批准入逐候选记账

- **GIVEN** 一批候选经 `admit_candidates` 评估（含入库、闸②拒收、闸③拒收各若干）
- **WHEN** 批处理完成
- **THEN** 账本 SHALL 为每个进入批量准入的候选各记一行（同 batch_id、同 source）
- **AND** 入库行 SHALL 携带 factor_id 且 p 值非空；闸③拒收行 SHALL 携带 p 值与该批 `n_hypotheses`/`p_adj`/`q`；闸①②拒收行 p 值 SHALL 为 NULL 且携带拒收闸口 reason code

#### Scenario: 跨批次族查询与全局反事实

- **GIVEN** 账本内已有多个批次、多个已验证候选（p 非空）
- **WHEN** 读取库级汇总与全局 BH 反事实预览（给定 q）
- **THEN** 汇总 SHALL 返回总行数 / 已验证数 / 已入库数 / 分批明细（batch_id、source、批大小、已验证数）
- **AND** 反事实预览 SHALL 返回：按全部已验证 p 值作单一家族 BH 校正后仍显著的数量、其对应的 factor_id 清单（如已入库）
- **AND** 读数 MUST NOT 修改任何行或因子状态

#### Scenario: 未达闸③候选 p 值 NULL 不污染家族

- **GIVEN** 某候选在结构查重或闸①/②被拒（未做置换检验）
- **WHEN** 记账
- **THEN** 该行 p_value/p_adj/n_hypotheses SHALL 为 NULL
- **AND** 全局 BH 反事实预览 SHALL 只消费非空 p 值（NULL 行不计入家族）

#### Scenario: 复位清空账本

- **WHEN** 执行 `reset_factor_library`
- **THEN** 账本 SHALL 与 factors/factor_ic/factor_events 一并清空并返回行数

### Requirement: 衰减监控与自动降级

系统 SHALL 每日重算 active 因子的近 60 日滚动 IC；研究面值因子（`source="research"`）的取值 SHALL 经研究面派生视图即时派生（生效日语义与 IC 广度下限同 `analyst-revision-factors` 准入口径），MUST NOT 走 DSL 求值、MUST NOT 以旧截面值冒充（派生输入不可得当日任务 fail loud）；当某因子滚动 IC 均值连续 20 个交易日为负且 |滚动 ICIR| < 0.1 时，SHALL 自动将其状态置为 `degraded`、从选股打分权重中移除，并落库降级事件（含触发时的滚动指标值）。

#### Scenario: 衰减降级

- **GIVEN** 因子 F 连续 20 个交易日滚动 IC 均值为负且滚动 ICIR = 0.05
- **WHEN** 当日衰减监控日批执行
- **THEN** F 状态变为 `degraded`，降级事件落库含滚动 IC/ICIR 快照
- **AND** 当日选股打分不再包含 F

#### Scenario: 值因子衰减监控走派生视图

- **GIVEN** active 值因子 `revision:eps_rev_1m` 与可达的研报库
- **WHEN** 衰减监控日批执行
- **THEN** 其当日与滚动 IC 均由派生视图当日截面计算（广度不足日计 NaN 不入样本），MUST NOT 以库外旧值或 DSL 求值路径产出
