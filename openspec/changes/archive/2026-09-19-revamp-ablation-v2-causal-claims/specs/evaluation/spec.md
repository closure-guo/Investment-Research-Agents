# Delta for evaluation

## MODIFIED Requirements

### Requirement: 数据对齐消融实验

系统 SHALL 支持数据对齐消融：构造三个架构变体——(a) 单分析师直出、(b) 分析师 + Bull/Bear 辩论、(c) 完整 5 层——所有变体接收**完全相同**的 state 快照（fetch_data / compute_metrics 输出重放），仅 Agent 编排不同。每变体 × 每标的 SHALL 重复运行 3 次取中位数。快照对齐 SHALL 由代码保证：每条 run 前重算快照 digest 并与本次跑批登记值比对，不一致时跑批 SHALL 显式失败并报告标的与两侧 digest，SHALL NOT 静默继续（跨交易日续跑不得混入不同输入）。digest SHALL 为**内容稳定**哈希——同一数据在任意进程、任意次构建得到相同值，SHALL NOT 依赖内存布局（对象列 `ndarray.tobytes()` 序列化的是指针，该形态会致核验恒失败——G5 通路验证实测）。

**指标层（v2 置换）**：层增量推断 SHALL 以因果主张登记表（`causal-ablation` 能力）对应的因果下游指标为主指标。原 judge 四维的安置：report_relevance / consistency SHALL 降级为 CI 回归健康监控（门禁族），SHALL NOT 参与层增量推断；debate_quality SHALL 从消融退役（由风险点增量率、交锋修正率与 pairwise 盲评替代）；decision_grounding 的 judge 版 SHALL 降级为抽查校准用，其 dg 检查（执行参数出处）SHALL 以代码化口径为主。citation 腿 SHALL 以四桶拆报口径（blocked / analyst_true_fail / surgical_repaired / verifier_normalized）呈现，SHALL NOT 以旧 `citation_pass` 标量作层增量解读。

**跑批入口唯一化**：跑批驱动 SHALL 为薄壳——判分、维度适用性过滤、judge_vars 落盘 SHALL 全部调用库侧（`evals/ablation.py`）函数，驱动侧 SHALL NOT 存在判分/过滤/落盘的重复实现；判分口径变更 SHALL 只改库侧一处。

**维度适用性过滤的唯一实现**：某变体不存在的层 SHALL NOT 被 judge 评分——评「不存在的层」只会产伪影（宽容评虚层 vs 挑剔评真层的不对称，incident #112）。变体→适用维度的映射 SHALL 只有一处实现（`evals.ablation._applicable_dims`），跑批驱动 SHALL 复用该实现，SHALL NOT 在驱动侧另行硬编码变体分支。适用性过滤 SHALL 在**判分调用前**生效：被过滤维度 SHALL 记 `None` 且 SHALL NOT 产生任何 judge 调用。

**层增量点估计的字段名与配对单元**：层增量的点估计字段 SHALL 命名为 `diff_mean`（值为均值差，与 `paired_bootstrap_ci` 的 mean-diff 口径一致），SHALL NOT 沿用 `diff_median` 之类与实际统计量不符的名称。聚合报告 SHALL 披露配对单元为**标的**而非 run（同标的同变体重复先取中位数，再以标的为配对单元），并给出配对单元数——使「3 标的 × 10 重复」不等于「有效 n=30」这一点对读报告的人可见。

**结论句式纪律**：消融结论 SHALL 只有两种合法形态——① CI 整体低于决策阈值时为真阴性结论（「在本实验分辨率（MDE=X）下，该对象增量低于其成本对应阈值，建议降级/裁剪」）；② CI 同时跨 0 与阈值时为诚实悬置（「分辨率不足，需扩至 N 只标的」）。裸「未获统计支持」SHALL NOT 作为合格结论，必须附 MDE；决策阈值 SHALL 附成本换算依据。

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

