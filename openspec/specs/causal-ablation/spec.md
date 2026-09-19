# causal-ablation Specification

## Purpose
TBD - created by archiving change revamp-ablation-v2-causal-claims. Update Purpose after archive.
## Requirements
### Requirement: 因果主张登记表准入

消融矩阵的被消融对象 SHALL 先在因果主张登记表中登记：每行含因果主张（没有它会怎样）、失败模式、主指标、判定方式（code | nli | judge）、效应量预期。无登记因果主张的对象 SHALL NOT 进入消融矩阵；登记表中的每条主张 SHALL 有对应实验或明确的悬置标注。指标准入判据为**因果下游检验**：主指标的失败模式必须因果地依赖被消融对象；不满足该判据的通用正确性指标 SHALL NOT 作为层增量主指标。

#### Scenario: 无主张对象被拒

- **WHEN** 向消融矩阵添加一个未登记因果主张的机制或编排层
- **THEN** 登记/配置校验 SHALL 拒绝其进入矩阵并给出原因

#### Scenario: 登记表与实验对齐

- **WHEN** 登记表定稿进入跑批
- **THEN** 每条主张 SHALL 能映射到本轮实验的指标管线，或携带显式悬置标注（含原因与解除条件）

### Requirement: 注入式反幻觉消融（实验族 A）

系统 SHALL 支持注入法消融：对每类污染（值错误、方向错误、单位错误、期次错位、镜像叙事、事件编造、时效过期、价位非法）构造受控污染快照，在被测机制开/关两态下跑同一污染输入，主指标为**逃逸率**——污染值原样出现在终态产物且未被任何告警拦截，且经人工终裁确认为真逃逸。机制开关点 SHALL 登记到具体配置项或函数级别；每个机制 SHALL 只在它因果负责的注入点分层（context 层 / 分析师输出层 / 决策层）上期待效应。注入实现 SHALL 确定性可复现（固定 fixture 优先于 LLM 生成）。

逃逸率统计 SHALL 与校验器误报分开报：校验器 FAIL 误报走四桶拆报口径（blocked / analyst_true_fail / surgical_repaired / verifier_normalized），SHALL NOT 进入逃逸率分母。

#### Scenario: 污染被拦截

- **GIVEN** 某 context 层注入「值错误」污染且 A3 机制开启
- **WHEN** 校验器拦截该污染值（终态产物不含该值或有 FAIL 标记）
- **THEN** 该单元计为「拦下」，逃逸率分子不含它

#### Scenario: 逃逸须终裁确认

- **GIVEN** 机制关闭且污染值出现在终态产物
- **WHEN** 统计逃逸率
- **THEN** 该单元 SHALL 先经人工终裁确认为真逃逸才计入分子；终裁记录 SHALL 落 `tests/validation/`

#### Scenario: 开关只影响目标机制

- **WHEN** 关闭某被测机制
- **THEN** 遥测 SHALL 证实其他机制仍按原状态运行（开关未连带影响共享代码路径上的其他机制）

#### Scenario: 误报不进分母

- **GIVEN** 机制关闭时校验器对未污染 claim 产生 FAIL 误报
- **WHEN** 汇总逃逸率
- **THEN** 误报 SHALL 归入四桶拆报口径，SHALL NOT 被计作「拦下」或「逃逸」

### Requirement: 可测面分型与注入有效性

每类污染 SHALL 登记其**可测面分型**（`fail` / `traceability` / `input_side` / `none`）并在报告逐型披露：

- `fail`：校验链能产 FAIL，拦截 = 被污染 claim 上有 FAIL 或修复；
- `traceability`：机制只产 PASS ↔ UNVERIFIABLE（A6 回声匹配），拦截 = **未静默放行**（被污染 claim 判定非 PASS）。该面 SHALL NOT 被泛化为通用口径——UNVERIFIABLE 是覆盖缺口而非错误，仅该型机制的全部输出面即此；
- `input_side`：机制无校验侧消费者（A7 时效标记只喂分析师 context），改读输入侧 presence 证据（降级告警是否进入模型输入）。该读数 SHALL NOT 被读作拦截率，该型单元 SHALL NOT 进逃逸终裁清单（无「拦截/逃逸」语义），排除项 SHALL 在报告显式披露；
- `none`：无任何已定义可测面 → 报告 blind_spots 显式披露。

污染构造 SHALL 保证**注入有效**：污染版在目标机制开启时产生可检偏差，且该偏差由污染**造成**（原版非 FAIL——否则 FAIL 来自 claim 本就有的覆盖缺口，属伪拦截）。空操作（对齐后有效值不变）、等价换算（污染值恰为已注册的单位/百分比归一等价）、自证出处（把「出处」与污染一并注入使回声必然命中）三类形态 SHALL NOT 作为实验单元。

#### Scenario: 空操作污染被跳过

- **WHEN** 构造某污染单元而其污染值在目标机制开启时不产生可检偏差（如方向翻转在 stated 自带符号时对齐后有效值不变）
- **THEN** 该候选 SHALL 被跳过并另选目标；无可选目标时 SHALL 显式报错，SHALL NOT 静默产出该单元

#### Scenario: 污染不得自证出处

- **GIVEN** 事件编造型污染
- **WHEN** 构造注入载荷
- **THEN** SHALL NOT 把被断言事件的出处（news_list 等源）与 claim 一并注入——否则回声匹配必然命中，单元不携带机制信息

#### Scenario: 输入侧面不冒充拦截

- **WHEN** 汇总可测面为 `input_side` 的污染型指标
- **THEN** 报告 SHALL 分列输入侧 presence 证据与判词，SHALL NOT 把该校验面读数（0 拦截）读作机制无价值

### Requirement: 注入成本结构二分

注入矩阵的实验单元 SHALL 按成本结构分型：**离线重放型**（校验侧机制，对已有产物 JSON 污染后重放校验器，零 LLM 调用）与**真跑型**（输入侧联动与决策层机制，需全管线 run）。跑批预算 SHALL 按分型分别申报；SHALL NOT 以单一「run 等效数」掩盖两类成本的数量级差异。

#### Scenario: 离线重放不消耗 LLM

- **WHEN** 运行离线重放型实验单元
- **THEN** LLM 调用计数 SHALL 为零，产物 SHALL 记录重放来源产物路径

#### Scenario: 预算分型申报

- **WHEN** 提交 P1 跑批预登记
- **THEN** 预算 SHALL 分列离线重放型单元数与真跑型 run 数

### Requirement: 编排层价值指标（实验族 B）

系统 SHALL 为编排层提供与因果主张对应的单元级指标管线：辩论层（B1 风险点增量率：辩论新增且被决策吸收的风险点 / 标的，差集代码提取 + NLI 判吸收；B2 交锋修正率：被 rebuttal_to 锚定且后续轮次修正的观点占比）、决策+风控层（B3 风控数字出处率：执行参数出处检查代码化；B4 价位合法性遥测：sanity 打回率与修正触发率）、整体（B5 pairwise 盲评：同标的同快照两变体报告并排、A/B 位置随机化、judge K=3 取多数）。B6（事后结算）SHALL NOT 作为层间归因证据，仅作 full 变体绝对质量线与未来裁剪的版本分段前后对照。

#### Scenario: 风险点增量提取

- **WHEN** 同标的两变体报告完成
- **THEN** 差集代码 SHALL 提取「辩论新增风险点」原文对，NLI 仅判定是否被决策吸收（二值），判定结果 SHALL 落单元级记录

#### Scenario: pairwise 位置随机化

- **WHEN** 运行 B5 盲评
- **THEN** 每对报告的 A/B 位置 SHALL 随机化，裁决问题 SHALL 锚定因果承诺（如「哪份更有助于做出投资决策」），输出 SHALL 为胜率 + 标的聚类二项 CI

#### Scenario: B6 不进层间归因

- **WHEN** 撰写层价值裁决书
- **THEN** track-record 结算数据 SHALL 仅出现在「full 绝对质量线」与「版本分段对照」语境，SHALL NOT 出现在层增量比较中

### Requirement: 单元级判定与聚类推断

消融判定 SHALL 下沉到单元级（claim / 风险点 / 执行参数 / 价位），每条判定 SHALL 落库：`unit_id, ticker, run, variant, unit_type, judgment, method(code|nli|judge), confidence`。统计推断 SHALL 以标的为重采样簇做聚类 bootstrap（B=10,000，百分位 CI），SHALL 如实报告设计效应（按 ICC 折算有效 n）；SHALL NOT 把重复折叠成中位数再配对。

#### Scenario: 单元级记录齐全

- **WHEN** 一条消融 run 完成判定
- **THEN** 每个判定单元 SHALL 有完整落库记录，method 字段 SHALL 区分 code/nli/judge

#### Scenario: 聚类 bootstrap 尊重标的边界

- **WHEN** 计算 CI
- **THEN** 重采样 SHALL 以标的（而非 run 或单元）为簇，报告 SHALL 披露配对单元数与设计效应折算的有效 n

### Requirement: 预登记与阳性对照

每轮消融跑批前 SHALL 在 `evals/ablation/preregister/` 落预登记文档：主指标、MDE、决策阈值（须附成本换算依据，裸数字阈值视为未登记）、样本量反算依据、停止规则、rubric 版本锁定。每轮跑批 SHALL 携带阳性对照（已知劣化变体，如关闭 verify_citations）；阳性对照测不出显著差异时，本轮全部阴性结果 SHALL 作废（管线灵敏度未证实前阴性结论不可发表）。

#### Scenario: 缺预登记拒绝跑批

- **WHEN** 消融驱动在无有效预登记文档时启动跑批
- **THEN** 驱动 SHALL 拒绝启动并提示预登记路径

#### Scenario: 阳性对照失灵作废本轮

- **GIVEN** 本轮阳性对照变体未测出显著劣化
- **WHEN** 汇总本轮结论
- **THEN** 本轮全部阴性结论 SHALL 标记作废（灵敏度未证实），SHALL NOT 进入结论注册表

### Requirement: LLM 判定的校准门控全覆盖

凡判定方式为 nli 或 judge 的消融指标（含 B1 吸收判定、B2 真修正判定、B3 方向补充、B5 盲评），其 rubric 与人工标注一致率 SHALL ≥80% 才允许进入消融结论；每批 SHALL 抽 ≥20% 单元人工复核，一致率 <80% 时该批判定 SHALL 作废并重校准（复用 `evals/judge_calibration/` 的触发器与流程）。

#### Scenario: 校准未达标阻断结论

- **GIVEN** B1 的 NLI 吸收判定与人工复核一致率为 72%
- **WHEN** 汇总本轮辩论层结论
- **THEN** B1 相关结论 SHALL 作废并触发重校准，SHALL NOT 以未校准判定出裁决结论

### Requirement: 结论注册表生命周期

evals 实验报告 SHALL 在头部携带 `status: active | superseded-by: <path>` 生命周期字段；结论被后续批次推翻或修正时，SHALL 更新原报告 status 并指向新报告，SHALL NOT 静默保留过期结论的 active 状态。docs/evals 索引 SHALL 渲染各报告的状态。

#### Scenario: 结论被取代

- **WHEN** 新批次推翻旧报告的层增量结论
- **THEN** 旧报告头部 SHALL 改为 `status: superseded-by: <新报告路径>`，新报告 SHALL 说明取代原因

#### Scenario: 索引渲染状态

- **WHEN** 渲染 docs/evals 报告索引
- **THEN** 每份报告的 active / superseded 状态 SHALL 可见，superseded 条目 SHALL 指向取代者

