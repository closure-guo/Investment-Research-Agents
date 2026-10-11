# Design: add-anchor-value-grounding

## 上下文

`debate_anchors.py` 现状（add-debate-argument-anchors，已归档）：`data` 型锚点仅 field_ref 解析；`event` 型仅回声；`inference` 型先 field_ref 后回声。fail-open，只落 `debate_anchor_checks` + span metadata，无消费面。

光大案例复盘：锚 `fundamental.中报净利润同比` 是 field 形态路径，state 中确定性数据无此值（快照同比暂缺）。归一探针实证：舆情标题与锚双向子串不命中 → 锚判 `unresolved` 已如实落盘，但 fail-open 且零消费面（mode C，主缺口）。相邻变体（mode A）：回声子串恰命中时判 `resolved`，「声明 field_ref、证据来自文本」的错位零记录。互补缺口（mode B）：`data` 型锚 field 命中但论点文本数字与命中值不可对上。

## 决策

### D1 只增可观测性信号，不做门禁（与 incident 026 纪律一致）

修改方案曾考虑：inference 禁回声回退 / value_mismatch 触发打回。均属路由变更——当前无观测数据支撑门禁阈值（误报率未知），先落确定性信号观测若干轮，再评估是否门禁（先归因后处置）。fail-open 语义在 spec 中显式保留。

### D2 数值溯源的作用域 = 经 field_ref 命中的锚点（data 与 inference 皆含）

- kind 只影响「回声回退资格」，不影响「field 命中后的数值可溯源义务」。
- event 型与回声命中的锚点不在范围：回声命中 = 归一子串命中，数值必然在源文本中（溯源由构造保证）。
- 纯 inference 推理文本（无 field 锚）不查：数字可来自推理链。

### D3 宽容匹配哲学：程序只判数字可溯源，方向/支持性归 judge

模块 docstring 契约「忠实性 = 可追溯性（程序）+ 支持性（judge）」。故候选值含绝对值形态：文本「下滑 24.01%」+ 解析值 -24.01 算可溯源（方向对错是 judge 的事）。候选集 = {v, v×100, round(v,2), round(v×100,2)} 及各自绝对值；相对容差 0.5%（浮点 ×100 链路噪声 + 中文四舍五入口径）。

### D4 数值 token 提取须排除标识符形态（复用 price-validator #259 M2 教训）

误报实证推演：论点「MA5 上穿 MA20」锚定 MA 值 2.0——`5`/`20` 是标识符组成不是数值断言。规则：

- 排除 ASCII 字母/数字相邻的 token：左邻 `[A-Za-z0-9]`（MA5、R1）、右邻 `[A-Za-z]`（2024Q1）。CJK 相邻不排除（「上涨5%」「10亿」是数值断言）——Python re `\w` 含 CJK，须用显式 ASCII 类。
- 排除孤立年份形态 `^(19|20)\d{2}$`（后随非量纲符时）：「2024 年报显示盈利改善」无数值断言，唯 year token 会造成假 value_mismatch；后随 `%`/`亿`/`万`/`元`/`倍` 时不排除（「2024亿」是数值）。
- 多 token 语义：任一 token 与任一候选值匹配即算可溯源（论点常聚合多值）。

### D5 field 形态判定：含 `.` 且首段 ∈ state 根键

无独立词表（`_resolve_field_ref` 是通用路径解析器，无词表成员测试可复用）。「首段是 state 实际根键」是确定性且零维护负担的形态判定：`fundamental.中报净利润同比` → state 有 `fundamental` ✓；事件标题「拟回购不超过 10 亿元」无点 ✗。仅在检查时算（state 在手），结果以 `echo_only_field_refs` 列表随记录落盘，`anchor_stats` 只汇总记录、不重算。

光大案例的事实修正（实证 `_norm_text` 归一探针）：完整舆情标题「中报净利润同比 -24.01%，下滑扩大」与锚 `fundamental.中报净利润同比` **双向子串均不命中**——该案例实为「field_ref 解析 None → unresolved 已如实记录，但 fail-open 且零消费面」形态（mode C），其告警面落在断点 3 delta。`field_ref_echo_only` 针对的是相邻变体（mode A）：归一子串恰好命中的场景（如短标题为锚点路径尾段子串），此时「声明指向结构化字段、实际证据来自文本」的错位此前完全不可见。

### D6 消费面即时立项

Produces 接口必须有消费任务（track-record-display-clarity 教训）：断点 3 delta `add-fm-grounding-surface` 同批立项，消费 `debate_anchor_checks` 新字段进 FM 审批上下文。

## 风险

- 误报（value_mismatch 错判）：D4 规则集覆盖标识符/年份两大类；容差 0.5% 覆盖舍入。残余误报由 fail-open 兜底（只进告警面，不影响管线）。
- 记录体积：每记录 +2 字段（matched_via 数组、echo_only_field_refs 小列表），append reducer 通道体积影响可忽略。
