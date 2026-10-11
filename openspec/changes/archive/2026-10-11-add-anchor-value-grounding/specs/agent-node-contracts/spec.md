# add-anchor-value-grounding delta — agent-node-contracts

## MODIFIED Requirements

### Requirement: 辩论论点结构化锚点

`DebateMessage.key_arguments` SHALL 为结构化论点列表，每项 `DebateArgument` 含 `text`（论点标头原文，非空）、`kind ∈ {data, event, inference, unspecified}` 与 `anchors: list[str]`。`kind` 对 LLM 只暴露 `data` / `event` / `inference` 三值：`data` 型的 `anchors` SHALL 为 state 英文键路径（field_ref，与分析师 claim 同一词表）；`event` 型的 `anchors` SHALL 为来源事件标题要点；`inference` 型 `anchors` 可为空。旧格式裸字符串或 `kind` 缺失/非法的论点 SHALL 解析为 `kind="unspecified"`（显式降级，SHALL NOT 推断 kind、SHALL NOT 视为解析失败）。

辩论与风控辩论节点 SHALL 在解析出 `DebateMessage` 后执行**确定性锚点校验**（零 LLM 调用）：`data` 型锚点复用引用校验器的 field_ref 解析器，解析得到非 None 值判 `resolved`，否则 `unresolved`；`event` 型锚点按既有回声源集合（`collect_text_sources`：`news_list` / `key_events` / `announcements` / `research_reports` / `share_unlock` / `block_trades`——与文本 claim 回声匹配同一实现）归一子串匹配，命中判 `resolved`；`inference` 型锚点先按 field_ref 解析、失败再按回声匹配；`data` / `event` 型零锚点判 `missing`；`inference` 型零锚点判 `none`（合法，计数）。校验结果 SHALL 写入 `AnalysisState` 中**已声明**的 channel `debate_anchor_checks`（append reducer）并落当前 span metadata。校验 SHALL fail-open：SHALL NOT 改变路由、SHALL NOT 阻断、SHALL NOT 触发重跑。

每条检查记录 SHALL 为每锚点记录解析途径 `matched_via`（与 `anchors` 平行的列表：field_ref 命中记 `field_ref`、回声命中记 `echo`、未命中记空串），并 SHALL 记录 `echo_only_field_refs`（inference 型锚点中「field 形态（含 `.` 且首段为 state 现存根键）却仅经回声命中 resolved」的锚点子集）——锚点声明形态与实际证据来源错位的确定性信号。

当论点存在任一经 field_ref 命中的锚点（`data` 或 `inference` 型）时，校验 SHALL 执行**数值溯源检查**：从论点 `text` 提取数值 token（排除标识符形态——ASCII 字母/数字相邻的 token 如 `MA5`/`R1`/`2024Q1`——与孤立年份形态），若文本含数值 token 且无一与任一命中值匹配（宽容匹配：原值 / ×100 百分数 / 两位舍入 / 绝对值，相对容差 0.5%——方向正确性归 judge，程序只判数字可溯源），该论点 `status` SHALL 判 `value_mismatch`（`anchored` 仍 true）；文本无数值 token、或任一 token 可溯源时 MUST NOT 判 `value_mismatch`。`event` 型与回声命中的锚点不在数值溯源范围（回声命中即子串命中，溯源由构造保证）。

`anchor_stats` SHALL 在既有桶之外新增 `value_mismatch`（status=value_mismatch 的论点数）与 `field_ref_echo_only`（echo_only_field_refs 并集大小）两桶。全部新增信号 SHALL fail-open：SHALL NOT 改变路由、SHALL NOT 阻断、SHALL NOT 触发重跑。

`rebuttal_to` 的 1-based 编号 SHALL 继续指向对方 `key_arguments` 的位置；对手可见的辩论历史编号行（「R{n} 论点: ①…」）SHALL 只渲染 `text`，SHALL NOT 在本变更中向对手暴露锚点。

#### Scenario: 结构化论点解析

- **WHEN** 辩手 LLM 输出 `key_arguments: [{"text": "MA5 上穿 MA20", "kind": "data", "anchors": ["technical_indicators.MA.5.-1", "technical_indicators.MA.20.-1"]}]`
- **THEN** `DebateMessage.key_arguments[0]` SHALL 为 `DebateArgument(text="MA5 上穿 MA20", kind="data", anchors=[...两条...])`
- **AND** `text` 为空 SHALL 触发验证异常（与既有非空结论字段口径一致）

#### Scenario: 旧格式显式降级

- **WHEN** `key_arguments` 为裸字符串列表 `["论点1", "论点2"]`（历史会话数据、TESTING stub、Langfuse 反解材料）或某项 `kind` 缺失 / 不在合法集
- **THEN** 该项 SHALL 解析为 `kind="unspecified"`、`anchors=[]`，SHALL NOT 抛解析异常，SHALL NOT 推断为 `inference`
- **AND** 该项 SHALL 计入锚点校验的 `unspecified` 计数

#### Scenario: data 型锚点解析校验

- **GIVEN** state 含 `technical_indicators.MA.5` 序列
- **WHEN** 论点 `kind="data"`，`anchors=["technical_indicators.MA.5.-1", "profitability_metrics.不存在的键.2024"]`
- **THEN** 第一个锚点 SHALL 判 `resolved`、第二个 SHALL 判 `unresolved`，该论点 `anchored=true`（任一锚点 resolved 即锚定）
- **AND** 解析语义（负索引 / 括号索引 / DataFrame 行键.列名 / 日期与季度形态归一）SHALL 与引用校验器完全一致，SHALL NOT 另建解析器

#### Scenario: event 型回声匹配

- **GIVEN** `news_list` 含标题「公司公告拟回购不超过 10 亿元」
- **WHEN** 论点 `kind="event"`，`anchors=["拟回购不超过 10 亿元"]`
- **THEN** 该锚点 SHALL 判 `resolved`（归一子串命中）
- **AND** 回声源集合 SHALL 与文本 claim 回声匹配复用同一集合与归一函数

#### Scenario: 申报纪律违规与推断无锚分别计数

- **WHEN** `data` 或 `event` 型论点 `anchors=[]`
- **THEN** SHALL 判 `missing` 并计入 `missing_required`
- **WHEN** `inference` 型论点 `anchors=[]`
- **THEN** SHALL 判 `none`、`anchored=false`，计入 `unanchored_inference`，SHALL NOT 视为违规

#### Scenario: fail-open 不阻断

- **WHEN** 某轮辩论全部论点 `anchored=false`（含全部 unresolved 或 missing）
- **THEN** 图路由 SHALL 与变更前完全一致（进入下一轮 / research_manager / trader），SHALL NOT 重跑辩手、SHALL NOT 置阻断标记
- **AND** 校验结果 SHALL 仍完整写入 `debate_anchor_checks` 与 span metadata

#### Scenario: matched_via 解析途径记录

- **WHEN** 论点 `kind="inference"`，`anchors=["technical_indicators.MA.5.-1", "拟回购不超过 10 亿元", "fundamental.不存在键.2024"]`，state 含 MA 序列与回购新闻
- **THEN** 检查记录 `matched_via` SHALL 为 `["field_ref", "echo", ""]`（与 anchors 平行）
- **WHEN** `kind="data"` 锚点命中
- **THEN** `matched_via` SHALL 记 `field_ref`；`kind="event"` 锚点命中 SHALL 记 `echo`

#### Scenario: 数值可溯源不误报

- **GIVEN** state 中 `fundamental.中报净利润同比` 解析为 `-0.2401`，论点 text「中报净利同比下滑 24.01%，趋势延续」
- **WHEN** 论点 `kind="data"`，`anchors=["fundamental.中报净利润同比"]`
- **THEN** status SHALL 为 `resolved`（绝对值形态 + ×100 百分数形态匹配），MUST NOT 判 `value_mismatch`

#### Scenario: 数值不可溯源判 value_mismatch

- **GIVEN** state 中 `fundamental.中报净利润同比` 解析为 `-0.2401`，论点 text「净利同比下滑 8.06%，盈利恶化」（8.06 为季度链单季值，与锚定值不可对上）
- **WHEN** 论点 `kind="data"`，`anchors=["fundamental.中报净利润同比"]`
- **THEN** status SHALL 为 `value_mismatch`、`anchored` SHALL 仍为 true
- **AND** `anchor_stats` 的 `value_mismatch` 桶 SHALL 计 1

#### Scenario: 标识符与年份形态不触发数值溯源

- **WHEN** 论点 text 为「MA5 上穿 MA20，R1 回应 2024 年报显示盈利改善」且存在 field 命中锚点（值 2.0）
- **THEN** 数值 token 提取 SHALL 排除 `5`（左邻字母）、`20`（左邻字母）、`1`（左邻字母）、`2024`（孤立年份），status MUST NOT 判 `value_mismatch`
- **WHEN** 文本无任何数值 token
- **THEN** MUST NOT 判 `value_mismatch`

#### Scenario: field 形态锚点仅回声命中计 field_ref_echo_only

- **GIVEN** state 根键含 `fundamental`，`fundamental.中报净利润同比` 解析为 None（快照同比暂缺），`news_list` 含标题「中报净利润同比」（归一后为锚点归一形态的子串，回声命中）
- **WHEN** 论点 `kind="inference"`，`anchors=["fundamental.中报净利润同比"]`
- **THEN** 该锚点 SHALL 判 `resolved`、`matched_via` SHALL 记 `echo`
- **AND** 检查记录 `echo_only_field_refs` SHALL 收录该锚点，`anchor_stats` 的 `field_ref_echo_only` 桶 SHALL 计 1
- **AND** event 型回声锚（不含 `.`）与 field_ref 命中的锚点 SHALL NOT 计入该桶

#### Scenario: 新增信号均不改变路由

- **WHEN** 某轮辩论出现 `value_mismatch` 或 `field_ref_echo_only` 非零
- **THEN** 图路由 SHALL 与信号全零时完全一致，SHALL NOT 重跑辩手、SHALL NOT 置阻断标记、SHALL NOT 改写辩论上下文
- **AND** 信号 SHALL 完整写入 `debate_anchor_checks` 与 span metadata，供下游审批上下文消费

#### Scenario: channel 声明与图通道契约

- **WHEN** 构建 `build_5layer_graph()`
- **THEN** `builder.channels` SHALL 含 `debate_anchor_checks`，且该键 SHALL 出现在图通道契约测试的断言集合中（未声明键被图合并静默丢弃——incident 027）
- **AND** 一次 deep 全流程后 state 中 `debate_anchor_checks` 条目数 SHALL 等于全部辩手发言的论点总数

#### Scenario: rebuttal_to 编号语义不变

- **WHEN** 第 2 轮辩手输出 `rebuttal_to=[1, 3]`
- **THEN** SHALL 指向对方第 1 轮 `key_arguments` 的第 1、3 项（结构化后仍按位置计数）
- **AND** 对手可见历史「R1 论点: ①…②…③…」SHALL 只含各项 `text`，交锋覆盖率计算 SHALL 与变更前一致
