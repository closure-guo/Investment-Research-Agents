# Delta for analyst-data-sources

## ADDED Requirements

### Requirement: 勾稽告警注入分析师上下文

`validate_financials` 产出的 `validation_warnings`（勾稽校验告警列表）非空时，系统 SHALL 将其注入基本面分析师与宏观分析师的 LLM context：以独立机生材料段呈现（标注「勾稽校验告警（机生，供交叉核对）」），逐条列出告警文本，MUST NOT 改写告警措辞、MUST NOT 由 LLM 复述为事实结论。告警列表为空时 SHALL 不注入（不留空段），与既有 optional 注入语义一致。注入失败 MUST NOT 中断分析师节点。

#### Scenario: 有告警时注入基本面与宏观 context

- GIVEN 三大报表勾稽校验产出非空 `validation_warnings`（如「利润表存在较大营业外收支」）
- WHEN 基本面分析师与宏观分析师构建 context
- THEN 两者的 context SHALL 各含「勾稽校验告警（机生，供交叉核对）」段与该告警原文
- AND 告警以机生材料呈现，SHALL NOT 被计为分析师引用 claim

#### Scenario: 无告警不注入

- GIVEN `validation_warnings` 为空列表
- WHEN 分析师构建 context
- THEN context SHALL NOT 含勾稽告警段（不留空节）

#### Scenario: 校验跳过时如实注入跳过告警

- GIVEN 三大报表缺失，勾稽校验跳过
- WHEN 分析师构建 context
- THEN context SHALL 注入「勾稽校验跳过：三大报表数据缺失」原文（该跳过提示本身即一条告警）

### Requirement: 行业成分股抓取

系统 SHALL 实现行业板块成分股抓取（`AKShareClient.fetch_industry_constituents(industry_name)`）：主源东方财富行业板块成分接口，返回成分股列表（代码、名称、总市值），按总市值降序排列。抓取 SHALL 走 `_call_ak` 既有超时/重试/不可达 fail-fast 包装；结果 SHALL 经共享缓存（行业维度键，TTL 24h）以避免同一行业重复抓取。接口失败或返回空 SHALL 返回 None（optional 降级语义），失败结果 SHALL NOT 写缓存。行业名与东财板块名不一致（如后缀差异）导致查询失败时，按接口失败降级，MUST NOT 用其他行业的成分凑数。

#### Scenario: 成分股按总市值降序返回

- GIVEN 东财行业板块接口对「白酒」返回含总市值的成分表
- WHEN fetch_industry_constituents 执行
- THEN 返回 SHALL 为按总市值降序的成分股列表（code/name/total_mv）
- AND 结果 SHALL 写入共享缓存（24h 内同行业不再发起网络抓取）

#### Scenario: 接口失败返回 None 且不写缓存

- GIVEN 东财行业板块接口连接不可达
- WHEN fetch_industry_constituents 执行
- THEN SHALL 返回 None（不抛异常）
- AND 缓存 SHALL NOT 写入失败结果

## MODIFIED Requirements

### Requirement: 同业财务数据获取

系统 SHALL 实现同业财务数据抓取（`AKShareClient.fetch_peer_data(stock_codes)`）：对用户指定的对标股代码列表逐标的获取两个字段组——估值组（名称、PE、PB、总市值）与财务组（营收同比、归母净利同比、毛利率、报告期）。估值组抓取 SHALL 复用 `fetch_stock_quote` 的主源/回退链（链定义见 data-source-resilience「行情 quote 三级回退（腾讯单标的 / 东财 spot / 百度估值+腾讯日线）」）以继承其单位归一与降级语义；财务组 SHALL 复用 `fetch_latest_period_snapshot`（与主标的最新报告期快照同函数同口径），其失败 SHALL NOT 影响估值组产出（字段组级降级：财务组全失败时该行仅含估值组字段，财务字段以缺失标记占位）。单标的抓取失败 SHALL 跳过该标的且不拖垮整批；全部标的失败或输入为空 SHALL 返回 None（fetch 层既有 optional 降级语义）。返回值 SHALL 为 DataFrame，列名契约：name/code/PE/PB/total_mv/revenue_yoy/netprofit_yoy/gross_margin/report_period；不可得字段 SHALL 以缺失标记占位（None），MUST NOT 删除列或虚构数值。市场平均 PE（`state.industry_pe`）与同业个股 PE 是不同口径的数据源，两者 SHALL 并存可辨。

请求未携带显式对标股（`peer_codes` 为空）时，系统 SHALL 尝试自动选取同业对标股（complete-peer-pipeline）：以 `industry_info.industry` 调 `fetch_industry_constituents` 取行业成分，按总市值取前 5 且排除主标的自身作为抓取输入。行业名缺失、成分抓取失败/为空、剔除自身后无剩余标的——任一命中即保持「无对标股」语义（不触发同业抓取调用，`peer_financials` 保持 None），MUST NOT 以其他行业标的凑数。显式 `peer_codes` 恒优先：请求指定列表时 SHALL NOT 追加自动选取标的。自动选取输入进入的同业抓取，其字段组、降级、格式化注入与报告呈现行为与显式指定路径完全一致。

(Previously: 仅在请求携带 peer_codes 时抓取，未指定对标股时不抓取。——补自动选取语义：无显式对标股时行业成分市值 Top5 排除自身作为输入；降级纪律与显式恒优先一并成文。)

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
- WHEN 同业抓取执行
- THEN `peer_financials` SHALL 为 None（与既有 optional 降级同语义）
- AND 管线 SHALL 继续执行，相对估值段以缺失声明呈现

#### Scenario: 未指定对标股时不抓取

- GIVEN 请求未携带 peer_codes 且自动选取不可得（行业名缺失 / 行业成分抓取失败或为空 / 剔除自身后无剩余标的）
- WHEN fetch_data 执行
- THEN SHALL NOT 触发同业抓取调用
- AND `peer_financials` 保持 None

#### Scenario: 未指定对标股时自动选取行业市值 Top5

- GIVEN 请求未携带 peer_codes，主标的行业为「白酒」，行业成分含至少 6 只且自身在列
- WHEN fetch_data 执行
- THEN 同业抓取输入 SHALL 为「白酒」成分按总市值前 5 且不含自身
- AND `peer_financials` 消费（相对估值/基本面 context/报告段）与显式指定路径一致

#### Scenario: 显式对标股恒优先

- GIVEN 请求携带 peer_codes="000858"，主标的存在可得的行业成分
- WHEN fetch_data 执行
- THEN 同业抓取 SHALL 仅使用显式列表，SHALL NOT 追加自动选取标的
