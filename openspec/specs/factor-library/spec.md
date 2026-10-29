# factor-library Specification

## Purpose
TBD - created by archiving change add-factor-library-and-monitor. Update Purpose after archive.
## Requirements
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

### Requirement: 双层查重

入库前 SHALL 执行结构层查重（与库内 candidate/active/degraded 因子做 AST 子树同构检测（retired 允许重提））与数值层查重（IC 相关性）；结构同构者直接拒收，原因码 `STRUCTURAL_DUPLICATE`。

#### Scenario: 换皮因子拒收

- **GIVEN** 库内含 `ts_mean(close, 10)`，新提案为 `ts_mean(close, 11)`
- **WHEN** 执行查重
- **THEN** 提案被拒收，原因码 `STRUCTURAL_DUPLICATE`

### Requirement: 分桶保种

active 因子 SHALL 按 category 分桶独立保留 top-k（k=10，按池级增量贡献排序）；新因子入库导致桶内超容时，桶内末位降级为 `retired`，不影响其他桶。

#### Scenario: 桶内末位淘汰

- **GIVEN** 动量桶已满 10 个 active 因子，新动量因子入库且增量贡献高于桶内末位
- **WHEN** 入库完成
- **THEN** 动量桶仍为 10 个 active，原末位转为 `retired`
- **AND** 其他桶的 active 集合不变

### Requirement: 衰减监控与自动降级

系统 SHALL 每日重算 active 因子的近 60 日滚动 IC；当某因子滚动 IC 均值连续 20 个交易日为负且 |滚动 ICIR| < 0.1 时，SHALL 自动将其状态置为 `degraded`、从选股打分权重中移除，并落库降级事件（含触发时的滚动指标值）。

#### Scenario: 衰减降级

- **GIVEN** 因子 F 连续 20 个交易日滚动 IC 均值为负且滚动 ICIR = 0.05
- **WHEN** 当日衰减监控日批执行
- **THEN** F 状态变为 `degraded`，降级事件落库含滚动 IC/ICIR 快照
- **AND** 当日选股打分不再包含 F

### Requirement: builtin 因子注册路径

系统 SHALL 内置手工因子基线包（20 日动量、5 日反转、换手率、20 日波动率、市值代理），以 `category=builtin` 注册入因子库；builtin 因子免 LLM 提案环节，但 SHALL 同样通过五道闸方可置为 active。注册 SHALL 由日批任务「内置因子注册」（工作日 15:50，快照与因子衰减监控之后、结算链之前）承载，走统一日批通道（调度 / 运行历史 / 手动补跑 / 单飞锁）；库内已存在同表达式条目（任意状态，含 retired/degraded；**`rejected` 拒绝池行除外——被拒 builtin 因子视为缺失、次日可重试**）SHALL 跳过（MUST NOT 复活 retired 条目、MUST NOT 重复入库）；存在缺失条目时 SHALL 以快照库最近面板逐条过闸，面板不可得 SHALL 上抛（任务记 failed，MUST NOT 以空面板放行注册）；摘要 SHALL 记录 `{registered, rejected, skipped, reasons}`（未过闸者逐条原因码）。

#### Scenario: builtin 因子受同等准入约束

- **GIVEN** builtin 反转因子在准入评估中 ICIR = 0.12
- **WHEN** 执行准入管线
- **THEN** 该因子被拒收于闸②，与 LLM 提案因子适用同一阈值

#### Scenario: 日批注册入口幂等（含 retired 不回填）

- **GIVEN** 因子库中已存在全部五条 builtin 条目（任意状态，含 retired）
- **WHEN** 内置因子注册日批执行
- **THEN** 五条均被跳过（摘要 `skipped=5 · registered=0 · rejected=0`），MUST NOT 复活 retired 条目、MUST NOT 重复入库
- **AND** 跳过路径 SHALL NOT 构造面板（快照库不可达亦不失败）

#### Scenario: 拒绝池行不构成已存在（被拒 builtin 可重试）

- **GIVEN** 某条 builtin 条目此前过闸失败、库内存在其 `rejected` 行
- **WHEN** 内置因子注册日批执行
- **THEN** 该条目 SHALL 视为缺失并重新过闸（MUST NOT 因 `rejected` 行存在而被跳过）

#### Scenario: 缺失条目过闸注册

- **GIVEN** 因子库存在缺失条目且快照面板可用
- **WHEN** 内置因子注册日批执行
- **THEN** 缺失条目逐条经五道闸：过闸者以 `category=builtin` / `source=builtin` 入库为 active，未过者摘要 `reasons` 记录逐条原因码
- **AND** 已存在条目（任意状态，`rejected` 除外）计入 `skipped`，MUST NOT 重复提交准入

#### Scenario: 面板不可得 fail loud

- **GIVEN** 因子库存在缺失条目且快照库为空 / 历史不足
- **WHEN** 内置因子注册日批执行
- **THEN** 任务上抛（运行历史记 failed），MUST NOT 以空面板放行注册

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

#### Scenario: IC 就绪度披露

- **GIVEN** 某 active 因子有 4 条 IC 记录、最新 IC 日期为 2026-10-21、打分门槛为 10
- **WHEN** 请求列表或详情
- **THEN** 返回 `ic_points=4`、`min_samples_for_scoring=10`、`ic_window=60`
- **AND** `expected_factor_mode_date` 为按工作日推算的首个可见达标日（示例为 2026-11-02），并在字段说明中标注「按工作日推算、不含节假日」

#### Scenario: 样本不足不触发风格暴露面板

- **GIVEN** 因子仅有 4 条 IC 记录（< 30）
- **WHEN** 请求详情
- **THEN** `style_exposure` 为 `null` 且 `monotonicity` 为 `null`
- **AND** 接口 MUST NOT 构建准入面板（详情响应不因快照库大小而阻塞）

#### Scenario: 分层单调性观测读数可算与不可算两态

- **GIVEN** 某 active 因子 IC 点数 ≥ 30 且可在准入面板上求值
- **WHEN** 请求详情
- **THEN** `monotonicity` 含 `passed` / `spearman` / `q10_q1` / `decile_returns` / `valid_days` / `threshold`，为观测读数（不挂任何处置）
- **AND** 快照库为空或表达式不可求值时 `monotonicity` 为 `null`（前端如实展示「数据不足」，MUST NOT 以 0 冒充）

### Requirement: 准入面板深度下限

五道闸准入评估（本地提案、builtin 注册、外部导入三入口共用）所用面板 SHALL 以最近不少于 500 个交易日为下限深度构造（独立准入面板常量，不影响衰减监控面板口径）；快照库可用历史不足下限时准入任务 SHALL fail loud（错误信息含面板深度不足与补历史指引），MUST NOT 以浅面板放行评估。

(Previously: 下限为 250 个交易日——闸② ICIR 估计标准误 ≈0.063，同族因子排序仍可能落在噪声带；20 日窗因子闸①缺失率 15.4%，60 日窗 29.8% 被结构性拒收。)

#### Scenario: 深度不足 fail loud

- **GIVEN** 快照库仅 400 个交易日可用
- **WHEN** 任一准入入口（提案月批 / 基线注册 / 外部导入）执行
- **THEN** 任务记 failed，错误信息含准入面板深度不足提示
- **AND** 因子库零新增、零评估（MUST NOT 以浅面板出闸口结论）

#### Scenario: 深面板长窗因子可算

- **GIVEN** 准入面板 ≥500 个交易日、候选为 20 日窗因子
- **WHEN** 闸①可计算性评估执行
- **THEN** 热身期缺失占比落在阈值口径内（≈4% + 覆盖缺口；实测 20 日窗 11.1%、60 日窗 18.4%，均 < 20%），该候选以真实覆盖与 IC 质量参与后续闸口裁决，不再被结构性拒收

### Requirement: 跨批次候选检验账本

系统 SHALL 在因子库中维护候选检验账本（`factor_admission_ledger`），逐候选记录其五道闸评估事实：批次标识与来源（monthly_broad/monthly_deep/harvest/builtin/external）、表达式、闸③置换检验读数（`p_value`/同批 BH 校正 `p_adj`/同批口径 `q`/同批族大小 `n_hypotheses`）、准入结果（是否入库、`factor_id`、拒收闸口 reason code）与评估时刻。写入 SHALL 由批量准入编排（`admit_candidates`）单点承担——任免入口（月批/收割/builtin/外部导入）不得各自接线遗漏；未达闸③的候选（预检失败除外）SHALL 记账但 p 值为 NULL。

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

### Requirement: IC 采集记录与缺口回填

衰减监控日批 SHALL 为每个 active 因子记录「面板窗口内、`as_of` 及以前」的**全部**可算 RankIC 交易日中尚缺的日期（缺口回填）；`record_ic` 幂等 upsert，已存在日期 SHALL NOT 重复重写；运行摘要 SHALL 含 `days_recorded`（本次写入的 IC 行数），`recorded`（有可算 IC 的因子数）语义不变。超出监控面板窗口（`DECAY_WINDOW+30` 交易日）的更早缺口 MUST NOT 虚构回补（诚实留洞）。无任何可算日的因子照旧计入 `skipped`。

#### Scenario: 例行日恰好新增一条

- **GIVEN** active 因子 F 昨日及以前的 IC 均已记录，今日面板出现新的可算 IC 日
- **WHEN** 衰减监控日批执行
- **THEN** 该日被记录，`days_recorded` 含这一条，且不重写任何已存在日期

#### Scenario: 多日中断后缺口一次补齐

- **GIVEN** active 因子 F 的 IC 序列缺失面板窗口内的中间 20 个可算日（监控曾连缺多个交易日）
- **WHEN** 衰减监控恢复后的首次运行执行
- **THEN** 20 个缺口日全部补写（`days_recorded=20`），序列在面板窗口内无洞
- **AND** 同一运行重跑 `days_recorded=0`（已存在日期不重写，幂等）

#### Scenario: 无可算日照旧跳过

- **GIVEN** active 因子 F 在 `as_of` 前无可算 IC 日（窗口不足/全 NaN）
- **WHEN** 衰减监控日批执行
- **THEN** F 计入 `skipped`，不写任何 IC 行

### Requirement: 拒绝池持久化

批量准入编排（`admit_candidates`）SHALL 在候选被五道闸拒收（闸①–⑤，含批内二次结构查重）时，将该候选持久化为因子库 `factors` 表 `status='rejected'` 行（单点承担，月批提案 / builtin 注册 / 收割导入三入口自动一致）：行 SHALL 携带表达式、假设、类别、来源与 `ast_json`，并 SHALL 落一条 created 事件，`detail` 携带拒收闸口编号、原因码与判定读数（与准入结论同源）。结构同构的重复拒收（同批或跨批）SHALL 更新既有 `rejected` 行（刷新 `updated_at` 并追加事件），MUST NOT 重复插入。预检失败与未过转译的候选（无 DSL 表达式，即 `untranslatable` / `transcription_error` / `errors` 桶）MUST NOT 落库（维持现状，仍只存在于收割状态文件与反馈 payload）。

#### Scenario: 闸②拒收候选持久化为拒绝池行
- **WHEN** 某候选在准入编排中于闸②（单因子检验）被拒收
- **THEN** 因子库 SHALL 新增一行 `status='rejected'`，携带该候选的表达式、假设、类别、来源与 `ast_json`
- **AND** SHALL 落一条事件，`detail` 含 `rejected_gate=2`、原因码与判定读数（IC 均值 / ICIR / 单调性读数等）

#### Scenario: 结构同构重复拒收按 upsert 合并
- **WHEN** 与既有 `rejected` 行结构同构的候选再次被拒收（同批逐字重复或跨批重提后被拒）
- **THEN** 系统 SHALL 更新既有 `rejected` 行的 `updated_at` 并追加事件，MUST NOT 新增第二行

#### Scenario: 无 DSL 候选不落库
- **WHEN** 候选未过转译（`untranslatable` / `transcription_error`）或预检失败
- **THEN** 因子库 MUST NOT 为其新增任何行（该类候选仍只留存于收割状态文件）

#### Scenario: builtin 注册跳过判定不把拒绝池行视为已存在
- **WHEN** builtin 日批注册遇到库内存在同表达式 `rejected` 行的内置因子
- **THEN** 该因子 SHALL 照常进入五道闸重试（MUST NOT 因 `rejected` 行存在而被跳过）

### Requirement: 拒绝池不参与查重与既有读路径

`rejected` 行 MUST NOT 参与结构层查重范围（查重维持 `candidate/active/degraded`，`retired` 允许重提的既有语义不变）——同构候选 SHALL 可被重新提案并再次走全量五道闸。`rejected` 行 MUST NOT 参与分桶保种配额、池级组合增量评估与选股打分。因子库只读列表 SHALL NOT 返回 `rejected` 行（对外查询行为不变，无前端变更）。库复位（`reset_factor_library`）SHALL 一并清空拒绝池。

#### Scenario: 拒绝池行不阻断重提
- **WHEN** 生成侧重新提出与某 `rejected` 行结构同构的候选
- **THEN** 闸0 结构查重 SHALL 放行（`rejected` 行不在比对对象中），该候选按当前数据窗口重走全量五道闸

#### Scenario: 只读列表不返回拒绝池行
- **WHEN** 调用因子库只读列表接口
- **THEN** 返回结果 SHALL NOT 包含 `status='rejected'` 的行（其余行为不变）

#### Scenario: 拒绝池行不参与桶配额与打分
- **WHEN** 分桶保种按 category 结算容量、或池级组合增量/选股打分读取池因子
- **THEN** 参与者 SHALL 仅限 active 因子，`rejected` 行 MUST NOT 计入

### Requirement: 拒绝池 IC 日批跟踪

衰减监控日批 SHALL 在 active 因子缺口回填之外，额外对全部 `rejected` 行执行同口径的缺口回填式 RankIC 记录（复用 `record_ic` 幂等 upsert：面板窗口内可算且尚缺的交易日，超出窗口的更早缺口不虚构回补）。`rejected` 行 MUST NOT 参与衰减判定（无滚动判决、无降级、无任何状态迁移）。运行摘要 SHALL 新增拒绝池读数（检查数 / 有可算 IC 数 / 本次写入 IC 行数），既有 `recorded` / `days_recorded` / `degraded` 等字段语义不变。面板不可得或 `as_of` 不在面板内 SHALL 与现状一致 fail loud。

#### Scenario: 日批为拒绝池行回填缺口 IC
- **WHEN** 衰减监控日批运行且面板可用，库内存在 `rejected` 行
- **THEN** 系统 SHALL 为每个 `rejected` 行按 active 同口径写入窗口内尚缺交易日的 RankIC（幂等，已存在日期不重写）

#### Scenario: 拒绝池行不触发衰减判定
- **WHEN** 某 `rejected` 行滚动 IC 连续为负且 ICIR 低于阈值
- **THEN** 系统 SHALL NOT 对其执行降级或任何状态迁移（`degraded` 判定仅适用于 active 因子）

#### Scenario: 摘要携带拒绝池读数且既有字段不变
- **WHEN** 衰减监控日批完成
- **THEN** 摘要 SHALL 包含拒绝池的检查数 / 有可算 IC 数 / 本次写入行数
- **AND** 既有 `recorded`（有可算 IC 的 active 因子数）/ `days_recorded` / `degraded` 语义保持不变

### Requirement: 存量拒绝候选回填

系统 SHALL 提供一次性回填脚本：从收割状态文件的已处理映射中，将拒收于五道闸（`status='rejected'` 且带 DSL 表达式与 `ast_json`）的既有候选按**原裁决原样**导入拒绝池（事件 `detail` 携带状态文件中记录的拒收闸口与原因；不重新过闸、不重新裁决）。导入 SHALL 沿用拒绝池 upsert 幂等语义（同构已有行则更新，MUST NOT 重复插入）；`untranslatable` / `transcription_error`（无 DSL）MUST NOT 导入。脚本 SHALL 支持显式指定库与状态文件路径，MUST NOT 依赖生产默认路径隐式生效。

#### Scenario: 回填导入存量闸口拒收候选
- **WHEN** 对含历史 `rejected` 记录（带表达式）的收割状态文件执行回填脚本
- **THEN** 拒绝池 SHALL 新增对应行与事件（原裁决原样：原闸口编号与原因码），摘要报告导入 / 更新 / 跳过条数

#### Scenario: 回填幂等且不导入无表达力候选
- **WHEN** 对同一状态文件重复执行回填，或状态文件含 `untranslatable` / `transcription_error` 记录
- **THEN** 重复执行 SHALL 零新增（同构行命中 upsert 更新路径或跳过），无 DSL 记录 SHALL 全部跳过

### Requirement: 拒绝池复苏重评通道

系统 SHALL 提供拒绝池复苏日批（`factor_revival_daily`，周一至周五 17:10，零 LLM 调用）：对 `rejected` 行按以下条件筛选复苏候选——(1) 库内 IC 记录数 ≥ `IC_WINDOW`（60）；(2) 最近 `IC_WINDOW` 个 IC 点的滚动 |ICIR| ≥ `MIN_ABS_ICIR`（与闸②同值，即「复苏静默线」）；(3) 冷却期外：该行最近一次 `created` 或 `re_rejected` 事件早于 `REVIVAL_COOLDOWN_DAYS`；(4) 单次运行提交数 ≤ `REVIVAL_DAILY_CAP`（同分并列按滚动 |ICIR| 降序、再按 factor_id 升序取前 N）。复苏提交 SHALL 走外部导入同一路径（DSL 预检 → 闸⓿~⑤，检验账本 `ledger_source="revival"`），表达式/假设/类别取自该 rejected 行原值。`rejected` SHALL 保持终态：过闸者以**新 candidate 行**入库，rejected 行不迁移、不删除；再拒者经既有 `record_rejected_factor` 同构 upsert 追加 `re_rejected` 事件（闸口与读数如实，冷却期自该事件自动重算）。复苏静默线 SHALL 只作提交预筛，MUST NOT 构成任何准入捷径——五道闸 SHALL 是唯一准入权威。无可复苏候选时 SHALL 零摘要零面板构建；存在可复苏候选而快照面板不可得时 SHALL fail loud（job 记 failed）。job 摘要 SHALL 含 `checked` / `eligible` / `cooldown_skipped` / `submitted` / `admitted` / `re_rejected` 读数。

#### Scenario: 复苏命中且过闸

- GIVEN 某 rejected 行库内 IC 90 点、近 60 点滚动 |ICIR| = 0.42、无 re_rejected 事件
- WHEN factor_revival_daily 运行
- THEN 该行表达式经外部导入路径提交五道闸
- AND 过闸后以新 candidate→active 行入库，原 rejected 行保持 rejected 状态不变
- AND 检验账本记录 ledger_source="revival"

#### Scenario: 冷却期内跳过

- GIVEN 某 rejected 行最近一次 re_rejected 事件在 10 天前（< 冷却期 30 天），且滚动 |ICIR| 超线
- WHEN factor_revival_daily 运行
- THEN 该行计入 cooldown_skipped，不提交

#### Scenario: 再拒后冷却重置

- GIVEN 某 rejected 行滚动 |ICIR| 超线且冷却期外，本次提交被闸④拒收
- WHEN 准入结果回写
- THEN 原 rejected 行追加 re_rejected 事件（含闸口与读数）
- AND 该行冷却期自该事件重算（次日不再被提交）

#### Scenario: 无可复苏候选零面板

- GIVEN 全部 rejected 行 IC 点数不足 60 或滚动 |ICIR| 低于静默线
- WHEN factor_revival_daily 运行
- THEN 摘要 submitted=0 且不构建 500 日准入面板

#### Scenario: 单次运行上限

- GIVEN eligible=8 > REVIVAL_DAILY_CAP=5
- WHEN factor_revival_daily 运行
- THEN 按滚动 |ICIR| 降序（并列按 factor_id 升序）提交 5 条
- AND 摘要 eligible=8、submitted=5

#### Scenario: 面板不可得 fail loud

- GIVEN 存在可复苏候选且快照库不可用
- WHEN factor_revival_daily 运行
- THEN job 以异常上抛（run_job 记 failed），不静默跳过

