## ADDED Requirements

### Requirement: 指标口径先行登记

cohort 评估的每个指标 SHALL 先在 `docs/evals/metrics.md` §1 登记口径（定义 / 分母 / 窗口 / 剔除规则 / 代码位置）后方可取数实施；口径变更 SHALL 先改台账再动代码。四轨道（供给成本 / 过程质量 / 方向能力 / 日频价值）SHALL 各自独立出读数，SHALL NOT 加权合成单一总分。

#### Scenario: 口径未登记即取数被拒

- **WHEN** 实施者在 `metrics.md` §1 未登记某指标的口径前编写其取数代码
- **THEN** 该实施步骤 SHALL 被视为未完成（tasks 硬依赖），评审 SHALL 打回

#### Scenario: 四轨道读数分离

- **WHEN** 一份 cohort 评估读数报告产出
- **THEN** 四轨道 SHALL 各自含独立结论小节
- **AND** 报告 SHALL NOT 出现跨轨道加权的合成分

### Requirement: 窗口切分与熔断日剔除

评估 SHALL 按供给面事件表切窗：`W1 = 2026-09-25~10-10`（kimi 分析 LLM）、`W2 = 2026-10-13~10-16`（deepseek + 旧 judge）、`W3 = 2026-10-17 起`（deepseek + 新 judge）。段内出结论；段间差异 SHALL 只作为换模型影响专案记录，SHALL NOT 并入轨道结论。provider 配额熔断整批失败日（如 10-09 / 10-16）SHALL 从轨道 A 成本均值中剔除并单独列示。后续新增供给面切换事件时 SHALL 增段并登记。

#### Scenario: 跨段数据不混报

- **WHEN** 轨道 A 报告单份 token 成本
- **THEN** SHALL 分段给出 W1 / W2 / W3 各段均值
- **AND** SHALL NOT 报告跨段混合均值作为任一段的结论依据

#### Scenario: 熔断日剔除留痕

- **WHEN** 某交易日因 provider 配额熔断整批失败
- **THEN** 该日 SHALL 被剔除出成本均值并在读数中单独列示（日期 / 原因）
- **AND** 成功率读数 SHALL 仍按全部计划日分母计算

### Requirement: Neutral 反事实读数（只读自算）

对 cohort 产出的 neutral（观望）观点，评估 SHALL 以反事实超额收益为判据：T+1 开盘至 T+20 收盘区间该股收益减同期基准（沪深 300）收益得 `r_e`。计算 SHALL 由评估侧只读自算（行情快照 + 基准收盘序列），SHALL NOT 依赖结算调度、SHALL NOT 写入 `predictions` 任何字段。同票同日多条 neutral SHALL 去重（取当日最新一条）。行情缺日的样本 SHALL 剔除并披露 `missing_bars` 计数，SHALL NOT 插值。判读标准：池内 `r_e` 分布中心显著 >0 ⇒ 观望在系统性错过机会（进全观望锁定处置证据）；中心 ≤0 ⇒ 观望自洽。

#### Scenario: 反事实读数可即时产出

- **GIVEN** 某 neutral 观点已发出且其后 T+1 起有行情
- **WHEN** 评估运行
- **THEN** 该样本的 `r_e` SHALL 被计算并进入分布，无论其 `predictions.status` 是否仍为 open

#### Scenario: 评估零写入

- **WHEN** 反事实读数计算完成
- **THEN** `predictions` / `cohort_runs` / 结算相关表 SHALL 无任何写入或状态变化

#### Scenario: 行情缺日剔除披露

- **GIVEN** 某样本 T+1~T+20 区间存在停牌或数据缺口
- **WHEN** 评估运行
- **THEN** 该样本 SHALL 被剔除且 `missing_bars` 计数 SHALL 出现在读数报告中

### Requirement: 交易日聚类统计单位

所有跨样本检验 SHALL 以交易日为统计单位：先算每日横截面均值（该日全部有效样本的均值），再跨日检验（n = 有效交易日数）。读数报告 SHALL 附每段有效交易日数（n）与「未达显著 ≠ 无差异」声明；在 n < 15 的段，结论 SHALL 限定为量级描述，SHALL NOT 使用「显著优于 / 劣于」表述。

#### Scenario: 同日样本不重复计自由度

- **WHEN** 对 neutral 反事实均值做检验
- **THEN** 样本单位 SHALL 为「交易日的池内均值」，同日 10 只 SHALL NOT 作为 10 个独立样本计入检验

#### Scenario: 小样本段结论限级

- **GIVEN** 某段有效交易日 n < 15
- **WHEN** 读数报告对该段下方向性结论
- **THEN** SHALL 使用量级描述（如「均值 +0.8pp，n=10」）
- **AND** SHALL NOT 出现显著性裁决表述

### Requirement: Confidence 堆积归因闸

confidence 字段在进入本程序任何下游使用（校准曲线、按置信分组读数）前，SHALL 先完成堆积形态归因：按（日期 × 方向）分桶定位堆积起止日是否与供给面切换重合，并抽 ≥5 条堆积样本人工判读原文。归因结论 SHALL 为三态之一：`prompt-anchor` / `default-fallback` / `genuine-mode`，并带处置去向（prompt delta / 解析器检查 / 保留记录）。

#### Scenario: 未归因禁止下游使用

- **GIVEN** confidence 0.55 堆积（38%）尚未归因
- **WHEN** 评估产出读数
- **THEN** 读数 SHALL NOT 包含基于 confidence 的校准曲线或置信分组读数
- **AND** 归因小节 SHALL 单列

### Requirement: 方向性结算读数的到期闸

short/long 观点的结算胜负读数 SHALL 设到期闸：open 方向性观点到期数 ≥30 或最近一批集中到期（约 2026-11-15）完成后，方可出正式读数；此前只允许标注 `preliminary` 的中期行，SHALL NOT 基于个位数胜负样本下方向能力结论。

#### Scenario: 小样本胜负不出结论

- **GIVEN** 已结算方向性观点中分出胜负的仅 3 条
- **WHEN** 中期读数产出
- **THEN** 该行 SHALL 标注 `preliminary`
- **AND** SHALL 附「样本不足以裁决方向能力」声明

### Requirement: 读数产物与台账记录

每轮评估 SHALL 产出：只读脚本运行记录（`--as-of` / `--universe-version` 参数化）、`docs/evals/` 读数报告（轨道分节 + 三前置小节 + n 声明）、`docs/evals/metrics/runs.jsonl` 追加一行（run / HEAD@启动 / means 摘要）。脚本 SHALL 零 LLM 调用。citation 读数 SHALL 从 Langfuse Scores 按 trace 拉取（覆盖率披露：有分数 trace 数 / 总 trace 数），通过率变化 SHALL 先归因到供给面切换再考虑分析师归因。

#### Scenario: runs.jsonl 记行

- **WHEN** 一轮评估收口
- **THEN** `runs.jsonl` SHALL 追加含 run 名 / 启动时 HEAD / 各轨道 means 摘要的一行

#### Scenario: citation 归因先行

- **WHEN** citation 通过率出现分段间变化
- **THEN** 读数 SHALL 先核对与供给面切换时点（10-13 / 10-17）的对应关系
- **AND** 在排除换模型解释前 SHALL NOT 将变化归因为分析师质量变化
