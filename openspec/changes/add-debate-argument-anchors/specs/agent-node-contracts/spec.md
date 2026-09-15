## ADDED Requirements

### Requirement: 辩论论点结构化锚点

`DebateMessage.key_arguments` SHALL 为结构化论点列表，每项 `DebateArgument` 含 `text`（论点标头原文，非空）、`kind ∈ {data, event, inference, unspecified}` 与 `anchors: list[str]`。`kind` 对 LLM 只暴露 `data` / `event` / `inference` 三值：`data` 型的 `anchors` SHALL 为 state 英文键路径（field_ref，与分析师 claim 同一词表）；`event` 型的 `anchors` SHALL 为来源事件标题要点；`inference` 型 `anchors` 可为空。旧格式裸字符串或 `kind` 缺失/非法的论点 SHALL 解析为 `kind="unspecified"`（显式降级，SHALL NOT 推断 kind、SHALL NOT 视为解析失败）。

辩论与风控辩论节点 SHALL 在解析出 `DebateMessage` 后执行**确定性锚点校验**（零 LLM 调用）：`data` 型锚点复用引用校验器的 field_ref 解析器，解析得到非 None 值判 `resolved`，否则 `unresolved`；`event` 型锚点按既有回声源集合（`collect_text_sources`：`news_list` / `key_events` / `announcements` / `research_reports` / `share_unlock` / `block_trades`——与文本 claim 回声匹配同一实现）归一子串匹配，命中判 `resolved`；`inference` 型锚点先按 field_ref 解析、失败再按回声匹配；`data` / `event` 型零锚点判 `missing`；`inference` 型零锚点判 `none`（合法，计数）。校验结果 SHALL 写入 `AnalysisState` 中**已声明**的 channel `debate_anchor_checks`（append reducer）并落当前 span metadata。校验 SHALL fail-open：SHALL NOT 改变路由、SHALL NOT 阻断、SHALL NOT 触发重跑。

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

#### Scenario: channel 声明与图通道契约

- **WHEN** 构建 `build_5layer_graph()`
- **THEN** `builder.channels` SHALL 含 `debate_anchor_checks`，且该键 SHALL 出现在图通道契约测试的断言集合中（未声明键被图合并静默丢弃——incident 027）
- **AND** 一次 deep 全流程后 state 中 `debate_anchor_checks` 条目数 SHALL 等于全部辩手发言的论点总数

#### Scenario: rebuttal_to 编号语义不变

- **WHEN** 第 2 轮辩手输出 `rebuttal_to=[1, 3]`
- **THEN** SHALL 指向对方第 1 轮 `key_arguments` 的第 1、3 项（结构化后仍按位置计数）
- **AND** 对手可见历史「R1 论点: ①…②…③…」SHALL 只含各项 `text`，交锋覆盖率计算 SHALL 与变更前一致
