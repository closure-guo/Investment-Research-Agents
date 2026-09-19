## MODIFIED Requirements

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

### Requirement: 数据对齐消融实验

系统 SHALL 支持数据对齐消融：构造三个架构变体——(a) 单分析师直出、(b) 分析师 + Bull/Bear 辩论、(c) 完整 5 层——所有变体接收**完全相同**的 state 快照（fetch_data / compute_metrics 输出重放），仅 Agent 编排不同。每变体 × 每标的 SHALL 重复运行 3 次取中位数。消融报告 SHALL 以 citation_pass 率与 judge 分数（带 CI）衡量各层增量价值，SHALL NOT 用单变体单次运行下结论。

**材料落盘与参数化**：消融每完成一条 run SHALL 落盘该 run 的 judge 输入材料（`judge_vars`）到独立文件（按 `(variant, ticker, repeat)` 命名），并在 run 记录中携带材料路径与 judge 明细（分数、纯定性论点数、是否封顶、枚举是否缺失）——使 rubric 变更后的重判 SHALL 可离线完成（不重跑管线、不依赖从 Langfuse 反解材料），并按同一口径复算。标的列表与重复次数 SHALL 经 CLI 参数化（默认值保持 3 标的 / 3 重复），实际取值 SHALL 写入产物 config；断点续跑的完成键 SHALL 仍为 `(variant, ticker, repeat)`，参数变更后已完成 run SHALL NOT 被重复消耗 token。

**judge 分数取 K 次均值（噪声下限披露）**：round11 实测同一材料同一 rubric 的单次 judge 调用在 5/4 边界**双峰翻转**（宁德 04baff5c，n=13 次调用：4 分 7 次 / 5 分 6 次，σ≈0.5）——该噪声与消融待测的层级增量（0.25–0.5 量级）同阶，单次调用不足以支撑层间比较。故消融的 judge 分数 SHALL 以 K 次重复的**均值**为点估计（K 经 CLI 声明并写入产物，默认 ≥3）；SHALL NOT 用中位数——双峰分布 p→0.5 时中位不降翻转概率，均值才无偏且方差随 K 收缩。SHALL 记录每次分数与极差（`scores` / `score_spread`）使噪声保持可见，SHALL NOT 只留均值而静默抹掉离散度。解析失败/输入缺失的调用不计入均值但计入 `judge_failures`；封顶/枚举遥测取**最低分那次**调用的观测（最保守，任一次找到纯定性标头则封顶证据不丢）。

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
