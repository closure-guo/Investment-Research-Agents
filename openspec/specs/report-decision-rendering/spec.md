# report-decision-rendering Specification

## Purpose
TBD - created by archiving change report-render-operational-params. Update Purpose after archive.
## Requirements
### Requirement: 交易决策节渲染操作参数

报告的「交易决策」节 SHALL 渲染 `TradeDecision` 的操作参数：方向、置信度、仓位档位为必含字段；`reasoning` 为必含字段。渲染 SHALL 与决策落库（decision-outcome 日批结算）消费的同一 JSON 对象同源，MUST NOT 只渲染自由文本理由而丢弃结构化参数。`reeval_triggers` 为全部 action 的必渲染对象：watch/hold 另渲染 `inaction_reason`；字段在场时 SHALL 结构化渲染，字段缺失时 SHALL 如实标注「未申报」，MUST NOT 编造条目。

sell 决策 SHALL 按 `sell_type` 分模板渲染（update-sell-action-typing，issue #188：减仓与做空是不同执行结构，共用建仓模板对持有者语义不通）：

- `sell_type="exit"`（持有者减仓）：渲染「减仓节奏」行（`exit_schedule` 原文；缺失如实「未申报」），MUST NOT 渲染入场/止损/目标价行（exit 无新建仓参数，与 watch/hold 的无建仓参数语义同型）；重新介入条件由 `reeval_triggers` 承载（既有渲染不变）；
- `sell_type="short"`（做空建仓）：按 buy/sell 既有参数行渲染（entry/stop/target + 派生指标）；
- `sell_type` 为 None（历史/未申报形态）：按 short 模板渲染（保守默认，与价位必填校验的默认推导一致）。

(Previously: sell 与 buy 共用一套参数行模板，无 sell_type 概念。)

#### Scenario: buy/sell 决策渲染完整操作参数

- **WHEN** `final_trade_decision` 的 action 为 buy，或 action 为 sell 且 `sell_type="short"`，且 `entry_price`/`stop_loss`/`target_price`/`position_size` 均为有效值
- **THEN** 报告「交易决策」节 SHALL 依次渲染方向、置信度、仓位档位、入场价、止损价、目标价、再评估触发条件、理由
- **THEN** 价位数值 SHALL 与决策 JSON 中的原始值一致（不做四舍五入以外的加工）

#### Scenario: exit（减仓）渲染减仓节奏而非建仓参数

- **WHEN** `final_trade_decision` 的 action 为 sell 且 `sell_type="exit"`，携带 `exit_schedule="分两批：现价减半、跌破600清仓"`
- **THEN** 报告 SHALL 渲染方向（卖出·减仓类标识）、置信度、仓位档位、「减仓节奏: 分两批：现价减半、跌破600清仓」、再评估触发条件（重新介入条件）、理由
- **THEN** 报告 MUST NOT 渲染入场价/止损价/目标价行（exit 语义无新建仓参数）
- **WHEN** `exit_schedule` 缺失
- **THEN** 「减仓节奏」行 SHALL 如实渲染「未申报」，MUST NOT 编造

#### Scenario: sell 未申报 sell_type 按默认模板渲染

- **WHEN** action 为 sell 且 `sell_type` 为 None（历史决策对象或申报缺失的默认形态）
- **THEN** 报告 SHALL 按 short 模板渲染既有参数行（行为与现状一致，历史报告兼容）

#### Scenario: buy/sell 决策再评估触发条件缺失标注

- **WHEN** `final_trade_decision` 的 action 为 buy 或 sell，且清洗后 `reeval_triggers` 为空（含「已打回仍未申报」终检形态）
- **THEN** 报告 SHALL 渲染「再评估触发条件: 未申报」行，MUST NOT 整行省略，MUST NOT 编造条目

#### Scenario: watch/hold 决策渲染结构化不行动理由与再评估条件

- **WHEN** `final_trade_decision` 的 action 为 watch 或 hold
- **THEN** 报告 SHALL 渲染方向、置信度、仓位档位、理由，MUST NOT 渲染入场/止损/目标价行（watch/hold 语义上无建仓参数）
- **THEN** 决策对象携带 `inaction_reason` 时，报告 SHALL 渲染「不行动原因」行，内容与决策 JSON 原始值一致
- **THEN** 决策对象携带非空 `reeval_triggers` 时，报告 SHALL 渲染「再评估触发条件」编号条目，条目内容与决策 JSON 原始值逐条一致
- **THEN** 字段缺失时报告 SHALL 渲染「未申报」如实标注，MUST NOT 编造结构化条目

#### Scenario: watch/hold 旧决策对象兼容渲染

- **WHEN** `final_trade_decision` 为 watch 或 hold 且为历史形态（无 `inaction_reason`/`reeval_triggers` 字段）
- **THEN** 报告 SHALL 正常渲染方向、置信度、仓位档位、理由，不行动原因与再评估触发条件行 SHALL 标注「未申报」
- **AND** 渲染 MUST NOT 因字段缺失抛异常

### Requirement: 参数缺失时诚实标注

操作参数为 0、null、缺失，或为不在档位词表（`light`/`moderate`/`heavy`）内的非法字面量（如 `"none"`、`"null"`、空字符串）时，报告 SHALL 如实标注「未提供」，MUST NOT 静默跳过该行，MUST NOT 以占位数据（如 0）或 LLM 原始字面量冒充有效值。档位词表校验 SHALL 大小写不敏感，非法字面量在渲染前归一为缺失，归一 MUST NOT 回写修改决策对象本身（落库与 trace 保留原值以可观测）。

(Previously: 操作参数为 0、null 或缺失时，报告 SHALL 如实标注「未提供」，MUST NOT 静默跳过该行，MUST NOT 以占位数据（如 0）冒充有效价位。非法字面量无处理规则，`"none"` 等会原样透传到报告。)

#### Scenario: 价位缺失标注

- **WHEN** action 为 sell 但 `stop_loss` 与 `target_price` 均为 0（风险辩论中保守方定性为「风控半成品」的形态）
- **THEN** 报告 SHALL 在止损/目标价行渲染「未提供」
- **THEN** 报告 MUST NOT 因此省略仓位档位或其他有效参数的渲染

#### Scenario: 非法仓位档位字面量归一渲染

- **WHEN** watch 决策的 `position_size` 为字符串 `"none"`（不在 light/moderate/heavy 词表内）
- **THEN** 报告「仓位」行 SHALL 渲染「未提供」，MUST NOT 渲染字面量 `none`
- **THEN** 决策对象落库与 trace 中的原值 SHALL 保留 `"none"`（归一只作用于渲染，MUST NOT 静默改写上游产物）

#### Scenario: 合法档位不受影响

- **WHEN** buy 决策的 `position_size` 为 `light`/`moderate`/`heavy`（含大小写变体如 `Light`）
- **THEN** 报告 SHALL 按原值渲染档位，MUST NOT 归一为「未提供」

### Requirement: 价位修正标注保留

当决策对象携带 `price_level_corrected` 标记时，报告 SHALL 保留既有「价位修正」行（toolize-price-levels 的可观测语义），MUST NOT 因新增参数渲染而丢失该标注。

#### Scenario: 修正过的价位渲染

- **WHEN** 决策对象 `price_level_corrected` 为真且 `price_level_correction_reason` 非空
- **THEN** 报告「交易决策」节 SHALL 在参数行之后渲染价位修正说明（含修正原因）

### Requirement: 派生指标由代码确定性计算

buy/sell 决策的派生指标（赔率 = (target−entry)/(entry−stop)、止损距离百分比 = (entry−stop)/entry）SHALL 由渲染代码按参数原值计算，MUST NOT 取用决策 `reasoning` 文本中 LLM 自行心算的同类数值；LLM 计算值与代码计算值不一致时，报告 SHALL 以代码计算值为准。

#### Scenario: 赔率渲染以代码计算为准

- **WHEN** buy 决策 entry=52.0、stop=47.8、target=60.0，且 reasoning 文本中出现「风险收益比约1.9:1」
- **THEN** 报告 SHALL 渲染代码计算的赔率值（8/4.2 ≈ 1.90）与止损距离（8.1%），数值来源为参数原值的确定性运算
- **THEN** 即使 reasoning 中自算赔率有误，报告 MUST NOT 采用 reasoning 文本中的数值

### Requirement: 数据新鲜度披露

报告头部 SHALL 包含行情数据截止声明行，标注 `state["kline"]` 最后交易日日期（确定性代码拼装，SHALL NOT 依赖 LLM 生成）。kline 缺失或为空时 SHALL 省略该行，SHALL NOT 伪造或回退到报告生成日期。

#### Scenario: 正常路径携带截止声明

- **GIVEN** state 含 kline，最后交易日为 2026-09-30
- **WHEN** 生成报告
- **THEN** 头部出现「行情数据截止: 2026-09-30」声明行

#### Scenario: kline 缺失时省略而非伪造

- **GIVEN** state 无 kline 或 kline 为空
- **WHEN** 生成报告
- **THEN** 头部不出现行情截止声明行（不回退为报告生成日期）

### Requirement: 报告头增量摘要

同一 `stock_code` 存在上一份已完成报告时，报告 SHALL 在「研究聚焦」之后渲染「距上次报告（YYYY-MM-DD）」增量摘要节，逐项对比以下维度：决策方向、置信度、双向触发位（或入场/止损/目标价）、现价、PE。全部对比 SHALL 基于结构化持久化数据（上一会话的 `final_trade_decision` 与 `chart_data` 关键指标），MUST NOT 解析上一报告的 markdown 文本提取数字。

渲染纪律：维度数值变化时渲染「旧值 → 新值」；无变化时如实渲染「无变化」；某维度数据缺失（历史会话无该字段）时该维度渲染「未申报」，MUST NOT 编造或从文本回填；全部维度均不可得时整节不渲染。当前标的首份报告（回溯查询为 None）时整节不渲染。

#### Scenario: 有历史报告渲染增量摘要

- **GIVEN** 601066 上一份完成报告（09-30）：watch、置信度 0.55、`trigger_low=22.91`、`trigger_high=24.6`、现价 23.03
- **WHEN** 渲染 10-08 新报告（watch、置信度 0.60、触发位不变、现价 23.50）
- **THEN** 报告头渲染「距上次报告（2026-09-30）」节：方向「watch → watch（无变化）」、置信度「0.55 → 0.60」、触发位「无变化」、现价「23.03 → 23.50」

#### Scenario: 首份报告不渲染

- **WHEN** 该 `stock_code` 无任何历史 completed 报告
- **THEN** MUST NOT 渲染「距上次报告」节（不留空节标题）

#### Scenario: 维度缺失如实标注

- **WHEN** 上一会话未持久化 `final_trade_decision`（历史行），但 `chart_data` 关键指标可得
- **THEN** 决策方向/置信度/触发位维度渲染「未申报」，现价/PE 维度正常对比，MUST NOT 解析上一报告 markdown 回填

### Requirement: 多空辩论结论分歧卡

「多空辩论结论」节 SHALL 在结论文本之前渲染一行结构化分歧卡：评级、置信度（取自研究经理结构化输出的评级字段）与结论首句（结论文本第一个完整句）。结构化评级字段缺失时该节退化为纯文本渲染（现状），MUST NOT 编造评级。

#### Scenario: 分歧卡渲染

- **WHEN** 研究经理结构化结论为「中性，置信度 0.55」，结论文本首句为「多空双方分歧聚焦于'强劲基本面与弱势技术面'的背离如何解读」
- **THEN** 「多空辩论结论」节头部渲染分歧卡：「评级: 中性 · 置信度 0.55 · 分歧焦点: 多空双方分歧聚焦于……」

#### Scenario: 结构化评级缺失降级

- **WHEN** 研究经理输出无结构化评级字段（历史会话或解析降级）
- **THEN** 该节按现状纯文本渲染，MUST NOT 渲染空卡片或编造评级

### Requirement: 风险指标与价位参考口径披露

报告口径披露节（`_format_freshness_section` 确定性渲染块）SHALL 在既有数据源之外增列风控决策链路的两个确定性计算产物，使交易决策与风控辩论节首现的风险数字/触发价位可在报告内溯源（issue #242 断点 1）：

- `state.risk_metrics`：年化波动率、最大回撤、VaR95（历史模拟法）以百分数两位小数渲染；`beta` 仅在基准数据可得（键存在）时渲染，缺失时 SHALL 省略该段（SHALL NOT 渲染「beta 暂缺」——beta 缺席是数据语义而非字段缺失）；其余单项缺失 SHALL 按既有「暂缺」语义标注。
- `state.price_levels`：`available=True` 时 SHALL 渲染入场参考价、止损带、目标带（区间 low–high）；`available=False` 时 SHALL 渲染「价位参考不可用」并附 reason（诚实标注，对齐估值缺失文案语义）。

两键全缺时 SHALL NOT 增行（零回归）；新增内容 SHALL 为披露节块内列表项（不产生新章节标题，不影响导出切章与段数）。

#### Scenario: 全字段渲染

- **GIVEN** state.risk_metrics = {volatility: 0.1568, max_drawdown: 0.1868, var_95: 0.0173, beta: 0.004}，price_levels.available=True 且含 entry_ref/stop_band_long/target_band_long
- **WHEN** 生成报告
- **THEN** 口径披露节含风险指标行（15.68%/18.68%/1.73%/0.004）与价位参考行（入场参考/止损带/目标带区间值）

#### Scenario: beta 缺失时省略该段

- **GIVEN** risk_metrics 无 beta 键（基准 K 线不可得）
- **WHEN** 生成报告
- **THEN** 风险指标行含波动率/最大回撤/VaR95 三项，且无 beta 字样

#### Scenario: 价位不可用诚实标注

- **GIVEN** price_levels = {available: false, reason: "insufficient_kline"}
- **WHEN** 生成报告
- **THEN** 披露节含「价位参考不可用」标注行并附原因

#### Scenario: 两键全缺零回归

- **GIVEN** state 无 risk_metrics 且无 price_levels
- **WHEN** 生成报告
- **THEN** 披露节不增行；若三旧数据源也全缺则披露节整体不出现

