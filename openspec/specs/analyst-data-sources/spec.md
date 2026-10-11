# analyst-data-sources Specification

## Purpose
TBD - created by archiving change add-analyst-data-coverage. Update Purpose after archive.
## Requirements
### Requirement: 公告与研报数据获取

系统 SHALL 在深度分析数据准备阶段并行获取个股公告列表（巨潮 cninfo，近 180 天，含标题/类别/日期）与券商研报列表（东财，含标题/机构/评级/目标价/日期），失败或空结果 SHALL 降级为空列表且不阻断管线。

#### Scenario: 公告与研报正常装配

- GIVEN 深度分析管线进入数据准备阶段
- WHEN 巨潮与东财接口可用
- THEN state SHALL 包含 announcements 与 research_reports 键
- AND 数据 SHALL 写入缓存（TTL 3600s）

#### Scenario: 接口失败降级

- GIVEN 公告或研报接口调用失败
- WHEN 数据准备完成
- THEN 对应 state 键 SHALL 为空列表
- AND 管线 SHALL 继续执行不中断

### Requirement: 解禁与大宗交易数据获取

系统 SHALL 获取个股限售解禁排队（东财，含解禁日期/数量/市值）与近 30 天大宗交易明细（东财按日期区间拉取后按个股过滤，含成交价/溢价率/买卖营业部），失败降级为空列表。

#### Scenario: 解禁与大宗装配

- GIVEN 深度分析管线进入数据准备阶段
- WHEN 东财解禁/大宗接口可用
- THEN state SHALL 包含 share_unlock 与 block_trades 键

### Requirement: 分析师消费新信源

基本面分析师的上下文 SHALL 包含公告列表（标题/类别/日期）与研报列表（标题/机构/评级/目标价/日期），prompt SHALL 附防锚定条款（研报评级/目标价是卖方观点，不得直接作为结论依据）；舆情分析师的上下文 SHALL 包含解禁与大宗事件面。

#### Scenario: 基本面上下文含公告与研报

- GIVEN state 含非空 announcements 与 research_reports
- WHEN 基本面分析师构建上下文
- THEN 上下文 SHALL 含公告标题与研报评级/目标价段落
- AND 研报段落 SHALL 附防锚定提示

#### Scenario: 舆情上下文含事件面

- GIVEN state 含非空 share_unlock 或 block_trades
- WHEN 舆情分析师构建上下文
- THEN 上下文 SHALL 含解禁/大宗事件段落

### Requirement: 新信源 claim 纳入回声匹配

citation 校验器 SHALL 将 announcements/research_reports/share_unlock/block_trades 的标题字段纳入文本 claim 回声匹配源集合（与 news_list/key_events 同语义：归一子串命中即 PASS(echo)，未命中 UNVERIFIABLE(text)）。

#### Scenario: 公告标题 claim 回声命中

- GIVEN 分析师 claim 引用 announcements 信源且 stated 内容为某公告标题
- WHEN citation 校验器执行文本回声匹配
- THEN 命中 SHALL 判 PASS(echo)

### Requirement: 最新报告期快照获取

系统 SHALL 在深度分析数据准备阶段获取「最新报告期快照」（latest_period_snapshot）：从三大报表数据源取最新已披露报告期（不限于年报，含中报/季报/三季报）的关键科目，写入 state 键 `latest_period_snapshot`。快照 SHALL 至少包含：报告日、营业总收入（累计）、归母净利润（累计）、毛利率、资产负债率、存货、合同负债，以及营收/归母净利相对上年同期的同比变化率（同期数据可得时）。快照各科目口径为该报告期累计值，SHALL 标注「累计口径」。快照抓取或字段缺失 SHALL 降级为携带缺失标注的部分快照且不阻断管线。

利润表含「信用减值损失」列且最新期与上年同期均可得时，快照 SHALL 增列「信用减值损失」（亿元）与「信用减值损失同比(%)」（列存在即纳入，不做行业门控——银行是主诉求，制造业同样受益）；列或同期缺失时该两项 SHALL 缺席（MUST NOT 渲染 None/暂缺占位进快照行）。报告口径披露节 SHALL 在快照行内追加呈现（如「信用减值损失 X 亿（同比 Y%）」），字段缺席时快照行维持现状形态（零回归）。

#### Scenario: 年报之后已有中报时快照取中报

- GIVEN 拓荆科技最新年报为 2025-12-31、最新中报为 2026-06-30（后者披露更晚）
- WHEN 深度分析数据准备阶段执行快照获取
- THEN `latest_period_snapshot.报告日` SHALL 为 2026-06-30
- AND 毛利率 SHALL 按中报累计口径计算（营业总收入 29.13 亿、营业成本 17.18 亿 → 约 41.0%）
- AND 资产负债率 SHALL 为中报期末值（约 47.9%，而非年报的 64.11%）
- AND 存货/合同负债 SHALL 为中报期末值

#### Scenario: 同期数据缺失时同比留空

- GIVEN 最新报告期为上市后首份报告，无上年同期数据
- WHEN 快照获取执行
- THEN 营收/归母净利同比 SHALL 为 None 并标注缺失
- AND 其余科目 SHALL 照常装配，管线不中断

#### Scenario: 快照数据源失败降级

- GIVEN 快照依赖的报表接口调用失败
- WHEN 数据准备阶段完成
- THEN `latest_period_snapshot` SHALL 为空 dict 并留 ERROR 日志
- AND 管线 SHALL 继续执行（与公告/研报降级同语义）

#### Scenario: 银行快照携带信用减值损失及同比

- GIVEN 银行标的利润表含「信用减值损失」列、最新期 208.79 亿、上年同期 159.02 亿
- WHEN 快照获取执行
- THEN 快照 SHALL 含 信用减值损失=208.79（亿元）与 信用减值损失同比(%)≈31.30
- AND 报告口径披露节快照行 SHALL 追加呈现，使「主动提储 vs 资产质量恶化」可溯源

#### Scenario: 信用减值损失列缺失时快照行零变化

- GIVEN 利润表无「信用减值损失」列或同期缺失
- WHEN 快照获取执行
- THEN 快照 MUST NOT 含该两项的 None 占位
- AND 报告口径披露节快照行 SHALL 维持现状形态

### Requirement: 季度利润表字段扩展

季度利润表获取（`fetch_quarterly_income`）SHALL 在现有单季归母净利润之外同时提取单季营业收入与单季营业成本；`quarterly_trend` SHALL 增加单季营收序列（含同比，去年同期可得时）与单季毛利率序列（(营收−营业成本)/营收）。字段缺失的季度 SHALL 置 None 且不产出伪值。

#### Scenario: 季度趋势含毛利率序列

- GIVEN 2026Q2 单季营收、营业成本与归母净利润均可得
- WHEN compute 节点装配 `quarterly_trend`
- THEN 该季度 SHALL 携带单季营收、单季毛利率与单季归母净利润
- AND 单季毛利率 SHALL 由 (营收−营业成本)/营收 直接运算得出

#### Scenario: 某季度成本缺失不产出伪毛利率

- GIVEN 2025Q3 单季营业成本缺失
- WHEN `quarterly_trend` 装配
- THEN 2025Q3 毛利率 SHALL 为 None
- AND MUST NOT 以 0 或邻期值填充

### Requirement: 分析师消费最新报告期快照

基本面分析师的上下文 SHALL 包含最新报告期快照数据段（state 键 `latest_period_snapshot`），段内 SHALL 标注报告日与「最新报告期」身份；prompt SHALL 指示：涉及盈利能力、负债水平、存货等存量/趋势论断时，最新报告期数据与年报序列趋势 SHALL 同时呈现，论断 SHALL 以最新报告期数据为准校验年报趋势是否已被打破，二者冲突时 MUST 显式说明（如年报序列下滑但最新中报回升）。

#### Scenario: 中报回升与年报下滑并陈

- GIVEN 年报毛利率序列 2023–2025 连续下滑（47.11%→34.95%）而快照显示 2026 中报毛利率约 41.0%（同比 +9pct）
- WHEN 基本面分析师构建上下文并生成报告
- THEN 上下文 SHALL 同时含年报序列与快照段
- AND 报告的盈利质量论断 SHALL 反映中报回升事实，MUST NOT 仅凭年报序列断言「毛利率连续下滑」

#### Scenario: 快照空缺时报告披露数据边界

- GIVEN `latest_period_snapshot` 为空 dict（数据源失败降级）
- WHEN 基本面分析师生成报告
- THEN 论断 SHALL 基于年报序列并显式标注「最新报告期快照缺失」
- AND MUST NOT 假设年报趋势仍然成立而不加限定

### Requirement: 同业财务数据获取

系统 SHALL 实现同业财务数据抓取（`AKShareClient.fetch_peer_data(stock_codes)`）：对用户指定的对标股代码列表逐标的获取两个字段组——估值组（名称、PE、PB、总市值）与财务组（营收同比、归母净利同比、毛利率、报告期）。估值组抓取 SHALL 复用 `fetch_stock_quote` 的主源/回退链（链定义见 data-source-resilience「行情 quote 三级回退（腾讯单标的 / 东财 spot / 百度估值+腾讯日线）」）以继承其单位归一与降级语义；财务组 SHALL 复用 `fetch_latest_period_snapshot`（与主标的最新报告期快照同函数同口径），其失败 SHALL NOT 影响估值组产出（字段组级降级：财务组全失败时该行仅含估值组字段，财务字段以缺失标记占位）。单标的抓取失败 SHALL 跳过该标的且不拖垮整批；全部标的失败或输入为空 SHALL 返回 None（fetch 层既有 optional 降级语义）。返回值 SHALL 为 DataFrame，列名契约：name/code/PE/PB/total_mv/revenue_yoy/netprofit_yoy/gross_margin/report_period；不可得字段 SHALL 以缺失标记占位（None），MUST NOT 删除列或虚构数值。市场平均 PE（`state.industry_pe`）与同业个股 PE 是不同口径的数据源，两者 SHALL 并存可辨。

(Previously: 逐标的获取名称、PE、PB；列名与相对估值消费契约一致（name/PE/PB）。——字段面从估值三件套扩展为估值组+财务组两级，新增总市值与财务三字段+报告期，并新增字段组级降级语义；抓取主链与单标的/整批降级语义不变。)

#### Scenario: 有对标股时返回同业 DataFrame

- GIVEN 用户请求携带 peer_codes="688012,002371" 且行情源可达
- WHEN fetch_data 执行同业抓取
- THEN `state.peer_financials` SHALL 为 DataFrame
- AND 列 SHALL 含 name/code/PE/PB/total_mv/revenue_yoy/netprofit_yoy/gross_margin/report_period，每行对应一个成功抓取的对标股
- AND 相对估值（relative_valuation）SHALL 以该数据计算

#### Scenario: 单标的失败跳过不拖垮整批

- GIVEN peer_codes 含两个代码，其一行情全源失败
- WHEN 同业抓取执行
- THEN 结果 SHALL 仅含成功标的的行，SHALL NOT 抛异常
- AND 相对估值 SHALL 基于剩余同业计算

#### Scenario: 财务字段组失败不影响估值组

- GIVEN 对标股行情源可达、财务字段接口全部失败
- WHEN 同业抓取执行
- THEN 结果行 SHALL 含估值组字段（name/PE/PB/total_mv）
- AND 财务组字段（revenue_yoy/netprofit_yoy/gross_margin/report_period）SHALL 以缺失标记占位
- AND SHALL NOT 因财务组失败丢弃整行或返回 None

#### Scenario: 全部失败降级 None

- GIVEN 全部对标股行情抓取失败
- WHEN fetch_data 完成
- THEN `peer_financials` SHALL 为 None（与既有 optional 降级同语义）
- AND 管线 SHALL 继续执行，相对估值段以缺失声明呈现

#### Scenario: 未指定对标股时不抓取

- GIVEN 请求未携带 peer_codes
- WHEN fetch_data 执行
- THEN SHALL NOT 触发同业抓取调用
- AND `peer_financials` 保持 None

