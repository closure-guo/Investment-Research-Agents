# Evaluation Specification

## Purpose

定义管线产出的评估能力域。评估以 langfuse dataset experiment 为核心执行（`evals/run.py`：`run_experiment` 是实验唯一入口，无 langfuse 时显式报错不降级），输出四个 judge 维度（report_relevance / debate_quality / decision_grounding / consistency）加确定性指标（section_coverage / ticker_match）。交易决策的论据可追溯性由 `evidence_refs` 结构化引用支撑，使 judge 可核对「决策论据 → 来源」而非仅凭自由文本推断。
## Requirements
### Requirement: 交易决策论据结构化引用

系统 SHALL 让 Trader 的决策输出包含结构化论据引用 `evidence_refs`：每条引用含 `claim`（论据）与 `source`（来源，枚举 technical/macro/fundamental/sentiment/debate_bull/debate_bear/research_manager）。Trader prompt SHALL 强制「reasoning 中的每条例据对应一条 evidence_ref，且数值与来源一致」，使决策论据可被机器/评估器核对。

#### Scenario: 决策输出带论据引用

- **GIVEN** Trader 基于分析师报告做出决策
- **WHEN** 输出 TradeDecision
- **THEN** JSON SHALL 包含 `evidence_refs` 列表（每项 `{claim, source}`）
- **AND** `reasoning` 的主要论据 SHALL 能在 `evidence_refs` 中找到对应项

#### Scenario: 解析与序列化兼容

- **WHEN** 下游（risk_judge、报告生成、judge 变量提取）消费 TradeDecision
- **THEN** `evidence_refs` SHALL 被 Pydantic 解析并随 `_serialize_decision` 输出
- **AND** 既有字段（action/confidence/reasoning/position_size 等）行为不变

#### Scenario: Risk Judge 回显引用

- **GIVEN** Risk Judge 与 Trader 共用 TradeDecision schema，且 judge 变量 `trade_decision` 取 `final_trade_decision`（Risk Judge 输出）
- **WHEN** Risk Judge 输出最终决策
- **THEN** 采纳自交易方案的论据 SHALL 原样保留其 `evidence_refs`（claim 与 source 不变）
- **AND** SHALL 不虚构来源；无可对应来源的论据可不引用（`evidence_refs` 允许为空数组）

### Requirement: decision_grounding 评估

系统 SHALL 在 decision_grounding judge 中输入 TradeDecision 的 `evidence_refs`，并让 judge 可核对「决策论据是否在对应 source 中有出处」。judge 输入 `trade_decision` SHALL 包含 evidence_refs；`analyst_reports` 输入 SHALL 保留关键数值（不被摘要抹掉核对所需信息）。rubric（v8 起）SHALL 显式声明归属判例：同一评判在多个来源（如 debate_bear 与 research_manager）均有原话时，evidence_refs 引用任一真实来源即合法，judge SHALL NOT 因未选「最早」或「主要」来源而按归属错安扣分。

#### Scenario: 有引用可核对

- **GIVEN** TradeDecision 含 evidence_refs（如 `{claim: "ROE 3.4%", source: "fundamental"}`）
- **WHEN** 运行 decision_grounding judge
- **THEN** judge SHALL 按「evidence_refs 的 claim 与 source 是否对得上、reasoning 是否全部有引用」给分
- **AND** 全部对得上 → 高分（4-5）；source 缺失或数值不符 → 低分（1-2）

#### Scenario: 无引用降级

- **WHEN** TradeDecision 无 evidence_refs（旧格式/解析失败）
- **THEN** judge SHALL 按原 rubric 从自由文本推断（不因缺字段报错）

#### Scenario: 多来源同判归属合法

- **GIVEN** evidence_refs 某条 claim（如「多方环比修复拐点论尚缺同比转正证据」）在 debate_bear 与 research_manager 的结论中均有原话
- **WHEN** 该条 source 标注为其中任一真实来源
- **THEN** judge SHALL 视为归属合法，SHALL NOT 扣分
- **AND** 仅当 claim 在所有被标来源中均无原话时，才按归属错安处理（降 1 档、不高于 4）

### Requirement: 确定性评估器

系统 SHALL 提供零 token 成本的确定性评估器，对结构化 / 可重算维度打分，可进 CI、可重复执行。至少包含 `section_coverage`（必备章节覆盖率）与 `ticker_match`（标的解析正确性）；已有 `citation_pass` 同属此类。确定性评估器 SHALL NOT 调用 LLM。

#### Scenario: section_coverage 评估

- **GIVEN** Dataset item 的 `expected_output.must_cover` 列出必备章节（如 `["偿债能力", "盈利能力", "技术面", "风险提示"]`）
- **WHEN** 运行 section_coverage 评估器
- **THEN** SHALL 返回 `{name: "section_coverage", value: <0-1 覆盖率>, comment: <缺失章节或 null>}`
- **AND** 章节命中 SHALL 经同义词词典匹配（非裸字符串 `in`），覆盖中文表述差异

#### Scenario: ticker_match 评估

- **GIVEN** `expected_output.ticker` 指定预期标的代码
- **WHEN** 运行 ticker_match 评估器
- **THEN** SHALL 返回 `{name: "ticker_match", value: 1.0 | 0.0}`

#### Scenario: expected 缺省时跳过

- **GIVEN** item 无 `must_cover` 或无 `ticker`
- **THEN** 对应评估器 SHALL 返回 null（不计入该维度），不报错

#### Scenario: 不调用 LLM

- **WHEN** 确定性评估器执行
- **THEN** SHALL NOT 发起任何 LLM 调用

### Requirement: LLM-as-Judge 评估器与 rubric 标准

系统 SHALL 提供 LLM-as-Judge 评估器，对主观质量维度按 rubric 打分，至少包含 `report_relevance`（报告切题度）、`debate_quality`（辩论实质交锋）、`decision_grounding`（决策论据前文支撑）、`consistency`（跨层结论一致性）。每个 Judge SHALL 由明确 rubric 驱动，输出 JSON `{score, reason}`，裁判模型 SHALL 使用 `deepseek-chat`（非生成模型），裁判调用 SHALL 出现在 Langfuse trace 中并以 `langfuse-llm-as-a-judge` 环境标记独立核算成本。rubric SHALL 显式声明"不以长度论优劣"以抑制冗长偏置。rubric 版本号 SHALL 随判例或档位语义变更递增（v8 起：debate_quality v4、decision_grounding v8），并记录于 `RUBRIC_VERSIONS`。

judge 输入材料 SHALL 满足：debate_quality 的 `debate_history` SHALL 在原始辩论发言之前附骨架行——交锋覆盖统计（各方论点被回应数 N/M）与收敛信号摘要（第二轮起双方立场靠拢/共识点/核心分歧保留项）；consistency 的材料 SHALL 含【Trader 方案】节（TradeDecision 的 action / position_size / 价位与触发条件的结构化渲染），使「Trader 方案 → Risk Judge 裁决」是否静默推翻可核对。

**debate_quality 结构化枚举与程序封顶（v6）**：prompt 内强制枚举经 round10 实测不足以稳定生效（LLM 自我枚举随机，8 行重判仍有漏判与回归），故 5 分档判据 SHALL 由代码承担——debate_quality 的输出契约 SHALL 在 JSON 中额外含 `points` 数组（每项 `{header, type}`，`header` 为论点标头原文，`type ∈ {data, qualitative}`）；`run_judge` SHALL 在解析后按枚举结果裁决：存在任一 `type="qualitative"` 时分数 SHALL 被压至 `min(score, 4)`，该封顶 SHALL NOT 依赖 LLM 是否自觉扣分，且 SHALL 在结果中标记 `cap_applied` 与 `qualitative_points` 数量。枚举缺失或格式非法时 SHALL 保持分数不变（fail-open）并标记 `enumeration_missing=true`——该标记 SHALL 随分数落库（eval 报告 comment / 消融 run 记录），SHALL NOT 静默视为「无纯定性论点」。其余维度输出契约与结果形状 SHALL 不变。

#### Scenario: report_relevance 评估

- **GIVEN** 用户查询 `{{query}}` 与最终报告 `{{report}}`
- **WHEN** 运行 report_relevance Judge
- **THEN** SHALL 按 1-5 rubric 打分（5 = 完全切题，1 = 完全答非所问）
- **AND** 输出 JSON `{score, reason}`

#### Scenario: debate_quality 评估

- **GIVEN** 辩论记录 `{{debate_history}}`（含交锋覆盖骨架行与收敛信号摘要；依赖 delta `agent-trace-content-fidelity` 的 span 内容保真）
- **WHEN** 运行 debate_quality Judge
- **THEN** SHALL 按 1-5 rubric 打分（5 = 双方逐条交锋且引证据，1 = 单方输出或空洞）
- **AND** v4 起 5 分档 SHALL 执行判例：论点列表（R1/R2 标头）中任一条为纯定性表述（无数据/事实支撑的断言，含「历史上……」类无样本论据）即降 4，即使该回应其余部分数据密集
- **AND** v6 起该判例 SHALL 由程序按 `points` 枚举执行（见下）

#### Scenario: 纯定性论点被程序封顶

- **GIVEN** judge 返回 `score=5` 且 `points` 中含任一 `type="qualitative"` 的论点标头
- **WHEN** 解析 judge 结果
- **THEN** 最终分数 SHALL 为 4（`cap_applied=true`，`qualitative_points ≥ 1`）
- **AND** SHALL NOT 因 LLM 给了 5 而放行（round10 实测漏判样本的兜底路径）

#### Scenario: 全数据论点不封顶

- **GIVEN** judge 返回 `score=5` 且 `points` 全部为 `type="data"`
- **WHEN** 解析 judge 结果
- **THEN** 最终分数 SHALL 保持 5（`cap_applied=false`）

#### Scenario: 封顶不下压低于 4

- **GIVEN** judge 返回 `score=3` 且 `points` 含纯定性论点
- **WHEN** 解析 judge 结果
- **THEN** 最终分数 SHALL 保持 3（封顶为 `min(score, 4)`，不额外下压）

#### Scenario: 枚举缺失的可审计降级

- **GIVEN** judge 输出缺 `points` 字段或类型非法（旧格式/模型未遵契约）
- **WHEN** 解析 judge 结果
- **THEN** 分数 SHALL 不变（fail-open，MUST NOT 按缺失推断为「无纯定性」而放行，也 MUST NOT 反向封顶）
- **AND** 结果 SHALL 标记 `enumeration_missing=true` 并随分数落库，供校准与代裁识别机制未生效的行

#### Scenario: decision_grounding 评估

- **GIVEN** 分析师结论 `{{analyst_reports}}`、辩论结论 `{{research_manager_decision}}`、交易决策 `{{trade_decision}}`
- **WHEN** 运行 decision_grounding Judge
- **THEN** SHALL 按 1-5 rubric 打分（5 = 决策论据均有前文出处，1 = 与前文矛盾或无中生有）

#### Scenario: consistency 评估

- **GIVEN** 各层结论 `{{analyst_reports}}` / `{{research_manager_decision}}` / `{{trade_decision}}`（Trader 方案节）/ `{{risk_judgment}}` / `{{fund_manager_decision}}` / `{{report_conclusion}}`
- **WHEN** 运行 consistency Judge
- **THEN** SHALL 按 1-5 rubric 打分（5 = 各层完全一致，1 = 明显自相矛盾）
- **AND** 特别检查 Fund Manager 结论与 Risk Judge 裁决的一致性、Risk Judge 裁决相对 Trader 方案是否有未说明的方向/参数推翻、报告结论与分析师章节的一致性

#### Scenario: Judge 输出解析失败容错

- **WHEN** Judge 返回非 JSON 或解析失败
- **THEN** SHALL 重试一次；仍失败则该维度记 score=null
- **AND** 不阻塞实验，但计入 judge 失败率

#### Scenario: 非 debate 维度结果形状不变

- **WHEN** 运行 report_relevance / decision_grounding / consistency Judge
- **THEN** 结果字典 SHALL NOT 增加 `points` / `cap_applied` / `enumeration_missing` 键（既有精确断言契约不变）

#### Scenario: 裁判成本独立核算

- **WHEN** Judge 调用发起
- **THEN** Langfuse trace 的 generation SHALL 标 `langfuse-llm-as-a-judge` 环境
- **AND** 成本在 Dashboard 可独立查看

### Requirement: 评估 Dataset 与覆盖矩阵

系统 SHALL 维护评估 Dataset（命名如 `a-share-analysis-v1`），每条 item SHALL 含 `input`（query / mode / session_id）与 `expected_output`（仅断言结构性字段，不断言时效数值），metadata SHALL 含 `category`、`source` 与 `pool`。Dataset 条目按 pool 分两组：`baseline` 池为固定可比集合（跨实验配对对比的对象，不得随轮次变动）；`rotating` 池为轮换候选池，每轮实验 SHALL 从池中按标的分组随机抽样（seed 可复现），防对固定标的过拟合。Dataset SHALL 可从历史 trace 捞取并幂等重建。

(Previously: Dataset 条目只按 category 分类，无 pool 区分；重复执行实验始终跑同一批 16 条 items，标的固定无轮换。)

#### Scenario: Item Schema

- **WHEN** Dataset item 定义
- **THEN** `input` SHALL 含 `query`、`mode`（deep/quick/follow_up）、可选 `session_id`
- **AND** `expected_output` 可含 `ticker`、`must_cover`、`should_clarify`，均为可选
- **AND** `metadata` 含 `category` 与 `source`

#### Scenario: 幂等建库

- **WHEN** `dataset_seed.py` 重复执行
- **THEN** SHALL 不产生重复 item（以 input.query + mode 为去重键）
- **AND** 已存在 item 不被覆盖

#### Scenario: expected 不含时效数值

- **GIVEN** 某 deep 典型 case
- **THEN** `expected_output` SHALL NOT 含具体财务数值（如净利润 X 亿）
- **AND** 只含结构性断言（章节、ticker）

#### Scenario: baseline 池固定可比

- **GIVEN** 两次实验均使用 baseline 池
- **WHEN** 运行实验
- **THEN** 两次实验的 item 集合 SHALL 完全一致（配对 bootstrap 可比前提）
- **AND** rotating 池条目 SHALL NOT 混入 baseline 实验

#### Scenario: rotating 池按标的分组抽样

- **WHEN** 以 `--pool rotating --rotating-sample N --rotating-seed S` 建库
- **THEN** SHALL 随机抽取 N 个标的，每个标的的全部条目（deep/quick）一并入池
- **AND** 相同 seed 抽取结果 SHALL 完全一致（可复现），不同 seed 应倾向不同标的组合
- **AND** rotating 条目 SHALL 建于独立 dataset（如 `a-share-analysis-v1-rot-<seed>`），不污染 baseline 的可比集合

#### Scenario: 确定性断言覆盖

- **WHEN** 定义 quick/deep 条目
- **THEN** 能明确对应单一标的的条目 SHALL 提供 `expected_output.ticker`（供 ticker_match 确定性评分）
- **AND** 无单一标的的行业类查询（如「银行股现在估值贵吗」）可留空，但须在 metadata 标注原因

#### Scenario: 出分条目占比

- **WHEN** 审视 Dataset 设计
- **THEN** 非 skipped 条目（可出分）SHALL 占条目总数 ≥ 80%
- **AND** follow_up / 意图澄清等首版跳过条目合计 SHALL NOT 超过 3 条

### Requirement: 实验回归工作流

系统 SHALL 提供 `evals/` 目录（与 src 平级，不侵入业务代码）与 `run_experiment` 入口，对全 Dataset 跑一遍，关联所用 prompt 版本，产出可对比基线的实验结果。实验 SHALL 支持"改 prompt / 模型 → 跑 → 对比基线 → 决策"闭环。`run_experiment`（经 Langfuse dataset）SHALL 是实验的唯一执行入口：不提供绕过 Langfuse 的本地循环降级；Langfuse 未配置/不可达时，实验入口 SHALL 显式报错并给出非零退出码，不得静默产出不可对比的分数。

#### Scenario: run_experiment 一键执行

- **WHEN** 执行 `evals/run.py "<实验名>"`
- **THEN** SHALL 对 Dataset 全量 item 跑 `run_analysis_task`
- **AND** 对每个 item 应用全部确定性 + Judge 评估器
- **AND** 产出含各 Score 均值与 per-item 明细的结果表

#### Scenario: 无 Langfuse 时显式报错

- **WHEN** 执行实验且 Langfuse 未配置或不可达
- **THEN** SHALL 打印明确错误（说明需要配置 Langfuse）并以非零退出码终止
- **AND** SHALL NOT 以本地循环降级产出分数

#### Scenario: prompt 版本关联

- **WHEN** 实验运行
- **THEN** SHALL 经 `langfuse.get_prompt(name, label="production")` 取 prompt
- **AND** prompt 名与版本记到 trace，UI 可回答"哪个 prompt 版本分数高"

#### Scenario: 业务代码零侵入

- **GIVEN** `evals/` 目录存在
- **THEN** 业务代码（`src/finance_agent/`）SHALL NOT 因评估被修改
- **AND** `evals/` 仅通过既有 graph 入口调用系统

### Requirement: Judge 校准门禁

Judge 评估器 SHALL NOT 在未校准情况下用于线上决策（如阻塞 PR）。上线前 SHALL 经 Annotation Queue 人工按同一 rubric 打分，judge 与人工一致性 ≥ 80% 方可定稿 rubric；定稿后每月抽检一次防漂移。

#### Scenario: 上线前校准

- **WHEN** 首版 rubric 完成、首轮实验产出
- **THEN** SHALL 抽 20-30 条进 Annotation Queue 人工打分（NUMERIC 1-5）
- **AND** judge 分与人工分一致性 < 80% 时 SHALL 调整 rubric 措辞并重校准
- **AND** 一致性 ≥ 80% 方可定稿

#### Scenario: 系统性偏置检测

- **WHEN** 校准发现 judge 系统性偏高 / 偏低（如对长报告偏宽松）
- **THEN** SHALL 在 rubric 强化对应措辞后重跑

#### Scenario: 月度抽检

- **GIVEN** rubric 已定稿上线
- **THEN** 此后每月 SHALL 抽检一次 judge 与人工一致性
- **AND** 漂移超标时触发 rubric 迭代

### Requirement: 线上托管 Evaluator

第二阶段，系统 SHALL 在 Langfuse 服务端配置线上托管 Evaluator，与线下实验用同一套 rubric 与裁判模型，按采样率（初值 10-20%）对生产 trace 自动评估，结果作为 Monitors 告警信号。quick 模式无辩论，仅跑 `report_relevance`。

#### Scenario: 采样评估

- **GIVEN** 线上 trace 产生
- **WHEN** 命中采样率
- **THEN** 托管 Evaluator SHALL 按同 rubric 自动跑 Judge
- **AND** Score 附着到该 trace

#### Scenario: 模式过滤

- **GIVEN** trace 的 `mode=quick`
- **THEN** 托管 Evaluator SHALL 仅跑 `report_relevance`，跳过 `debate_quality`

#### Scenario: 漂移告警

- **WHEN** 某 Judge Score 均值在窗口内骤降
- **THEN** Monitors SHALL 触发告警（webhook）

### Requirement: 断言级校验基准集

系统 SHALL 维护断言级校验基准集（claim benchmark）：从历史报告抽样 30-50 份，每份抽取 20-30 条 claim，人工标注每条 claim 的应有裁决（PASS / FAIL / UNVERIFIABLE）。标注 SHALL 双人背对背进行、分歧仲裁，并报告一致性系数（Cohen's κ）。基准集 SHALL 随生产 bad case 滚动补库，SHALL NOT 一次建成后冻结。

#### Scenario: 基准集构建

- **WHEN** 构建或扩充校验基准集
- **THEN** 每条 claim SHALL 有两名标注者的独立标注与仲裁后的最终标签
- **AND** 数据集元信息 SHALL 记录标注者一致性 κ 与版本号

#### Scenario: 滚动补库

- **WHEN** 生产中发现校验器误判的 bad case（人工复核确认）
- **THEN** 该 claim SHALL 以人工裁决为标签补入基准集下一版本

### Requirement: 校验器准度测量与门禁

系统 SHALL 提供校验器准度测量：对基准集运行 `verify_claims`，输出整体 Precision / Recall / F1，以及两个对抗子集的分项召回——(a) 擦边子集：stated_value 在真值 ±5% 以内的对抗 claim；(b) hedged 措辞子集：含"约""可能""接近"等模糊措辞的 claim。整体 F1 ≥ 0.90 与相对冻结基线退步 ≤ 0.02 的 CI 门禁 SHALL 保留，擦边子集召回 SHALL 单独显式披露（不设硬门禁）；但门禁产物（measure 报告）SHALL 显式披露所用基准集的身份：`rule_derived`（构造标签）基准集 SHALL 被标注为「算法回归探针——仅验证实现未回归，不构成真实准度声明」；真实准度声明 SHALL 仅来自含人工标注（annotator=double_human/single_human）与真实来源（origin 非空）的金标准集，且须报告标注者一致性 κ。两个信号 SHALL 分开展示，SHALL NOT 混编为一句话的「F1 可信」。

(Previously: 校验器 F1 ≥ 0.90 即为「准度可信」，未区分基准集身份与来源。)

#### Scenario: 准度达标

- **GIVEN** 基准集标注完成
- **WHEN** 运行准度测量
- **THEN** 报告 SHALL 含整体 P/R/F1（带 95% CI）与两个对抗子集的分项召回
- **AND** 整体 F1 ≥ 0.90 时校验器准度状态为"可信"，否则其下游 FAIL 判定 SHALL 在评估报告中标注"校验器自身准度未达标"

#### Scenario: 擦边盲区披露

- **WHEN** 生成准度报告
- **THEN** 擦边子集召回 SHALL 单独成行披露，SHALL NOT 被整体指标掩盖

#### Scenario: 构造集身份披露

- **WHEN** CI 或报告中呈现校验器 F1
- **THEN** 若基准集全部为构造标签，SHALL 输出「回归探针，非真实准度」声明
- **AND** SHALL NOT 出现「校验器准度可信」措辞

#### Scenario: 真实准度声明

- **WHEN** 使用含人工标注与真实来源的样本报告准度
- **THEN** SHALL 报告整体 P/R/F1（带 CI）与标注者一致性 κ
- **AND** F1 ≥ 0.90 时方可表述「准度可信」

### Requirement: 实验对比统计显著性

`run_experiment` 的基线对比 SHALL 使用配对 bootstrap（B=10,000，按 dataset item 重采样）报告差值的 95% 置信区间。当 CI 含 0 时，结论 SHALL 只能表述为"无显著差异"，SHALL NOT 用语义化措辞包装点估计差异。本条适用于全部确定性评估器与 judge 分数的对比报告。

#### Scenario: 显著改进

- **GIVEN** 新 prompt 版本与基线各跑完全量 dataset
- **WHEN** 分数差值的 95% CI 不含 0 且为正
- **THEN** 报告 SHALL 判定"显著改进"并给出 CI 区间

#### Scenario: 差异不显著

- **WHEN** 分数差值的 95% CI 含 0
- **THEN** 报告 SHALL 输出"无显著差异"，并 SHALL NOT 出现"略有提升""整体更好"等无统计支撑的结论性措辞

### Requirement: 数据对齐消融实验

系统 SHALL 支持数据对齐消融：构造三个架构变体——(a) 单分析师直出、(b) 分析师 + Bull/Bear 辩论、(c) 完整 5 层——所有变体接收**完全相同**的 state 快照（fetch_data / compute_metrics 输出重放），仅 Agent 编排不同。每变体 × 每标的 SHALL 重复运行 3 次取中位数。快照对齐 SHALL 由代码保证：每条 run 前重算快照 digest 并与本次跑批登记值比对，不一致时跑批 SHALL 显式失败并报告标的与两侧 digest，SHALL NOT 静默继续（跨交易日续跑不得混入不同输入）。digest SHALL 为**内容稳定**哈希——同一数据在任意进程、任意次构建得到相同值，SHALL NOT 依赖内存布局（对象列 `ndarray.tobytes()` 序列化的是指针，该形态会致核验恒失败——G5 通路验证实测）。

**指标层（v2 置换）**：层增量推断 SHALL 以因果主张登记表（`causal-ablation` 能力）对应的因果下游指标为主指标。原 judge 四维的安置：report_relevance / consistency SHALL 降级为 CI 回归健康监控（门禁族），SHALL NOT 参与层增量推断；debate_quality SHALL 从消融退役（由风险点增量率、交锋修正率与 pairwise 盲评替代）；decision_grounding 的 judge 版 SHALL 降级为抽查校准用，其 dg 检查（执行参数出处）SHALL 以代码化口径为主。citation 腿 SHALL 以四桶拆报口径（blocked / analyst_true_fail / surgical_repaired / verifier_normalized）呈现，SHALL NOT 以旧 `citation_pass` 标量作层增量解读。

**跑批入口唯一化**：跑批驱动 SHALL 为薄壳——判分、维度适用性过滤、judge_vars 落盘 SHALL 全部调用库侧（`evals/ablation.py`）函数，驱动侧 SHALL NOT 存在判分/过滤/落盘的重复实现；判分口径变更 SHALL 只改库侧一处。

**维度适用性过滤的唯一实现**：某变体不存在的层 SHALL NOT 被 judge 评分——评「不存在的层」只会产伪影（宽容评虚层 vs 挑剔评真层的不对称，incident #112）。变体→适用维度的映射 SHALL 只有一处实现（`evals.ablation._applicable_dims`），跑批驱动 SHALL 复用该实现，SHALL NOT 在驱动侧另行硬编码变体分支。适用性过滤 SHALL 在**判分调用前**生效：被过滤维度 SHALL 记 `None` 且 SHALL NOT 产生任何 judge 调用。

**层增量点估计的字段名与配对单元**：层增量的点估计字段 SHALL 命名为 `diff_mean`（值为均值差，与 `paired_bootstrap_ci` 的 mean-diff 口径一致），SHALL NOT 沿用 `diff_median` 之类与实际统计量不符的名称。聚合报告 SHALL 披露配对单元为**标的**而非 run（同标的同变体重复先取中位数，再以标的为配对单元），并给出配对单元数——使「3 标的 × 10 重复」不等于「有效 n=30」这一点对读报告的人可见。

**结论句式纪律**：消融结论 SHALL 只有两种合法形态——① CI 整体低于决策阈值时为真阴性结论（「在本实验分辨率（MDE=X）下，该对象增量低于其成本对应阈值，建议降级/裁剪」）；② CI 同时跨 0 与阈值时为诚实悬置（「分辨率不足，需扩至 N 只标的」）。裸「未获统计支持」SHALL NOT 作为合格结论，必须附 MDE；决策阈值 SHALL 附成本换算依据。

**材料落盘与参数化**：消融每完成一条 run SHALL 落盘该 run 的 judge 输入材料（`judge_vars`）到独立文件（按 `(variant, ticker, repeat)` 命名），并在 run 记录中携带材料路径与 judge 明细（分数、纯定性论点数、是否封顶、枚举是否缺失）——使 rubric 变更后的重判 SHALL 可离线完成（不重跑管线、不依赖从 Langfuse 反解材料），并按同一口径复算。标的列表与重复次数 SHALL 经 CLI 参数化（默认值保持 3 标的 / 3 重复），实际取值 SHALL 写入产物 config；断点续跑的完成键 SHALL 仍为 `(variant, ticker, repeat)`，参数变更后已完成 run SHALL NOT 被重复消耗 token。

**judge 分数取 K 次均值（噪声下限披露）**：round11 实测同一材料同一 rubric 的单次 judge 调用在 5/4 边界**双峰翻转**（宁德 04baff5c，n=13 次调用：4 分 7 次 / 5 分 6 次，σ≈0.5）——该噪声与消融待测的层级增量（0.25–0.5 量级）同阶，单次调用不足以支撑层间比较。故消融的 judge 分数 SHALL 以 K 次重复的**均值**为点估计（K 经 CLI 声明并写入产物，默认 ≥3）；SHALL NOT 用中位数——双峰分布 p→0.5 时中位不降翻转概率，均值才无偏且方差随 K 收缩。SHALL 记录每次分数与极差（`scores` / `score_spread`）使噪声保持可见，SHALL NOT 只留均值而静默抹掉离散度。解析失败/输入缺失的调用不计入均值但计入 `judge_failures`；封顶/枚举遥测取**最低分那次**调用的观测（最保守，任一次找到纯定性标头则封顶证据不丢）。

**材料落盘与参数化**：消融每完成一条 run SHALL 落盘该 run 的 judge 输入材料（`judge_vars`）到独立文件（按 `(variant, ticker, repeat)` 命名），并在 run 记录中携带材料路径与 judge 明细（分数、纯定性论点数、是否封顶、枚举是否缺失）——使 rubric 变更后的重判 SHALL 可离线完成（不重跑管线、不依赖从 Langfuse 反解材料），并按同一口径复算。标的列表与重复次数 SHALL 经 CLI 参数化（默认值保持 3 标的 / 3 重复），实际取值 SHALL 写入产物 config；断点续跑的完成键 SHALL 仍为 `(variant, ticker, repeat)`，参数变更后已完成 run SHALL NOT 被重复消耗 token。

**judge 分数取 K 次均值（噪声下限披露）**：round11 实测同一材料同一 rubric 的单次 judge 调用在 5/4 边界**双峰翻转**（宁德 04baff5c，n=13 次调用：4 分 7 次 / 5 分 6 次，σ≈0.5）——该噪声与消融待测的层级增量（0.25–0.5 量级）同阶，单次调用不足以支撑层间比较。故消融的 judge 分数 SHALL 以 K 次重复的**均值**为点估计（K 经 CLI 声明并写入产物，默认 ≥3）；SHALL NOT 用中位数——双峰分布 p→0.5 时中位不降翻转概率，均值才无偏且方差随 K 收缩。SHALL 记录每次分数与极差（`scores` / `score_spread`）使噪声保持可见，SHALL NOT 只留均值而静默抹掉离散度。解析失败/输入缺失的调用不计入均值但计入 `judge_failures`；封顶/枚举遥测取**最低分那次**调用的观测（最保守，任一次找到纯定性标头则封顶证据不丢）。

#### Scenario: 变体输入对齐

- **WHEN** 执行消融实验
- **THEN** 三个变体的数据输入 SHALL 来自同一 trace 的 state 快照重放，差异只可归因于编排架构
- **AND** 每条 run 前快照 digest SHALL 与跑批登记值比对通过，不一致时该 run SHALL 作废告警

#### Scenario: 消融结论

- **WHEN** 消融完成
- **THEN** 报告 SHALL 给出每个被消融对象的因果下游主指标效应量、95% CI 与 MDE
- **AND** 结论 SHALL 符合两种合法句式之一（真阴性或诚实悬置），决策阈值 SHALL 附成本换算依据

#### Scenario: judge 材料落盘可离线重判

- **WHEN** 一条消融 run 完成
- **THEN** 其 `judge_vars` SHALL 已写入 `reports/ablation/judge_vars/<variant>-<ticker>-<repeat>.json`，run 记录 SHALL 含该路径
- **AND** 用该文件直接调用 `run_judge` SHALL 复现同一批 judge 分数（无需重跑管线）

#### Scenario: 重复次数与标的参数化

- **WHEN** 以 `--tickers ... --repeats N` 运行消融驱动
- **THEN** 实际 TICKERS/REPEATS SHALL 写入产物 config，且 runs 条数 SHALL 为 `len(tickers) × 3 × N`（无可跳过项时）
- **AND** 断点续跑文件中的完成键 SHALL 仍为 `(variant, ticker, repeat)`

#### Scenario: judge 分数取均值并记录离散度

- **GIVEN** 某 run 的 debate_quality 三次调用返回 `[5, 4, 4]`
- **WHEN** 记录该 run 的 judge 分数
- **THEN** 点估计 SHALL 为 4.33（均值），`scores` 与 `score_spread=1` SHALL 一并落盘
- **AND** 全部调用失败时 SHALL 记 score=None 且 `judge_failures=K`（沿实验失败率口径，不静默给分）
- **AND** 封顶/枚举遥测 SHALL 取自最低分那次调用（`[5,4,4]` → 取 4 分那次的枚举）

#### Scenario: 材料落盘不改变聚合口径

- **WHEN** 落盘材料后运行聚合
- **THEN** `aggregate_results` 的输入 runs 形状 SHALL 与落盘前一致（新增字段为附加信息），既有聚合 CI 口径 SHALL NOT 因落盘而变化

#### Scenario: 跑批入口唯一化

- **WHEN** 消融驱动执行判分、维度适用性过滤或 judge_vars 落盘
- **THEN** 这些逻辑 SHALL 经由库侧函数完成；驱动侧 SHALL NOT 内联判分/过滤/落盘实现
- **AND** 被过滤维度 SHALL 记 `None` 且 SHALL NOT 发起 judge 调用

#### Scenario: 跨进程续跑的快照 digest 核验

- **GIVEN** 断点续跑台账已登记某标的的快照 digest
- **WHEN** 重启后续跑重建该标的快照
- **THEN** digest 与登记值不一致时跑批 SHALL 显式失败并输出标的与两侧 digest，SHALL NOT 继续消耗 token、SHALL NOT 将新旧 run 混入同一批次比较
- **AND** digest SHALL 内容稳定：同一数据在任意进程、任意次构建得到相同值（对象列不得以指针序列化参与哈希）

#### Scenario: 层增量点估计字段与配对单元

- **WHEN** 读取聚合报告的层增量条目
- **THEN** 点估计 SHALL 位于 `diff_mean` 字段，`diff_median` SHALL NOT 出现
- **AND** 报告 SHALL 携带配对单元数与「配对单元=标的」的披露

#### Scenario: citation 腿拆报呈现

- **WHEN** 消融报告呈现 citation 相关结果
- **THEN** SHALL 以四桶拆报口径（blocked / analyst_true_fail / surgical_repaired / verifier_normalized）呈现，SHALL NOT 以 citation_pass 标量作层增量比较

#### Scenario: 旧四维不再作层增量

- **WHEN** 报告聚合 judge 维度
- **THEN** report_relevance / consistency SHALL 仅以健康监控口径呈现（CI 回归基线），SHALL NOT 出现在层增量结论句中

#### Scenario: 驱动复用库侧适用性过滤

- **GIVEN** 变体 `plus_debate`（无 Trader / 风控辩论 / RJ / FM 层）
- **WHEN** 跑批驱动为其一条 run 判分
- **THEN** 驱动 SHALL 经 `evals.ablation._applicable_dims("plus_debate")` 取维度集合
- **AND** `decision_grounding` 与 `consistency` SHALL 记 `None` 且 SHALL NOT 发起 judge 调用
- **AND** 驱动侧 SHALL NOT 存在按变体名硬编码的维度分支

#### Scenario: judge 材料落盘可离线重判

- **WHEN** 一条消融 run 完成
- **THEN** 其 `judge_vars` SHALL 已写入 `reports/ablation/judge_vars/<variant>-<ticker>-<repeat>.json`，run 记录 SHALL 含该路径
- **AND** 用该文件直接调用 `run_judge` SHALL 复现同一批 judge 分数（无需重跑管线）

#### Scenario: 重复次数与标的参数化

- **WHEN** 以 `--tickers ... --repeats N` 运行消融驱动
- **THEN** 实际 TICKERS/REPEATS SHALL 写入产物 config，且 runs 条数 SHALL 为 `len(tickers) × 3 × N`（无可跳过项时）
- **AND** 断点续跑文件中的完成键 SHALL 仍为 `(variant, ticker, repeat)`

#### Scenario: judge 分数取均值并记录离散度

- **GIVEN** 某 run 的 debate_quality 三次调用返回 `[5, 4, 4]`
- **WHEN** 记录该 run 的 judge 分数
- **THEN** 点估计 SHALL 为 4.33（均值），`scores` 与 `score_spread=1` SHALL 一并落盘
- **AND** 全部调用失败时 SHALL 记 score=None 且 `judge_failures=K`（沿实验失败率口径，不静默给分）
- **AND** 封顶/枚举遥测 SHALL 取自最低分那次调用（`[5,4,4]` → 取 4 分那次的枚举）

#### Scenario: 材料落盘不改变聚合口径

- **WHEN** 落盘材料后运行聚合
- **THEN** `aggregate_results` 的输入 runs 形状 SHALL 与落盘前一致（新增字段为附加信息），既有聚合 CI 口径 SHALL NOT 变化

### Requirement: 基准集 v1.1 构造规则

校验基准集 v1.1 SHALL 重构 near_miss 子集：篡改幅度改为 ±{0.3%, 0.5%, 0.7%, 1%} 四档（探容差边界而非远离边界），且其中 50% 为 should_pass 样本（篡改幅度在容差内，标签为 PASS），使子集同时测量漏检与误报。v1.1 SHALL 新增 semantic_mismatch 子集：数值与 field_ref 正确但术语或期次张冠李戴（如毛利率写作净利率、年报值描述为季度值），标签为 FAIL。子集检出率 SHALL 在准度报告中单独披露。

#### Scenario: 边界双向探测

- **WHEN** 生成 v1.1 near_miss 子集
- **THEN** 样本覆盖容差两侧（0.3%/0.5% 档多为 should_pass，0.7%/1% 档多为 should_fail）
- **AND** 报告分别披露"过线检出率"与"线内误报率"，不接受单一汇总检出率

#### Scenario: 语义子集验收新规则

- **GIVEN** 术语一致性校验规则已上线
- **WHEN** 对 semantic_mismatch 子集运行准度测量
- **THEN** 该子集检出率 SHALL ≥ 90% 并在准度报告中单独成行

### Requirement: 覆盖率纳入实验指标

run_experiment 的实验报告 SHALL 增加 `citation_coverage` 指标（按 dataset item 聚合均值与 95% CI），与 citation_pass 并列展示；覆盖率变化的显著性判断沿用配对 bootstrap 契约。

#### Scenario: 实验报告含覆盖率

- **WHEN** 对比两个 prompt 版本的实验结果
- **THEN** 报告 SHALL 含 citation_coverage 的均值差与 CI，CI 含 0 时表述为"无显著差异"

### Requirement: judge rubric 扩展语义核对

decision_grounding judge 的 rubric SHALL 扩展：核对 interpretation/报告表述中的指标术语、期次、方向与所引用数值的语义一致性（如数值下降却表述"改善"）。rubric 变更 SHALL 递增版本号，并按 Judge 校准门禁契约重新校准（与人工一致性 ≥80%）后方可上线。

#### Scenario: 解读失当被扣分

- **GIVEN** 报告将行业垫底的 45.2% 毛利率表述为"行业领先"，且 evidence_refs 指向该值
- **WHEN** 运行 decision_grounding judge
- **THEN** judge SHALL 按 rubric 对解读失当扣分（不得仅因数值有出处给高分）

### Requirement: deep 边界歧义解析样本

deep 边界类别 SHALL 覆盖至少一个「模糊名称无代码」的歧义解析样本（如「分析平安」→ 期望解析到具体标的或触发反问），用于加压 ticker 解析与意图澄清路径。

#### Scenario: 歧义样本加压

- **GIVEN** dataset 含「分析平安」这类无代码模糊 query
- **WHEN** 运行实验
- **THEN** 该条目 SHALL 期望解析到确定标的（ticker_match 打分）或经 `should_clarify` 触发反问
- **AND** 两种预期均在 expected_output 中显式声明

### Requirement: 章节覆盖评估（section_coverage）

`SECTION_SYNONYMS` 词典 SHALL 携带版本号（`SECTION_SYNONYMS_VERSION`），冻结测试 SHALL 锁定词典内容（防止无人察觉的漂移）；prompt（`src/finance_agent/prompts/*.md`）中出现的章节性词与词典的交叉一致性 SHALL 有测试覆盖——prompt 引入词典未覆盖的章节词时测试 SHALL 红（提示更新词典或冻结版本）。

#### Scenario: 词典冻结

- **WHEN** 词典内容被修改
- **THEN** 冻结测试 SHALL 红（提示显式更新版本与冻结快照）
- **AND** 版本号随内容变更递增

#### Scenario: prompt 章节词回归

- **WHEN** prompt 中出现词典未覆盖的章节性标题词（如新增「分红能力」章节）
- **THEN** 交叉一致性测试 SHALL 红
- **AND** 修复方式 SHALL 是更新词典（或将已知英文标题显式列入 allowlist），SHALL NOT 修改测试

### Requirement: 幻觉率真值来源标注

幻觉率测量的真值数据（`data_map`）SHALL 携带 `source` 字段（快照来源与时点，如 `snapshot:akshare-2026-08-25`）；缺 source 的 data_map SHALL 被拒绝测量。真值来源 SHALL 与报告生成所用数据管道解耦（独立快照），防止「报告 vs 自己抓的数据」自证。

#### Scenario: 缺 source 拒绝

- **WHEN** 传入无 `source` 字段的 data_map
- **THEN** 测量 SHALL 报错并拒绝执行
- **AND** 错误信息 SHALL 指明需提供独立快照来源

#### Scenario: 带 source 正常测量

- **WHEN** data_map 含 `source`（如 `snapshot:akshare-2026-08-25`）
- **THEN** 测量正常执行，source 随报告输出
- **AND** 报告 SHALL 展示真值快照来源（供审计追溯）

### Requirement: 评估报告结论状态与适用口径标注

评估报告的结论是有生命周期的对象：新证据推翻旧结论时，旧结论 SHALL 自动失效，而不是留在原地等人发现。消融结果报告（`evals/ablation/results/*.md`）SHALL 在头部声明状态字段 `**status**:`，取值 SHALL 为 `active` 或 `superseded-by: <相对路径>`；`superseded-by` 的目标路径 SHALL 解析到仓库内真实存在的文件（悬空指针与无标记等价，都不允许）。被推翻的结论 SHALL 就地标注（原文保留 + 撤回说明 + 指向权威版本），SHALL NOT 只删数字或只改数字——只改数字会让读者以为结论仍成立。

**权威报告的适用口径披露义务**：一份宣称覆盖前版结论的报告（如「权威版」）SHALL 披露其数字的适用口径，至少包含：① judge 校准状态（该跑批相对 judge 校准达标轮次的先后——校准前产出的分数不得表述为已校准）；② 维度适用性是否生效（该批次的各变体评了哪些维度；若评了不存在的层，相关层增量 SHALL 标注为无效比较而非层增量）；③ 层增量的配对单元与其数量；④ 已知契约噪声是否进入该报告用作层增量的指标（进了就 SHALL 声明该指标不作层增量解读）。披露 SHALL 位于报告头部或结论区，SHALL NOT 只藏在正文脚注。

**对外材料引用纪律**：README 等对外材料引用消融数字时 SHALL 引用状态为 `active` 的报告，且结论措辞 SHALL 与该报告的 CI 纪律一致——「未获统计支持」SHALL NOT 被转写为「据此可裁剪」（统计不支持与价值为零是两个命题，裁剪决策需要独立证据）。

> 本要求是本 delta 的最小落点：只覆盖消融结果报告的头部状态与权威报告的披露义务。完整的结论注册表（全库扫描 + 索引页状态徽章 + 状态迁移校验）留待后续 delta，见 design.md「不做的事」。

#### Scenario: 被推翻的报告带 superseded 头部

- **GIVEN** `evals/ablation/results/pilot.md` 的「风险辩论+基金经理层显著退步」结论已被 n=10 报告推翻（伪影 #109/#111/#112）
- **WHEN** 读取该报告
- **THEN** 头部 SHALL 含 `**status**: superseded-by: docs/evals/2026-09-03-消融n10权威结果.md`
- **AND** 「简历素材对照」段 SHALL 保留原文并附撤回说明（结论被推翻 + 成本口径 41% vs 29% 的差异 + 指向权威版本）
- **AND** 该路径 SHALL 在仓库中真实存在

#### Scenario: 权威报告披露测量口径

- **GIVEN** 某 n=10 报告自称「修复后权威版」，但其 90 批次产出于 judge 校准 round7 达标（2026-09-13）之前
- **WHEN** 阅读该报告
- **THEN** 报告 SHALL 声明该批次 judge 未校准
- **AND** 报告 SHALL 声明 `plus_debate` 变体无 Trader/风控/FM 层，其 grounding / consistency 分数为 #112 伪影存活，对应的 full−plus_debate 层增量条目 SHALL 标注为无效比较
- **AND** 报告 SHALL 声明层增量的配对单元为标的（3 个）而非 run（30 条）

#### Scenario: 状态契约的机器校验

- **WHEN** 运行报告状态契约测试
- **THEN** `evals/ablation/results/*.md` 每份文件 SHALL 含合法 `**status**:` 头
- **AND** `superseded-by` 指向的路径 SHALL 存在，否则测试 SHALL 失败

### Requirement: 辩论论点锚点覆盖率

系统 SHALL 提供确定性评估器 `argument_anchor_coverage`（零 LLM 调用）：从 state channel `debate_anchor_checks` 计算 `value = 有效锚定论点数 / 论点总数`（有效锚定 = 任一锚点 `resolved`；Layer II 与 Layer IV 合并计），并携带拆项 `unanchored_inference`（推断型零锚）、`unresolved`（申报了锚但无一解析/命中）、`missing_required`（data / event 型零锚）、`unspecified`（旧格式或 kind 非法）。论点总数为 0 时 SHALL 返回 null（不计入该维度）。该指标 SHALL 作为 NUMERIC Score 上报 Langfuse（与 `citation_coverage` 并列），SHALL 进入实验报告与消融 run 记录（`evals/run.py` / 消融驱动），使辩论层拥有不依赖 judge 取值域的层间比较信号。

debate_quality 的 judge 材料 SHALL 在既有骨架行（交锋覆盖统计、收敛信号）之后追加**锚点覆盖骨架行**（各方 `anchored/total` 与拆项），并在每条论点编号前标注 `[kind 状态]`（如 `[data ✓]` / `[inference ○]` / `[data ✗ 锚不可解析]`）。本要求 SHALL NOT 改变 debate_quality 的 rubric 版本与 `points` 输出契约——锚点信息仅作确定性对照锚呈现；judge `points.type` 与辩手申报 `kind` 的交叉核对留待后续校准轮。

指标口径 SHALL 登记至 `docs/evals/metrics.md` §1.2（定义 / 代码位置 / 拆项），judge 材料形态变化 SHALL 在时间线标注切点（跨切点 debate 分不可直接比较）。

#### Scenario: 覆盖率确定性计算

- **GIVEN** 一次 deep 全流程 `debate_anchor_checks` 含 12 条论点校验，其中 8 条 `anchored=true`、2 条 inference 零锚、1 条 data 型锚全部 unresolved、1 条 unspecified
- **WHEN** 运行 `argument_anchor_coverage` 评估器
- **THEN** SHALL 返回 `{name: "argument_anchor_coverage", value: 0.6667, comment: 含 unanchored_inference=2 / unresolved=1 / missing_required=0 / unspecified=1}`
- **AND** SHALL NOT 发起任何 LLM 调用

#### Scenario: 无论点时跳过

- **WHEN** `debate_anchor_checks` 为空（quick 模式或辩论层未执行）
- **THEN** 评估器 SHALL 返回 null，不报错、不计入该维度

#### Scenario: judge 材料附锚点骨架行与状态标注

- **WHEN** 提取 debate_quality 的 `debate_history` 材料
- **THEN** 材料 SHALL 含「【锚点覆盖】bull a/b｜bear c/d｜风控 e/f（含拆项）」骨架行，位于交锋覆盖与收敛信号骨架行之后、原始发言之前
- **AND** 每条论点行 SHALL 形如「①[data ✓] 论点原文」，`kind` 与状态取自 `debate_anchor_checks`，SHALL NOT 由材料提取阶段重新推断
- **AND** rubric 版本与 `points` 契约 SHALL 保持 v6 不变

#### Scenario: 消融与实验报告携带

- **WHEN** 消融一条 run 完成或 `python -m evals.run` 一条 item 完成
- **THEN** run / item 记录 SHALL 含 `argument_anchor_coverage` 的 value 与拆项
- **AND** 消融聚合报告 SHALL 对该指标按既有配对 bootstrap 口径给出层间增量与 95% CI（与 judge 维度同法）

#### Scenario: 口径登记与切点

- **WHEN** 本变更合入并首轮实验收口
- **THEN** `docs/evals/metrics.md` §1.2 SHALL 新增该指标行（定义 / 代码位置 / 拆项）
- **AND** 时间线 SHALL 标注「judge 材料加锚点骨架行」切点，跨切点 debate_quality 分数 SHALL 标注不可直接比较

