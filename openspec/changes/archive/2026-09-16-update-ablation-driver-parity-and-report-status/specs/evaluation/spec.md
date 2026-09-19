## MODIFIED Requirements

### Requirement: 数据对齐消融实验

系统 SHALL 支持数据对齐消融：构造三个架构变体——(a) 单分析师直出、(b) 分析师 + Bull/Bear 辩论、(c) 完整 5 层——所有变体接收**完全相同**的 state 快照（fetch_data / compute_metrics 输出重放），仅 Agent 编排不同。每变体 × 每标的 SHALL 重复运行 3 次取中位数。消融报告 SHALL 以 citation_pass 率与 judge 分数（带 CI）衡量各层增量价值，SHALL NOT 用单变体单次运行下结论。

**材料落盘与参数化**：消融每完成一条 run SHALL 落盘该 run 的 judge 输入材料（`judge_vars`）到独立文件（按 `(variant, ticker, repeat)` 命名），并在 run 记录中携带材料路径与 judge 明细（分数、纯定性论点数、是否封顶、枚举是否缺失）——使 rubric 变更后的重判 SHALL 可离线完成（不重跑管线、不依赖从 Langfuse 反解材料），并按同一口径复算。标的列表与重复次数 SHALL 经 CLI 参数化（默认值保持 3 标的 / 3 重复），实际取值 SHALL 写入产物 config；断点续跑的完成键 SHALL 仍为 `(variant, ticker, repeat)`，参数变更后已完成 run SHALL NOT 被重复消耗 token。

**judge 分数取 K 次均值（噪声下限披露）**：round11 实测同一材料同一 rubric 的单次 judge 调用在 5/4 边界**双峰翻转**（宁德 04baff5c，n=13 次调用：4 分 7 次 / 5 分 6 次，σ≈0.5）——该噪声与消融待测的层级增量（0.25–0.5 量级）同阶，单次调用不足以支撑层间比较。故消融的 judge 分数 SHALL 以 K 次重复的**均值**为点估计（K 经 CLI 声明并写入产物，默认 ≥3）；SHALL NOT 用中位数——双峰分布 p→0.5 时中位不降翻转概率，均值才无偏且方差随 K 收缩。SHALL 记录每次分数与极差（`scores` / `score_spread`）使噪声保持可见，SHALL NOT 只留均值而静默抹掉离散度。解析失败/输入缺失的调用不计入均值但计入 `judge_failures`；封顶/枚举遥测取**最低分那次**调用的观测（最保守，任一次找到纯定性标头则封顶证据不丢）。

**维度适用性过滤的唯一实现**：某变体不存在的层 SHALL NOT 被 judge 评分——评「不存在的层」只会产伪影（宽容评虚层 vs 挑剔评真层的不对称，incident #112）。变体→适用维度的映射 SHALL 只有一处实现（`evals.ablation._applicable_dims`），跑批驱动 SHALL 复用该实现，SHALL NOT 在驱动侧另行硬编码变体分支。适用性过滤 SHALL 在**判分调用前**生效：被过滤维度 SHALL 记 `None` 且 SHALL NOT 产生任何 judge 调用（省 token 与产伪影是同一动作的两面）。

**跑批快照一致性核验**：跨进程续跑（断点续跑）时，每条 run 所用快照 SHALL 与本次跑批登记的 digest 一致；重建快照的 digest 与登记值不一致时，跑批 SHALL 显式失败并报告标的与两侧 digest，SHALL NOT 静默混批、SHALL NOT 以「进程内重放共享同 digest」的注释代替核查。本条使「差异只可归因于编排架构」由注释保证升级为代码保证——跨交易日续跑若数据源已刷新（新闻/行情按日变），旧 run 与新 run 的输入即不同，此时层增量不可解释。

**层增量点估计的字段名与配对单元**：层增量的点估计字段 SHALL 命名为 `diff_mean`（值为均值差 `mean(cur) − mean(prev)`，与 `paired_bootstrap_ci` 的 mean-diff 口径一致），SHALL NOT 沿用 `diff_median` 之类与实际统计量不符的名称。聚合报告 SHALL 披露配对单元（bootstrap 的重采样单位）为**标的**而非 run：同标的同变体重复先取中位数，再以标的为配对单元。报告 SHALL 同时给出配对单元数，使「3 标的 × 10 重复」不等于「有效 n=30」这一点对读报告的人可见。

#### Scenario: 变体输入对齐

- **WHEN** 执行消融实验
- **THEN** 三个变体的数据输入 SHALL 来自同一 trace 的 state 快照重放，差异只可归因于编排架构

#### Scenario: 消融结论

- **WHEN** 消融完成
- **THEN** 报告 SHALL 给出每个新增层级的增量效果及 95% CI
- **AND** 对 CI 含 0 的层级，报告 SHALL 明确标注"该层价值未获统计支持"，供成本裁剪决策参考

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

#### Scenario: 驱动复用库侧适用性过滤

- **GIVEN** 变体 `plus_debate`（无 Trader / 风控辩论 / RJ / FM 层）
- **WHEN** 跑批驱动为其一条 run 判分
- **THEN** 驱动 SHALL 经 `evals.ablation._applicable_dims("plus_debate")` 取维度集合
- **AND** `decision_grounding` 与 `consistency` SHALL 记 `None` 且 SHALL NOT 发起 judge 调用
- **AND** 驱动侧 SHALL NOT 存在按变体名硬编码的维度分支

#### Scenario: 跨进程续跑的快照 digest 核验

- **GIVEN** 断点续跑台账已登记某标的的快照 digest
- **WHEN** 重启后续跑重建该标的快照，得到不同 digest（数据源已刷新）
- **THEN** 跑批 SHALL 显式失败并输出标的与两侧 digest
- **AND** SHALL NOT 继续消耗 token、SHALL NOT 将新旧 run 混入同一批次的层增量比较

#### Scenario: 层增量点估计字段与配对单元

- **WHEN** 读取聚合报告的层增量条目
- **THEN** judge 维度的点估计 SHALL 位于 `diff_mean` 字段，`diff_median` SHALL NOT 出现
- **AND** 报告 SHALL 携带配对单元数，读报告的人 SHALL 能看出有效 n 等于标的数而非 run 数

## ADDED Requirements

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
