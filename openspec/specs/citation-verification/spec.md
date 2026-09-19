# citation-verification Specification

## Purpose
TBD - created by archiving change fix-citation-contract-diseases. Update Purpose after archive.
## Requirements
### Requirement: 序列引用负索引语义

引用校验器对序列型 field_ref SHALL 支持负索引（-1 = 最新一期，-N = 倒数第 N 期），该语义与序列长度及上下文裁剪窗口解耦。技术指标 context 的窗口说明 SHALL 明示负索引约定（"field_ref 引用序列值时用负索引：-1=最新一期"）。正索引按底层序列原位解析（legacy 语义不变）。
(Previously: 同前句；未涉及数组顺序声明)

技术指标 context SHALL 同时声明序列数组内部顺序：序列为**时间正序（旧→新）**，列表**末尾元素为最新一期**；LLM 引用负索引值前 SHALL 核对所在序列尾部（-1 即最后一个元素），不得将展示容器首元素或记忆中历史行情当作最新值。
(Previously: 无此条款——incident 022 中际旭创 13 条技术 claim 因方向歧义整体期次错位)

#### Scenario: 负索引解析为最新值

- GIVEN state 中某指标序列为升序时间序列且任意长度
- WHEN claim 的 field_ref 以 `-1` 索引该序列（如 `technical_indicators.MA.5.-1`）
- THEN 校验器 SHALL 取序列最后一个元素作为 ground truth

#### Scenario: 裁剪窗口变更不影响校验语义

- GIVEN 分析师 context 的序列裁剪窗口从 60 期调整为任意值
- WHEN LLM 按负索引约定引用序列值
- THEN 校验结果 SHALL 不因窗口变更而改变（负索引与长度无关）

#### Scenario: 数组方向歧义消除

- GIVEN 技术指标 context 展示时间正序序列（旧→新）且末尾为最新一期
- WHEN LLM 引用「最新一期」的指标值
- THEN LLM SHALL 引用序列末尾元素（`-1` 或对应的负索引），SHALL NOT 将窗口首元素（最旧一期）当作最新值
- AND 校验结果 SHALL 与其真实最新值一致（修复前该场景系统性期次错位 FAIL——incident 022）

#### Scenario: 引用自证约束

- GIVEN LLM 反幻觉规则要求引用负索引值前核对序列尾部
- WHEN LLM 声明「最新 MA5 = X」
- THEN LLM SHALL 从序列末尾元素读取 X（不得凭记忆或展示首元素推断）
- AND 该自证要求 SHALL 在技术分析师 prompt 中可判定生效（发布经 deploy_prompts.py）

### Requirement: 上下文与校验单一词表

分析师 context 的数据段标题 SHALL 内联标注对应 state 英文键（如「利润表（income_statement，近3年）」），使 LLM 生成的 field_ref 与校验器解析的键天然同源；校验器 SHALL NOT 维护中文标签映射表。引用校验器 SHALL 支持 DataFrame 的「行键.列名」两段解析（行键按单元格值匹配，列名须为真实列）与 `[N]` 括号索引展开。

#### Scenario: 英文键引用可解析

- GIVEN context 段落标题含英文 state 键标注
- WHEN LLM 以该英文键为 field_ref 根键引用数据（含 DataFrame 行键.列名与 `[N]` 括号索引）
- THEN 校验器 SHALL 按对应 state 结构解析出 ground truth

#### Scenario: 中文标签不再是可解析词表

- GIVEN field_ref 根键为未标注进 context 英文键体系的中文标签
- WHEN 校验器解析该引用
- THEN SHALL 按不可路径处理（FAIL），不静默映射

### Requirement: 数值型相对容差

数值型 claim 的 PASS 判定 SHALL 为 |delta| < 0.01 或相对误差 < 0.5%（与计算型容差对齐），消除大数量级数值的假阴性。(Previously: data-ordering-citation-contract（archive）约定校验算法不变，数值型为绝对容差 0.01)

#### Scenario: 亿元级数值四舍五入通过

- GIVEN ground truth 为 1,038,756,658.94 量级的数值
- WHEN stated_value 与其相差 0.5（远小于 0.5% 相对误差）
- THEN 校验 SHALL PASS

#### Scenario: 显著偏离仍失败

- GIVEN ground truth 为任意数值
- WHEN stated_value 相对误差 ≥ 0.5% 且绝对差 ≥ 0.01
- THEN 校验 SHALL FAIL

### Requirement: 计算型声明重算注册表全覆盖

claim 的**校验深度 SHALL NOT 由其自我声明（`claim_type`）决定**：`field_ref` 根键命中重算注册表的
claim（无论申报为 `numerical` 还是 `computational`）SHALL 一律从 state 原始数据重算 ground-truth；
重算注册表 SHALL 覆盖 `metrics/` 模块的全部纯函数指标族（偿债、盈利、运营、现金流、杜邦、技术指标、
风控指标）**以及全部由代码从数据快照计算的派生字段（含 `garp_result`、`anomalies`）**，每个注册根键
SHALL 有独立的重算 fixture 测试（从原始报表数据重算，不依赖 LLM、不调外部接口）。字符串枚举型派生
字段（如 GARP failures 集合）SHALL 按集合相等比对。未注册根键的计算型 claim SHALL 判 UNVERIFIABLE，
且 SHALL 计入覆盖缺口指标供覆盖率审计。重算与直读两条路径 SHALL 共用同一比对实现（方向对齐 +
percent/万/亿候选归一 + 相对容差），SHALL NOT 各自维护容差语义。重算输入不可得（如 state 缺原始报表）
时 SHALL 显式降级回直读比对并保留覆盖缺口标记，SHALL NOT 静默等价于未校验。

(Previously: 重算路由由 `claim_type=computational` 触发——LLM 把自算值标成 numerical 即可绕过重算，P1 冻结批 5/5 复现)

#### Scenario: 已注册指标重算通过

- **GIVEN** Agent 报告含 claim（如 `solvency_metrics.资产负债率.2024`），其根键已注册
- **WHEN** 执行校验
- **THEN** 系统 SHALL 从 state 原始数据经对应纯函数重算 ground-truth，按相对容差 0.5% 判定 PASS/FAIL

#### Scenario: 标签不得绕过重算

- **GIVEN** claim 申报 `claim_type="numerical"` 而 `field_ref` 根键已注册重算
- **WHEN** 该根键的派生 dict 与原始数据不一致（污染或自算错误）
- **THEN** 校验 SHALL 判 FAIL（真值取自重算，SHALL NOT 取自可被污染的派生 dict）

#### Scenario: 未注册根键显式降级

- **WHEN** 计算型 claim 的根键未在注册表中
- **THEN** 校验结果 SHALL 为 UNVERIFIABLE
- **AND** 该事件 SHALL 计入覆盖缺口计数，SHALL NOT 静默等同于 FAIL 或被忽略

#### Scenario: 容差语义不回归

- **WHEN** 注册表扩展或路由变更后执行任意校验
- **THEN** 数值容差（绝对 0.01 / 相对 0.5%）与三态裁决（PASS/FAIL/UNVERIFIABLE）语义 SHALL 与既有契约一致

#### Scenario: 快照派生字段可验

- **WHEN** Agent 报告含 `garp_result`、`anomalies` 等快照派生字段的 claim
- **THEN** 校验 SHALL 经注册的重算/集合比对得出 PASS/FAIL，SHALL NOT 恒为 UNVERIFIABLE

### Requirement: UNVERIFIABLE 占比监控

系统 SHALL 在每次引用校验完成后向 Langfuse 上报 Score `citation_unverifiable_ratio`（UNVERIFIABLE 占全部 claim 比例），关联 `langfuse_trace_id`。该指标 SHALL 作为数据层退化（数据源接口变更、事件管线降级、注册表覆盖缺口扩大）的先行监控信号。

#### Scenario: 占比上报

- **WHEN** citation_node 完成一次批量校验
- **THEN** 系统 SHALL 上报 `citation_unverifiable_ratio`（0-1 浮点）至 Langfuse，与既有 `citation_pass` 并列
- **AND** Langfuse 不可用时 SHALL 记 WARN 且不阻断业务管线

#### Scenario: 占比突升告警

- **GIVEN** `citation_unverifiable_ratio` 的滚动均值较基线上升超过阈值（默认 +10pp，可配置）
- **WHEN** 监控任务检测到突升
- **THEN** 系统 SHALL 产生告警记录，提示排查数据层或注册表覆盖缺口

### Requirement: context 数据形态语义声明

系统 SHALL 在构建分析师 context 时，为每个序列/数组数据块注入机器生成的语义头，显式声明：排序方向（升序/降序）、最新期定位语义（如 index -1 = 最新交易日及其日期）、序列长度。语义头由代码依据 state 实际数据形态生成，SHALL NOT 依赖 prompt 自然语言要求作为主要防线。LLM 在 context 中见到的数据形态语义 SHALL 与校验器解析语义一致。

#### Scenario: 序列块带语义头

- **WHEN** 构建含时间序列的分析师 context（technical/macro/fundamental）
- **THEN** 每个序列块 SHALL 附语义头（如"升序，index -1 = 最新交易日 2026-08-28，共 60 期"）
- **AND** 语义头内容与 state 中该序列的实际排序一致

#### Scenario: 期次错位不重现

- **GIVEN** 类似中际旭创的暴涨标的
- **WHEN** 技术分析师产出技术形态叙事
- **THEN** 叙事引用的期次 SHALL 与 state 最新期一致（不产生"窗口首元素当最新"的镜像叙事）

### Requirement: 术语与期次一致性校验

Claim SHALL 扩展 `metric_name`（指标枚举，含中文别名到规范键映射）与 `period` 字段。校验器 SHALL 校验：(a) `metric_name` 的规范键等于 field_ref 末端键；(b) `period` 与 field_ref 解析出的期间一致。不一致判 FAIL 并归入独立桶（semantic_term_mismatch / semantic_period_mismatch）。字段为 None（旧数据或未填）时 SHALL 跳过检查并计入覆盖缺口，SHALL NOT 静默 PASS。metric_name 已申报但词表无对应规范键（词表外）时 SHALL 同样跳过术语检查并计入覆盖缺口——state 指标段空间开放（报表行名/dupont/health_score/garp 等），词表不可闭合，词表外判 FAIL 经三标的冒烟实证全为误报（2026-09-01）。

#### Scenario: 术语张冠李戴被拦截

- **GIVEN** claim 的 field_ref 指向 `profitability_metrics.毛利率.2024`，metric_name 为"净利率"
- **WHEN** 执行校验
- **THEN** 判 FAIL，桶为 semantic_term_mismatch（即使数值与真值一致）

#### Scenario: 缺省字段显式降级

- **WHEN** claim 缺 metric_name/period（旧格式）
- **THEN** 跳过对应检查，计入覆盖缺口计数，其余检查照常

#### Scenario: 词表外术语显式降级

- **GIVEN** claim 申报了 metric_name，但词表无其规范键（如 "健康度评分" 之于 `health_score.total`）
- **WHEN** 执行术语检查
- **THEN** 跳过术语检查，计入覆盖缺口计数，SHALL NOT 判 FAIL；值级/期次/内部一致性检查照常

### Requirement: claim 内部一致性校验

校验器 SHALL 校验 claim 内部一致性：(a) `stated_value` 经归一化（剥离"约/接近/%"等修饰与单位）后可在 `interpretation` 中匹配到对应数值表述；(b) interpretation 中的方向词（增长/上升/改善 vs 下降/恶化）不与数值 delta 符号矛盾。不一致判 FAIL，桶为 internal_inconsistency。

#### Scenario: 数值两张皮被拦截

- **GIVEN** claim 的 stated_value 为 45.2，interpretation 写"毛利率约 30%"
- **WHEN** 执行内部一致性检查
- **THEN** 判 FAIL，桶为 internal_inconsistency

#### Scenario: 方向词矛盾被拦截

- **GIVEN** claim 断言指标变化，interpretation 含"大幅增长"，而数值 delta 为负
- **THEN** 判 FAIL，桶为 internal_inconsistency

### Requirement: 正文覆盖率校验（citation recall）

系统 SHALL 对最终报告 markdown 执行数字普查：提取全部数值表述（百分比/金额/倍数，归一化口径由 fixture 钉死），逐一与全部 claim 的 stated_value 在**普查容差（相对 2%，绝对值下限 0.01）**内匹配，产出 `citation_coverage = 已认领数值数 / 普查数值总数`，并作为 NUMERIC Score 上报 Langfuse。豁免清单（编号、评级刻度等）SHALL 显式声明并进 fixture。覆盖率 SHALL NOT 参与 after_citation 路由（首版只监控，低于阈值 0.8 告警）。
(Previously: 普查匹配复用校验器 0.5% 相对容差；豁免清单仅为编号/评级刻度等静态清单。)

普查匹配规则 v3（2026-09-02 人工终裁，issue #106）：
1. **模板/脚手架文本排除**：决策语义、仓位档位说明（position_size 档位/如总资金/试 探性仓位等）结构性文本区间的数值 SHALL NOT 计入普查；排除清单进 fixture。
2. **方向词符号不敏感**：数值邻近（±20 字符内）方向词（下滑/下降/增长/上升/减少/增加）时，按绝对值匹配——`下滑 10.05%` 与 claim `-10.05` 认领命中。
3. **不等式匹配**：数值带「超/约/近/低于/高于/达到」修饰时，与 claim stated_value 按阈值比较（|value − stated| ≤ 2%·|stated| 视为命中），不再要求等值。
4. **事件数字标记**：数值能匹配到 state 事件源（key_events/news 中出现的数值）时，标记 `event_covered` 而非 unmatched；事件来源数字 SHALL 允许不建数值 claim，但正文 SHALL 内联注明来源事件。

#### Scenario: 黑数字暴露

- **GIVEN** 报告正文含数值"营收 10.39 亿"，但无任何 claim 的 stated_value 与其匹配
- **WHEN** 执行数字普查
- **THEN** 该数值计为未认领，`citation_coverage` 相应降低
- **AND** 低于阈值时产生告警记录，不阻断报告产出

#### Scenario: 方向词符号认领
- **GIVEN** 报告正文含「净利率下滑 10.05%」，claim stated_value = -10.05，字段可校验
- **WHEN** 执行数字普查
- **THEN** 该数值按绝对值与 -10.05 匹配，计为已认领
- **AND** 不再因符号相反计为黑数字

#### Scenario: 阈值式复述认领
- **GIVEN** 报告正文含「ROE 超 30%」，claim stated_value = 32.53
- **WHEN** 执行数字普查
- **THEN** |30 − 32.53| ≤ 2%·32.53 判定命中，计为已认领

#### Scenario: 脚手架文本不计普查
- **GIVEN** 报告含 position_size 档位说明「light=试探性仓位（如总资金 10-20%）」
- **WHEN** 执行数字普查
- **THEN** 该区间数值（10%、20%）不计入普查总数

#### Scenario: 事件数字豁免
- **GIVEN** 报告引用新闻事件「出厂价由 969 元上调至 1169 元」，且该数值出现在 state 事件源中
- **WHEN** 执行数字普查
- **THEN** 标记 `event_covered`，不计入 unmatched
- **AND** 正文未内联注明来源事件时，报告 SHALL 记录提示

### Requirement: 校验失败按桶分流与定向重试

after_citation 路由 SHALL 按 FAIL 桶分流：(a) 值级 FAIL（value_mismatch，ground_truth 存在且超容差）触发**定向重试**——仅重跑产出该 claim 的分析师节点，重试上下文 SHALL 附失败 claim 明细与 ground_truth；(b) 格式/契约类 FAIL（路径不可解析、术语/期次/内部不一致）SHALL NOT 触发重试，记为 incident 候选；(c) UNVERIFIABLE 不触发重试。重试上限（iteration_count < 3）语义不变；重跑后该分析师全部 claim SHALL 重新校验。

#### Scenario: 值级 FAIL 定向重跑

- **GIVEN** 基本面分析师 1 条 claim 值级 FAIL，其余分析师全部 PASS
- **WHEN** 路由触发重试
- **THEN** 仅基本面分析师重跑（附失败明细与 ground_truth），其余分析师结果复用
- **AND** 重跑后其全部 claim 重新校验

#### Scenario: 格式类 FAIL 不重试

- **WHEN** FAIL 桶为 semantic_term_mismatch / path_unresolvable / internal_inconsistency
- **THEN** 不触发分析师重跑，记录 incident 候选并放行（flag 语义）

### Requirement: state anomalies 自动补登记

系统 SHALL 在引用校验前执行自动补登记：对正文数值与 state 的 `growth_rates`（含 anomalies 的 |growth|>0.5 子集，也含非 anomaly 增速）做**双条件共现匹配**——① 数值与 `growth_rates.{dim}.{metric}` 的整数百分比渲染（`:.0%`）按**取整感知容差**（≤ 0.5 个百分点）匹配；② 指标名与数值出现在同一句/段落。双条件满足时 SHALL 自动生成 claim，field_ref **SHALL 指向结构化真值**（`growth_rates.{dim}.{metric}`），渲染字符串 SHALL 仅用于定位、不用于验证（禁止"字符串验字符串"）。不满足任一条件的数值 SHALL 保持 unmatched 走原流程。自动补登记的 claim SHALL 与人工申报 claim 走完全相同的校验路径（容差/桶/重试语义不变）。

#### Scenario: anomalies 数值自动补登记
- **GIVEN** state 含 anomaly「solvency.净债务/EBITDA: 变化率-368%」，growth_rates.solvency.净债务/EBITDA = -3.676（即 -367.6%）
- **AND** 报告正文同句含「净债务/EBITDA 变化 -368%」
- **WHEN** 执行自动补登记
- **THEN** 生成 claim，field_ref = growth_rates.solvency.净债务/EBITDA，stated_value = -3.68
- **AND** 取整感知容差（0.5 个百分点）内与真值 -3.676 匹配，校验 PASS

#### Scenario: 指标名不共现不补登记
- **GIVEN** 正文某处单独出现「-368%」，同句无任何 anomaly 指标名
- **WHEN** 执行自动补登记
- **THEN** 不生成 claim，数值保持 unmatched 走原流程

#### Scenario: 编造数字不被洗白
- **GIVEN** 正文含「账上资金 1400 亿」，state 无对应数值/指标名共现
- **WHEN** 执行自动补登记
- **THEN** 不生成 claim，保持 unmatched → 进入 reject/人工复核
- **AND** 验证标准与人工申报 claim 完全一致，不因补登记而降级

### Requirement: comparative 基期值双端申报与校验

系统 SHALL 要求 comparative（同比/环比/对比）claim 声明双端数值：当期 `stated_value` + 基期 `stated_value_b`（field_ref_b 指向基期字段）。校验器 SHALL 对基期值与当期值使用相同的容差语义分别校验；未申报基期的 comparative claim SHALL 判 FAIL 并记录。prompt 侧 SHALL 强制比较值申报纪律（当前值 + 基期值 + 对应 field_ref/field_ref_b），改动后须经 prompt 发布流程。

在此基础上，比较型 claim SHALL 支持两类申报：

1. **方向型**（`stated_value` 为 `greater_than` / `less_than` / `equal_to`）：按 `field_ref` 与 `field_ref_b` 双端真值校验比较方向；`field_ref_b` 设而 `stated_value_b` 缺 SHALL 判 FAIL（比较基期不得裸奔）——既有语义不变。
2. **差值型**（`stated_value` 为数值）：SHALL 用双端真值重算差值（`a - b`）与申报值按容差比对，参考系取两操作数绝对值较大者（`max(|a|,|b|)`）；`direction` 已申报时 SHALL 同时校验符号方向（`negative` = 正文以正向数值表述负向差值），未申报时 SHALL 跳过方向检查并在结果上标记覆盖缺口（显式降级，不静默 PASS）。差值型 claim 的 `stated_value_b` SHALL 为可选（申报则按既有容差校验，缺省不判 FAIL——申报对象是差值本身）；但申报値经归一后≈任一端真值时（LLM 以某一端的值填 stated_value 的回声形态）SHALL 判 UNVERIFIABLE（未知语义不武断判错）。

差值型的两端路径（`field_ref` / `field_ref_b`）任一缺失/不可解析、或任一非数值时，SHALL 按既有语义判 FAIL（path_unresolvable），不得以 UNVERIFIABLE 吞掉。

#### Scenario: 基期值裸奔被拦截
- **GIVEN** 报告叙述「2025 净利率 19.07%，较 2024 年 21.93% 下滑」，claim 仅申报 2025 值（stated_value=19.07）
- **WHEN** 校验 comparative claim
- **THEN** 判 FAIL，原因：comparative 未申报基期 stated_value_b
- **AND** 引导 LLM 补申报 2024 基期（21.93）与 field_ref_b

#### Scenario: 差值型申报通过

- **GIVEN** 正文「MA5 较 MA20 低约 3.62」，claim 声明 `comparative`、`field_ref` 与 `field_ref_b` 双端、`stated_value=3.62`、`direction=negative`
- **WHEN** 双端重算差值 = -3.62（在容差内）
- **THEN** SHALL 判 PASS（符号方向一致）

#### Scenario: 差值型申报超容差

- **WHEN** 申报差值 5.0、重算差值 -3.62（超容差）
- **THEN** SHALL 判 value_mismatch FAIL

#### Scenario: 差值型方向未申报的显式降级

- **WHEN** 差值型 claim 未申报 `direction`
- **THEN** SHALL 仅按差值量级比对，并在结果上标记覆盖缺口；SHALL NOT 因方向未知而静默 PASS

### Requirement: 增速类计算值补登记（D2 吸收）

系统 SHALL 对「正文增速数值 + 增速指标名共现」执行补登记：`growth_rates` 中全部指标（含非 anomaly 的 |growth|≤0.5）按 D2 双条件补登记为 numerical claim（field_ref = `growth_rates.{dim}.{metric}`），取整感知容差验证。该机制覆盖「FCF 同比增长 96.6%」类可重算增速数字，即使未触发 anomaly 也补。不可定位结构化真值的数值 SHALL 不补登记，保持 unmatched。

#### Scenario: 非 anomaly 增速补登记
- **GIVEN** 正文含「FCF 同比增长 96.6%」，growth_rates.cashflow.FCF = 0.966（|growth|>0.5 会触发 anomaly，但补登记不依赖 anomaly 存在）
- **WHEN** 执行增速补登记
- **THEN** 生成 claim，field_ref = growth_rates.cashflow.FCF，stated_value = 0.97
- **AND** 0.5pp 取整容差内与真值 0.966 匹配，标准校验 PASS

### Requirement: 覆盖率缺口打回补 claim（coverage 闭环最后一公里）

系统 SHALL 在覆盖率普查产出 unmatched（D2/D4 自动补登记与 D5 事件豁免之后仍未被认领的数字）时，将缺口反馈回正文来源分析师节点做**局部补证**：反馈 SHALL 携带未认领数字（原文 + 解析值），提示分析师「补建对应 claim 或删除正文数字」；补证后 SHALL 重新校验与普查。打回上限 SHALL 与既有 citation 重试共享迭代上限（<3），停滞降级语义不变；纯评论性/无状态源数字（非硬数据）SHALL 不触发打回（属可接受口径）。

#### Scenario: 无来源数字打回补证
- **GIVEN** 正文含「账上资金超过 1400 亿元」，D2/D4 无法自动补登记（state 无对应真值），D5 不豁免
- **WHEN** 覆盖率普查
- **THEN** 将该数字加入打回反馈，定向重跑来源分析师
- **AND** 分析师补建货币资金 claim 或删除该正文数字，复验后 unmatched 消除

### Requirement: 校验结果结构化下发

系统 SHALL 在报告就绪时随事件/会话数据下发结构化引用数组；每项 SHALL 包含稳定 `id`、`claim`、`source`、`verdict`（verified/failed/unchecked）与 `detail`；无引用或旧数据缺省该字段时 SHALL 正常返回，不报错。

#### Scenario: 报告携带引用数组

- **GIVEN** 一次深度分析完成且经过引用校验
- **WHEN** 报告就绪事件发出
- **THEN** 负载中 SHALL 含引用数组，每项含上述五字段

#### Scenario: 旧数据兼容

- **GIVEN** 无引用字段的历史会话
- **WHEN** 前端读取该会话
- **THEN** 系统 SHALL 正常返回，引用字段缺省

### Requirement: claim direction 独立申报与方向一致性校验

Claim SHALL 增加 `direction` 字段（`"positive" | "negative" | "flat" | None`），数值型（numerical/computational）claim 的 direction 由分析师在登记时显式申报，方向语义 SHALL NOT 由校验器在正文文本中推断。当 direction 已申报时，校验器 SHALL 校验方向一致性：`sign(stated_value) × direction 与 sign(ground_truth)` 不符 → 判 FAIL，分桶为 `direction_mismatch`（新增桶，纳入 value_mismatch 同级的定向重试目标）。当 direction 为 None（旧格式 claim），校验器 SHALL 跳过方向检查并将该 claim 计入覆盖缺口（显式降级，SHALL NOT 静默 PASS），与 metric_name/period 未申报的既有降级先例一致。

#### Scenario: 方向申报与真值不符判 FAIL

- **WHEN** claim 申报 `stated_value=10.05, direction="negative"`，而 field_ref 真值为 `-10.05`
- **THEN** 校验器 SHALL 判 PASS（绝对值匹配且符号经 direction 修饰后一致）

- **WHEN** claim 申报 `stated_value=10.05, direction="positive"`，而 field_ref 真值为 `-10.05`
- **THEN** 校验器 SHALL 判 FAIL 且 bucket 为 `direction_mismatch`

#### Scenario: 旧格式 claim 显式降级

- **WHEN** claim 的 direction 为 None 且正文方向词表兜底未命中
- **THEN** 校验器 SHALL 跳过方向检查、按既有数值容差判定，并将该 claim 计入覆盖缺口（coverage_gap），SHALL NOT 判 PASS 后静默不计

### Requirement: 方向词表退役为未申报兜底

正文覆盖率普查的方向词符号不敏感匹配（`_DIRECTION_WORDS`）SHALL 仅在 claim 未申报 direction 时作为兜底启用；已申报 direction 的 claim SHALL NOT 再依赖文本方向词推断。兜底词表 SHALL 补充语料实证的高频漏网词：`负增长`、`跌幅`、`收窄`（词表由 11 词扩至 14 词后冻结，后续长尾由 coverage 打回回路兜底，SHALL NOT 无评估依据继续扩表）。

#### Scenario: 已申报 claim 不走词表

- **WHEN** claim 已申报 direction="negative" 且 stated_value 绝对值与真值匹配
- **THEN** 校验器 SHALL 直接按 direction 判定，SHALL NOT 因正文方向词未命中而 FAIL 或计缺口

#### Scenario: 未申报 claim 走兜底词表

- **WHEN** claim direction=None，正文写「营收负增长 5.2%」且真值为 -5.2
- **THEN** 兜底词表 SHALL 命中「负增长」触发符号不敏感匹配，该数字 SHALL 认领成功

### Requirement: 修复回填后强制重校验

单点修复回填正文后，系统 SHALL 立即重跑完整 citation 校验（值容差、术语/期次一致性、coverage 普查全路径），并以重校验结果作为该轮最终校验状态。修复 SHALL NOT 跳过或豁免任何校验分支（含方向检查，若方向 delta 已落地）；改写引入的新错误 SHALL 照常按既有分桶暴露并进入既有重试/放行路由——修复回路 SHALL NOT 吞错。若重校验后同处仍 FAIL，该 claim SHALL 回退至目标分析师全量重试路径（若轮次与阈值允许）或按既有放行规则处理，SHALL NOT 对同处 claim 连续发起第 2 次单点修复。

#### Scenario: 修复成功以重校验为准

- **WHEN** 出错句「净利润下滑 10.4%」被改写为真值一致的表述并回填
- **THEN** 系统 SHALL 重跑完整校验，全部通过时该轮 citation_pass 判 PASS

#### Scenario: 修复引入新错不吞错

- **WHEN** 改写回填后重校验发现同句新数字与真值仍超容差
- **THEN** 该 claim SHALL 照常判 value_mismatch FAIL 并按既有路由处理，SHALL NOT 因「已修复过」而放行

#### Scenario: 同处不二次单点修复

- **WHEN** 某 claim 经单点修复回填后重校验仍 FAIL
- **THEN** 系统 SHALL NOT 对该 claim 再次发起单点修复

### Requirement: 修复遥测与原桶保留

单点修复 SHALL 在 Langfuse trace 留痕：修前句、修后句、ground_truth、重校验结果。被修复的 claim SHALL 保留原 value_mismatch 分桶计数并追加 `value_mismatch_repaired` 遥测口径——修复 SHALL NOT 从分桶统计中抹除原失败信号（prompt 优化的归因依据）。

#### Scenario: 修复留痕可归因

- **WHEN** 某轮 1 条 value_mismatch 经单点修复后 PASS
- **THEN** trace SHALL 同时可见：原 FAIL 记录（value_mismatch 桶）、value_mismatch_repaired 标记、修前修后句与真值

### Requirement: 数值单位量级归一

数值比对 SHALL 在容差比较前进行单位归一：优先从 claim 的 interpretation 文本中提取与 stated_value 面值相同 token 的紧邻单位词（亿/万/元），按 1e8/1e4/1 缩放后与真值比较；interpretation 无单位词时，truth/stated 比值落在 1e4 或 1e8 的相对容差带内 SHALL 判 PASS 并标记 `unit_inferred`；数据根键 SHALL 注册 `unit` 属性（元/万元/%/无量纲），真值先按表单位归一。有单位词且缩放后仍不匹配 SHALL 维持 FAIL。

#### Scenario: 亿元申报对元真值通过

- **WHEN** claim stated_value=583.95、interpretation 含「583.95亿元」、真值 5.8394610072e10（元）
- **THEN** 校验 SHALL 判 PASS 并标记 unit_normalized="亿"

#### Scenario: 无单位词比值兜底

- **WHEN** interpretation 不含单位词且 truth/stated ≈ 1e4 或 1e8（相对容差内）
- **THEN** 校验 SHALL 判 PASS 并标记 unit_inferred

#### Scenario: 缩放后仍不匹配维持 FAIL

- **WHEN** 单位缩放后与真值的偏差超出容差
- **THEN** 校验 SHALL 维持 value_mismatch FAIL

### Requirement: 路径形态归一

field_ref 解析 SHALL 对 DataFrame 行键同时尝试原始值与去连字符形式（`2025-12-31` ↔ `20251231`）；根键为 `quarterly_trend` 且路径段匹配 `\d{4}Q[1-4]` 时 SHALL 查 `quarters` 列表换算为位置索引。**根键别名 SHALL 归一：`derived` → `derived_series`**（analysts context 曾提示简写前缀 `derived.`，与 state 根键不一致——归一面同时覆盖普通解析与计算型注册表查找，两种写法等价）。归一 SHALL 双向幂等，SHALL NOT 改变既有负索引与 `[N]` 括号语义。

#### Scenario: 显示格式日期可解析

- **WHEN** claim field_ref 为 `financial_indicators.2025-12-31.加权每股收益`，行键存储为 `20251231`
- **THEN** 解析 SHALL 命中该行

#### Scenario: 季度标签换算位置

- **WHEN** claim field_ref 为 `quarterly_trend.yoy.2026Q2`
- **THEN** 解析 SHALL 经 quarters 列表定位该季度

#### Scenario: 根键别名等价解析

- **WHEN** claim field_ref 为 `derived.chg_5d`（简写根）而 state 根键为 `derived_series`
- **THEN** 解析 SHALL 归一命中该值
- **AND** 计算型 claim 引用 `derived.*` 时 SHALL 同样按 `derived_series` 查重算注册表（不得落 UNVERIFIABLE）

### Requirement: 术语一致性以真实列名为准

根键为 DataFrame 报表域（三大报表、financial_indicators）的 claim，其 metric_name 与解析出的真实列名一致时 SHALL 判术语一致；`metric_vocab` SHALL 维护报表列名到规范名的别名映射（如「归属于母公司的净利润」→「归母净利润」）。

#### Scenario: 照抄真实列名通过

- **WHEN** claim metric_name 为「归属于母公司的净利润」且该列真实存在于利润表
- **THEN** 语义核对 SHALL 判一致，SHALL NOT 判 semantic_term_mismatch

### Requirement: 符号校验限定有符号量

指标注册表 SHALL 为每个指标声明 `signed` 属性（增长率/同比环比/变动幅度/技术柱状等为 True；指数/比率/价格等水平量为 False）。`direction` 申报 SHALL 仅在 signed 指标上参与符号比对；非 signed 指标上的 negative/positive 申报 SHALL 记覆盖缺口，SHALL NOT 判 direction_mismatch。分析师 prompt 中 `direction` 字段 SHALL 注明「符号语义，非高低」。

#### Scenario: 水平量的负向申报不判 FAIL

- **WHEN** claim 为「PMI 49.8 低于荣枯线 50」，metric 注册 signed=False，direction 申报为 negative
- **THEN** 校验 SHALL 记覆盖缺口提示，SHALL NOT 判 direction_mismatch FAIL

#### Scenario: 有符号量方向照常校验

- **WHEN** claim 为增长率类（signed=True），direction=positive 而真值为负
- **THEN** 校验 SHALL 维持 direction_mismatch FAIL

### Requirement: 文本 claim 分型与回声匹配

`claim_type ∈ {entity, regulatory}` 或 `source_type = event` 的文本 claim SHALL 不计入 FAIL=0 分母、SHALL 不计覆盖缺口；校验 SHALL 先做回声匹配（claim/interpretation 归一后与 `news_list[*].title`、`key_events[*]` 子串匹配），命中判 PASS(echo)，未命中判 UNVERIF​IABLE(text) 并单独计数。

#### Scenario: 标题回声命中

- **WHEN** 文本 claim 引用的新闻标题（归一后）出现在 news_list 标题中
- **THEN** 校验 SHALL 判 PASS(echo)

#### Scenario: 未命中不污染分母

- **WHEN** 文本 claim 回声未命中
- **THEN** 校验 SHALL 判 UNVERIFIABLE(text)、单独计数，SHALL NOT 计入阻断分母或覆盖缺口

### Requirement: 门禁三层分置与指标拆报

引用门禁 SHALL 分三层：阻断层 = 归一与准入规则后残余 FAIL 计数（`citation_blocked`，>0 阻断）；警告层 = coverage < 0.90 报警不阻断；跟踪层 = UNVERIFIABLE 占比（分文本/未注册两类单独计数）。`citation_minor_fail` SHALL 退役。实验报告与 trace SHALL 分别输出残余 FAIL 数、归一后由 FAIL 转 PASS 数（`verifier_normalized_count`）、文本/未注册 UNVERIFIABLE 数，SHALL NOT 单独呈现混合 `citation_pass` 作为引用质量指标。

#### Scenario: 归一后残余 FAIL 阻断

- **WHEN** 归一后仍存在准入规则内的 FAIL
- **THEN** 阻断层 SHALL 置位，管线按既有渲染路径放行但标记阻断

#### Scenario: 覆盖率不足报警不阻断

- **WHEN** coverage < 0.90 且无残余 FAIL
- **THEN** 系统 SHALL 记录警告标记，SHALL NOT 阻断

#### Scenario: 拆报输出

- **WHEN** 一轮实验结束
- **THEN** 报告 SHALL 含残余 FAIL 数、verifier_normalized_count、文本/未注册 UNVERIFIABLE 数，SHALL NOT 呈现单一混合通过率

### Requirement: 注入式故障演练

归一上线 SHALL 以注入式故障验证门禁仍会开火：对真实 fixture 故意改错一个数字（非单位换算、非路径/格式差异），校验 SHALL 判 FAIL 且阻断层置位。该演练 SHALL 作为回归测试长期保留。

#### Scenario: 注入真错误仍开火

- **WHEN** 对 fixture 中的 income_statement 营收真值注入 ±10% 偏差并生成对应 claim
- **THEN** 校验 SHALL 判 value_mismatch FAIL，阻断层置位

### Requirement: 单点修复的自动处置授权与边界

`value_mismatch` 稀疏失败的单点修复属于**真错桶的受护栏自动处置**：下列三条护栏全部成立时，SHALL 免于「自动化处置须以逐条人工终裁为前置」的一般要求（AGENTS.md 红线，incident 026）；任一护栏失效 SHALL 视为策略变更（须走 delta）并恢复终裁前置。

1. **不隐藏真错**：修复回填条数 SHALL 计入 `citation_analyst_true_fail`（修复会把真错藏进 PASS，真错口径必须包含它们），并 SHALL 单独计数为 `citation_surgical_repaired` 供观测修复触发与成功率。
2. **重校验仲裁**：修复回填后 SHALL 立即整体重校验，并以重校验结果为该轮最终状态（改写引入的新错照常 FAIL，SHALL NOT 因「已修复过」放行）。
3. **最小改写 + 可审计**：修复指令 SHALL 限制「只改错误数字及其直接联动措辞、禁止改写其他内容、禁止引入新数字」；每次修复 SHALL 在 trace 留痕（修前句/修后句/ground_truth/重校验结果）。

**触发边界**：SHALL 仅当同一分析师同一轮 `value_mismatch` 失败数 < 3（稀疏）时发起；≥3 处 SHALL NOT 单点修复。自动重试关闭态下修复失败 SHALL 直接按阻断标记放行（`citation_blocked` 置位），SHALL NOT 触发分析师重跑。

**变更纪律**：放宽稀疏阈值、移除真错口径、跳过强制重校验、允许修复引入新数字——任一 SHALL 先经 delta 修改本要求，SHALL NOT 作为实现细节直接调整。

#### Scenario: 稀疏失败触发且计入真错

- **GIVEN** 某分析师同轮 1 处 value_mismatch
- **WHEN** 单点修复成功且重校验 PASS
- **THEN** `citation_surgical_repaired` SHALL +1
- **AND** `citation_analyst_true_fail` SHALL 同时计入该条（不因修复而消失）

#### Scenario: 密集失败不触发

- **WHEN** 同一分析师同轮 value_mismatch ≥ 3 处
- **THEN** 系统 SHALL NOT 发起单点修复，按既有重试/放行路由处理

#### Scenario: 护栏变更须走 delta

- **WHEN** 需求方要放宽稀疏阈值、移除真错口径或跳过强制重校验
- **THEN** SHALL 先开 delta 修改本要求，SHALL NOT 作为实现细节直接调整

### Requirement: 派生键重算注册覆盖门禁

`compute_metrics` 产出的全部派生键 SHALL ⊆ 计算型重算注册表（`_COMPUTATIONAL_RECALC`）∪ 显式豁免表（每条豁免 SHALL 附理由）；未注册且未豁免的派生键被按计算型引用时判 UNVERIFIABLE 并计入覆盖缺口。

系统 SHALL 提供门禁测试：以覆盖全部可选分支（K 线/基准/季报/同业/行情）的构造 state 运行 `compute_metrics`，断言其全部产出键已注册或已豁免——新派生键未注册即测试红，不得依赖人工记忆补齐。

#### Scenario: 新派生键未注册即红

- **WHEN** 有人在 `compute_metrics` 新增派生键而未同步注册表/豁免表
- **THEN** 覆盖门禁测试 SHALL 失败并列出缺失键

#### Scenario: 注册键可重算

- **GIVEN** 派生键已注册（如 `growth_rates`）
- **WHEN** 计算型 claim 引用该键的子路径且真值存在
- **THEN** SHALL 用同一份 compute 代码重算并按既有相对容差裁决（不再 UNVERIFIABLE）

### Requirement: 比较型差值申报的显式降级与计数

comparative claim 的 `stated_value` 不属比较方向枚举（`greater_than` / `less_than` / `equal_to`）时——典型为 LLM 把差值或差幅数字（「MA5 较 MA20 低约 2.3%」的 2.3）填入 `stated_value`——校验器 SHALL 判 UNVERIFIABLE，bucket SHALL 为 `comparative_delta_unregistered`，且 SHALL 计入覆盖缺口计数（与「未注册根键的计算型 claim」同一计数通道），SHALL NOT 静默不计。UNVERIFIABLE 拆报 SHALL 将其单列为第三类 `citation_unverifiable_comparative_delta`（与文本类、未注册类并列），实验报告与 trace SHALL 分别输出。

本要求 SHALL NOT 为该路径新增验证语义：SHALL NOT 重算差值、SHALL NOT 判 PASS/FAIL、SHALL NOT 改变三枚举路径与 D3 基期校验的既有行为与执行顺序、SHALL NOT 触发阻断或重试。是否为比较型差值建立公式申报与重算（`formula` + `stated_delta`），SHALL 依据该计数在首轮实验中的占比与逐条人工归因另行决策，SHALL NOT 预先实现。

#### Scenario: 差值数字填入 stated_value 被计数

- **GIVEN** comparative claim `field_ref=technical_indicators.MA.5.-1`，`field_ref_b=technical_indicators.MA.20.-1`，`stated_value=2.3`
- **WHEN** 执行校验
- **THEN** 结果 SHALL 为 UNVERIFIABLE，bucket `comparative_delta_unregistered`
- **AND** 覆盖缺口计数 SHALL +1，SHALL NOT 出现在阻断分母

#### Scenario: 拆报单列

- **WHEN** 一轮实验结束
- **THEN** 报告 SHALL 含 `citation_unverifiable_comparative_delta` 计数，与 `citation_unverifiable_text`、`citation_unverifiable_unregistered` 并列
- **AND** `citation_unverifiable_ratio` 的构成 SHALL 可按三类分解

#### Scenario: 三枚举行为不变

- **WHEN** comparative claim `stated_value="less_than"` 且两端真值 a < b、基期申报合规
- **THEN** 校验 SHALL 判 PASS，与变更前完全一致
- **AND** `stated_value="less_than"` 而 a ≥ b 时 SHALL 判 FAIL、bucket `value_mismatch`，与变更前一致

#### Scenario: 不新增验证语义

- **WHEN** 非枚举 `stated_value` 的 comparative claim 进入校验
- **THEN** 校验器 SHALL NOT 尝试用两端真值重算差值与 `stated_value` 比对
- **AND** 阻断层 SHALL NOT 因该类 claim 置位，路由 SHALL 不受影响

### Requirement: 同义列名字段解析消歧

校验器按 field_ref 对快照 DataFrame 取值或重算时，SHALL 先做**列名消歧**：当目标表存在多个语义同名/近义列（如利润表同时存在「归属于母公司的净利润」与「归母净利润」）且数值不同时，解析器 SHALL（按优先级）——① 命中官方科目全名列则视为唯一权威列；② 仅命中短名/别名时，若存在对应官方全名列 SHALL 改用全名列；③ 无法消歧时 SHALL 将该 claim 判为 `blocked`（解析歧义）并进四桶拆报的 blocked 桶，SHALL NOT 静默取任一列的值参与 value_mismatch 比较。

依据：2026-09-18 A4 自然腿 owner 终裁（601318，报表 1347.78 亿为官方全名列真值、校验器取短名列 235.23 亿产生假 FAIL）。

#### Scenario: 官方全名优先

- **WHEN** field_ref 为 `income_statement.20251231.归母净利润`，且该表同时存在「归属于母公司的净利润」（官方全名）与「归母净利润」（短名）两列且数值不同
- **THEN** 校验器 SHALL 取官方全名列的值作为 ground truth，短名 field_ref SHALL 映射到全名列

#### Scenario: 不可消歧判 blocked

- **WHEN** 存在多个近义列且无官方全名列可判定权威来源
- **THEN** 该 claim SHALL 判 `blocked` 进解析桶，SHALL NOT 产生 value_mismatch FAIL

### Requirement: claim 值槽语义类型校验

数值型 claim 的 `stated_value` SHALL 与 `field_ref` 所指字段的**语义类型**一致（水平值 level 不得填变化量 delta，反之亦然）。当 claim 的 interpretation 表明其数值是区间变化量（环比/同比变化、差值），而 field_ref 指向水平值字段（或相反）时，校验器 SHALL 将该 claim 判为**契约错误**进解析桶（`claim_contract_error`），SHALL NOT 计入 value_mismatch（分析师幻觉口径），且 SHALL NOT 触发修复回路（正文往往正确，修的应是 claim 契约）。

依据：2026-09-18 A4 自然腿 owner 终裁（600276，claim 把 PMI 环比变化 0.6 填进 PMI 水平值槽 49.8，正文算术正确）。

#### Scenario: 变化量入水平槽判契约错误

- **WHEN** claim 的 field_ref 为 `macro_indicators.pmi.0.制造业-指数`（水平值 49.8），stated_value 为 0.6 且 interpretation 为「环比回升 0.6 个百分点」
- **THEN** 校验器 SHALL 判 `claim_contract_error` 进解析桶，SHALL NOT 判 value_mismatch

#### Scenario: 契约错误不触发修复

- **WHEN** 某 claim 判为 `claim_contract_error`
- **THEN** 该 claim SHALL NOT 进入单点修复或全量重试路径，SHALL 在校验报告中单独呈现供契约修复

