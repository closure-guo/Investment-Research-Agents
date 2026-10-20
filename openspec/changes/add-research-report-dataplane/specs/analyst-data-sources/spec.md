# Delta for analyst-data-sources

## MODIFIED Requirements

### Requirement: 公告与研报数据获取

系统 SHALL 在深度分析数据准备阶段并行获取个股公告列表（巨潮 cninfo，近 180 天，含标题/类别/日期）与券商研报列表（东财，含标题/机构/评级/发布日期/近一月个股研报数，以及接口提供的盈利预测 EPS（可得时）），失败或空结果 SHALL 降级为空列表且不阻断管线。研报字段提取 SHALL 对齐接口真实列名（机构名取「机构」列、发布日期取「日期」列）；东财该接口不提供目标价，提取器 MUST NOT 保留恒空的目标价占位字段。研报取数 SHALL 配契约测试：fixture 取自真实接口响应快照，断言列名与字段映射，接口列名漂移致字段静默为空时测试 MUST 红。

(Previously: 系统 SHALL 在深度分析数据准备阶段并行获取个股公告列表（巨潮 cninfo，近 180 天，含标题/类别/日期）与券商研报列表（东财，含标题/机构/评级/目标价/日期），失败或空结果 SHALL 降级为空列表且不阻断管线。——机构/日期列名对齐真实接口、目标价占位移除、新增盈利预测列与近一月研报数、新增契约测试要求；降级语义不变。)

#### Scenario: 公告与研报正常装配

- GIVEN 深度分析管线进入数据准备阶段
- WHEN 巨潮与东财接口可用
- THEN state SHALL 包含 announcements 与 research_reports 键
- AND 研报行 SHALL 含非空机构与发布日期（接口返回该数据时）
- AND 数据 SHALL 写入缓存（TTL 3600s）

#### Scenario: 接口失败降级

- GIVEN 公告或研报接口调用失败
- WHEN 数据准备完成
- THEN 对应 state 键 SHALL 为空列表
- AND 管线 SHALL 继续执行不中断

#### Scenario: 列名契约测试防漂移

- GIVEN fixture 为一次真实东财研报接口响应快照
- WHEN 契约测试运行
- THEN SHALL 断言「机构」「日期」列存在且映射进机构/发布日期字段
- AND 接口列名变更（映射落空）时测试 SHALL 失败

### Requirement: 分析师消费新信源

基本面分析师的上下文 SHALL 包含公告列表（标题/类别/日期）、研报列表（标题/机构/评级/发布日期）与一致预期段；一致预期段 SHALL 含覆盖机构数（或研报数）、近 6 个月评级分布（买入~卖出计数）与一致预期 EPS（可得时），优先读取研报数据面当日快照，快照缺失时 SHALL 按需单次直拉全市场盈利预测过滤本标的，仍不可得时 SHALL 显式标注「一致预期数据缺失」。无分析师覆盖的标的 SHALL 显式标注「无分析师覆盖」，MUST NOT 以零值或缺失占位伪装成有覆盖的数字。prompt SHALL 附防锚定条款（研报评级与一致预期是卖方观点/市场预期参照，不得直接作为结论依据）；舆情分析师的上下文 SHALL 包含解禁与大宗事件面。

(Previously: 基本面分析师的上下文 SHALL 包含公告列表（标题/类别/日期）与研报列表（标题/机构/评级/目标价/日期），prompt SHALL 附防锚定条款（研报评级/目标价是卖方观点，不得直接作为结论依据）；舆情分析师的上下文 SHALL 包含解禁与大宗事件面。——研报段字段面对齐（目标价→盈利预测/一致预期）、新增一致预期段与降级链、新增无覆盖显式语义；舆情侧不变。)

#### Scenario: 基本面上下文含公告与研报

- GIVEN state 含非空 announcements 与 research_reports
- WHEN 基本面分析师构建上下文
- THEN 上下文 SHALL 含公告标题与研报段落（标题/机构/评级/发布日期）
- AND 上下文 SHALL 含一致预期段（评级分布与覆盖机构数）
- AND 研报与一致预期段 SHALL 附防锚定提示

#### Scenario: 无覆盖标的显式标注

- GIVEN 标的 X 无任何分析师覆盖（全市场盈利预测表中无该标的）
- WHEN 基本面分析师构建上下文
- THEN 一致预期段 SHALL 标注「无分析师覆盖」
- AND MUST NOT 出现零值评级分布或 0 机构预期数值

#### Scenario: 舆情上下文含事件面

- GIVEN state 含非空 share_unlock 或 block_trades
- WHEN 舆情分析师构建上下文
- THEN 上下文 SHALL 含解禁/大宗事件段落
