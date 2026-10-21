# research-report-dataplane Specification

## Purpose
TBD - created by archiving change add-research-report-dataplane. Update Purpose after archive.
## Requirements
### Requirement: 五类研报元数据日批采集

系统 SHALL 每交易日按发布日期窗口从东财研报接口（`reportapi.eastmoney.com`）全市场增量采集五类研报元数据：个股研报（`/report/list` qType=0，可不带 code 按日拉全市场）、行业研报（qType=1）、策略报告（`/report/jg` qType=2）、宏观研究（qType=3）、券商晨报（qType=4）。落库字段 SHALL 至少包含：报告类别、标题、机构、发布日期（published_at）、标的代码与名称（个股类）、行业名（行业类）、评级（接口提供时）、盈利预测 EPS/PE（接口提供时）、来源标识。周末/节假日窗口 0 条 SHALL 记录为合法的空窗状态，MUST NOT 伪造数据。

#### Scenario: 五类日批正常落库

- GIVEN 交易日 T 收盘后日批运行
- WHEN 东财五类接口按 [T-3, T] 窗口拉取
- THEN 研报主表 SHALL 新增五类各自的当日行
- AND 每行 SHALL 携带 published_at 与 source

#### Scenario: 节假日空窗为合法状态

- GIVEN 窗口内为非交易日且接口返回 0 行
- WHEN 日批运行
- THEN 主表 SHALL 无新增行且本次运行 SHALL 记录 ok 状态（空窗非失败）

### Requirement: 评级变动事件采集

系统 SHALL 每交易日从巨潮评级预测接口按日拉取全市场投资评级变动（含证券代码/简称/发布日期/研究机构/研究员/投资评级/是否首次评级/评级变化/前一次投资评级/目标价上下限），落评级变动表。事件唯一键 SHALL 为（标的代码, 研究机构, 发布日期, 投资评级, 前一次投资评级）。

#### Scenario: 当日评级变动落库

- GIVEN 交易日 T 巨潮返回 80 条评级
- WHEN 日批运行
- THEN 评级变动表 SHALL 新增当日事件行，含评级变化方向字段

#### Scenario: 评级事件幂等重跑

- GIVEN 交易日 T 的评级变动已落库
- WHEN 同日日批重跑
- THEN 评级变动表行数 SHALL 不变（唯一键幂等）

### Requirement: 一致预期快照与单股明细

系统 SHALL 每交易日拉取东财全市场盈利预测快照（近 6 个月研报数、买入/增持/中性/减持/卖出五档评级分布、当年起三年 EPS 预测），以「快照日期+标的」为键落盈利预测表。分析单标的时系统 SHALL 可按需拉取同花顺单股一致预期明细（预测机构数/最小值/均值/最大值与机构级预测明细含报告日期）。

#### Scenario: 全市场预期快照落库

- GIVEN 东财盈利预测接口返回约 2887 只标的数据
- WHEN 预期快照日批运行
- THEN 盈利预测表 SHALL 含当日快照行（评级分布与 EPS 预测齐备）

#### Scenario: 无覆盖标的显式区分

- GIVEN 标的 X 不在全市场盈利预测表中
- WHEN 查询 X 的一致预期
- THEN 结果 SHALL 显式为「无分析师覆盖」
- AND MUST NOT 返回全零/缺失占位伪装成有覆盖的数值

### Requirement: 分析师档案表

系统 SHALL 从研报主表与评级变动表聚合维护分析师档案（机构、分析师姓名、覆盖标的数、近 90 日报告数、评级分布），随日批更新；档案数据 SHALL 可按机构与分析师查询。

#### Scenario: 档案随日批更新

- GIVEN 研报主表含机构 O 近 90 日的报告行
- WHEN 日批聚合运行
- THEN 分析师表 SHALL 含 O 的覆盖标的数与近 90 日报告数

### Requirement: 期次键与幂等重放

研报主表唯一键 SHALL 为 hash(标题, 机构, 发布日期)。同一批次重复导入、日批重跑、按窗口补跑 SHALL 幂等（不产生重复行、以新数据覆盖同键旧行并保留更新时间）。同一篇报告经不同来源标识到达（如未来第二源行级接入）SHALL 按该键合并为单行并累计来源标识。

#### Scenario: 同批二次导入幂等

- GIVEN 批次 B 已导入主表
- WHEN 批次 B 原样再次导入
- THEN 主表行数 SHALL 不变

#### Scenario: 同键不同来源合并（store 能力）

- GIVEN 同一篇报告（标题/机构/日期相同）以两个不同来源标识各落库一次
- WHEN 两次写入完成
- THEN 该报告 SHALL 为主表单行且来源标识含两个来源

### Requirement: 第二源交叉与降级

新浪研报列表 SHALL 作为第二源用于当日各分类计数的交叉校验（新浪实测仅公司/行业/策略/宏观四类，券商晨报无独立分类；列表页按页截断，计数只作「有/无」级交叉，不作精确对账）；东财接口失败时日批 SHALL 记录失败并降级（以新浪计数入运行记录、不写主表伪行、不阻断其他 job），恢复后下一窗口按幂等键补齐。主表每行 SHALL 携带来源标识。

#### Scenario: 东财失败降级不阻断

- GIVEN 东财研报接口当日不可达
- WHEN 日批运行
- THEN 本次运行 SHALL 记录 failed/degraded 状态与新浪计数
- AND 评级变动与预期快照 job SHALL 照常执行

#### Scenario: 恢复后按窗补齐

- GIVEN T-1 日东财失败、T 日恢复
- WHEN T 日日批按 [T-4, T] 窗口运行
- THEN T-1 漏采行 SHALL 补齐且与既有行无重复

### Requirement: 覆盖状态显式化

系统 SHALL 提供按标的查询研报覆盖状态：最新研报日期、近 30 日研报条数、是否有预期覆盖。「采集失败」「无覆盖」「断更（曾有覆盖、长期无新报）」SHALL 为三种可区分状态，MUST NOT 以空结果静默伪装「无覆盖」。

#### Scenario: 断更标的状态可查

- GIVEN 标的 600845 主表最新研报日期为 2025-04-10
- WHEN 查询其覆盖状态
- THEN SHALL 返回最新日期 2025-04-10 与断更标注
- AND MUST NOT 表现为「无覆盖」

### Requirement: 调度注册与限流纪律

三个日批 job（研报元数据、评级变动、预期快照）SHALL 注册进 start_scheduler 的调度注册面，注册面 SHALL 有镜像守护测试（新增 job 漏注册时测试红）。对东财 reportapi/datacenter 与巨潮的调用 SHALL 串行执行、单请求间隔 ≥1 秒加随机抖动、复用 HTTP 会话并携带常规 UA 与 Referer。

#### Scenario: 注册面镜像守护

- GIVEN 新增研报元数据 job
- WHEN 调度注册表镜像测试运行
- THEN SHALL 验证该 job 已注册（漏注册即测试失败）

#### Scenario: 限流纪律可验证

- GIVEN 采集器对东财发起 N 个连续请求
- THEN 相邻请求间隔 SHALL ≥1 秒且复用同一会话

### Requirement: 手工投喂与全文入库（个人研究）

系统 SHALL 支持从本机投喂目录（env `RESEARCH_REPORT_INBOX_DIR`，缺省 `data/research_report_inbox`）扫描 PDF 研报并解析全文入库：按文件内容 sha1 幂等，落 `report_documents` 表（filename/file_path/title/org/publish_date/page_count/char_count/text/usage_policy/source_type/ingested_at）。同一文件重复投喂 MUST NOT 产生重复行。文档 SHALL 标记 `usage_policy=personal_research`（个人研究自用；本系统不对外分发全文）。解析失败 SHALL 记入运行记录并继续处理其他文件；投喂目录不存在 SHALL 为合法空态。系统不自动下载全文，全文只经人工投喂进入。

#### Scenario: 正常投喂入库

- GIVEN 投喂目录含一份未入库的 PDF（文件名含机构与日期）
- WHEN 投喂扫描运行
- THEN `report_documents` SHALL 新增一行，含页数、字符数与全文
- AND 运行记录 SHALL 记 ok 且 rows=1

#### Scenario: 重复投喂幂等

- GIVEN 同一份 PDF 已入库
- WHEN 再次扫描同一目录
- THEN 文档表行数 SHALL 不变，本次计 skipped
- AND MUST NOT 覆盖首次入库时间

#### Scenario: 解析失败降级

- GIVEN 投喂目录含一个损坏 PDF
- WHEN 投喂扫描运行
- THEN 该文件 SHALL 记入运行记录错误，其余文件照常处理
- AND 全部失败时运行记录 SHALL 为 failed

#### Scenario: 目录不存在为空态

- GIVEN 投喂目录尚未创建
- WHEN 投喂扫描运行
- THEN 运行记录 SHALL 为 empty，MUST NOT 视为失败

### Requirement: 投喂文档结构化抽取

系统 SHALL 对投喂 PDF 全文做 LLM 结构化抽取，产出 broker / analyst / report_date / report_type / stocks[]（code/name/rating/rating_normalized/target_price/eps_forecasts/industry）/ core_logic / catalysts / risks / summary，落 `report_extractions`（doc_key 主键）。抽取 MUST 只依据原文，原文缺失的字段为 null 或空数组，MUST NOT 推断补全。评级 SHALL 归一化到 买入/增持/中性/减持/卖出/未知（含观点型表述：看多→买入、偏多→增持、看空→卖出、偏空→减持）；模型给出「未知」时 SHALL 退回原始评级做确定性映射。抽取失败 SHALL 不影响文档入库并记入运行记录；同一文档已有抽取时 SHALL NOT 重复调用 LLM（force 显式重抽）。

#### Scenario: 抽取落库

- GIVEN 一份已投喂的研报全文
- WHEN 抽取运行
- THEN `report_extractions` SHALL 新增一行，含评级、目标价、EPS 预测与观点清单
- AND 股票代码非 6 位时 SHALL 置 null（MUST NOT 猜代码）

#### Scenario: 已有抽取不重复调用

- GIVEN 文档已有抽取结果
- WHEN 批量抽取再次运行
- THEN 该文档 SHALL 计入 skipped，LLM 调用次数不增加

#### Scenario: 抽取失败不阻断入库

- GIVEN LLM 网关不可用
- WHEN 投喂扫描运行
- THEN 文档 SHALL 正常入库，运行记录记 degraded 与错误
- AND 后续可通过批量抽取补做

#### Scenario: 观点型评级归一化

- GIVEN 原文评级为「看空」且模型归一化字段为「未知」
- WHEN 抽取结果入库
- THEN rating_normalized SHALL 为「卖出」

### Requirement: 按标的合并投喂研报

系统 SHALL 按标的合并多篇投喂抽取结果：输出报告数、覆盖机构、评级分布、目标价区间（min/median/max/count）、分年 EPS 区间、去重后的核心逻辑/催化剂/风险清单与文档清单；无该标的记录时返回 None。合并 SHALL 为确定性聚合（MUST NOT 再调用 LLM）。分析师上下文在存在合并结果时 SHALL 注入「手工投喂研报合并」段，并附防锚定条款（投喂研报为个人研究材料，观点有立场与覆盖偏差）。

#### Scenario: 多篇聚合

- GIVEN 同一标的有两篇投喂研报（评级分别为买入与增持，目标价 30 与 40）
- WHEN 合并运行
- THEN 评级分布 SHALL 为买入 1 / 增持 1
- AND 目标价区间 SHALL 为 30~40（中位 35）

#### Scenario: 无投喂记录

- GIVEN 某标的没有任何投喂抽取
- WHEN 分析期读取合并摘要
- THEN 结果 SHALL 为 None，分析师上下文 SHALL 不出现该段

### Requirement: 历史回填

系统 SHALL 支持按日期区间分窗回填（默认起点 2024-01-01，走东财接口按窗口拉取），与日批共用同一幂等键；回填完成后与日批增量无缝衔接。第三方日更仓库仅可作为回填结果的对照校验源，MUST NOT 成为生产依赖。

#### Scenario: 区间回填幂等

- GIVEN 2024-01-01 至 2026-10-01 区间已回填
- WHEN 同区间再次回填
- THEN 主表行数 SHALL 不变

### Requirement: 全文页偏移入库

系统 SHALL 在手工投喂 PDF 入库时记录每页在全文中的字符边界，落 `report_documents.page_offsets`（JSON 数组，元素含 `page` / `start` / `end`，左闭右开）。

- 偏移 SHALL 按固定拼接规则 `"\n".join(page_texts)` 计算，并与 `text` 字段一致。
- 解析失败（加密、无文本层、损坏）时 `page_offsets` SHALL 为 null，文档仍入库，运行记录 SHALL 标 degraded 并继续处理其他文件。
- 存量文档未重解析时 `page_offsets` SHALL 保持 null；MUST NOT 按页数均分或其他方式伪造偏移。

#### Scenario: 数字 PDF 页偏移正确

- **GIVEN** 一份 12 页、可抽取文本层的 PDF
- **WHEN** 投喂扫描入库
- **THEN** `page_offsets` SHALL 含 12 个元素
- **AND** 每个 `[start, end)` 切片 SHALL 落在对应页文本范围内
- **AND** 首元素 `start=0`，末元素 `end=len(text)`（忽略页间分隔符）

#### Scenario: 扫描件降级不阻断

- **GIVEN** 投喂目录含一份无文本层的扫描 PDF
- **WHEN** 投喂扫描运行
- **THEN** 该文档 `page_offsets` SHALL 为 null
- **AND** 运行记录 SHALL 为 degraded 且错误可查
- **AND** 同目录其他 PDF SHALL 正常入库

#### Scenario: 存量文档不伪造偏移

- **GIVEN** 一份在本次变更前入库的文档，未执行重解析
- **WHEN** 查询该文档
- **THEN** `page_offsets` SHALL 为 null
- **AND** MUST NOT 返回按页数均分的估算值

### Requirement: 抽取观点页码标注

投喂文档的结构化抽取 SHALL 支持为每条核心逻辑、催化剂、风险与个股标注来源页 `source_page`；原文无法定位页码时 SHALL 为 null。

- 模型 MUST NOT 依据段落顺序猜测页码。
- 消费端 SHALL 兼容旧抽取的字符串形态与新抽取的 `{text, source_page}` 对象形态。
- 页码 SHALL 落在该文档 `page_count` 范围内；越界值 SHALL 归一为 null 并记入抽取运行记录。

#### Scenario: 观点带页码

- **GIVEN** 一份抽取成功、页偏移可用的投喂文档
- **WHEN** 抽取运行
- **THEN** 至少一条核心逻辑/催化剂/风险 SHALL 携带 `source_page`
- **AND** 每个 `source_page` SHALL 在 1..page_count 范围内

#### Scenario: 无法定位时为空

- **GIVEN** 原文没有清晰的页码线索
- **WHEN** 抽取运行
- **THEN** 对应 `source_page` SHALL 为 null
- **AND** MUST NOT 填入 0 或猜测值

#### Scenario: 旧抽取兼容

- **GIVEN** 一份抽取结果为字符串数组（无页码）的存量文档
- **WHEN** 合并或展示该文档
- **THEN** SHALL 正常处理字符串形态
- **AND** 页码 SHALL 显示为未知，不报错

### Requirement: 跨源报告关联

系统 SHALL 在抽取完成后尝试把投喂文档与 `research_reports` 元数据行关联，写入 `report_documents.report_key`。

- 匹配键 SHALL 为 `(broker, report_date, normalized_title)`；日期允许 ±1 天窗口。
- 归一标题 SHALL 只做 NFKC、去空白与常见标点、casefold；MUST NOT 删除中文实词。
- 恰好 1 条候选命中时 SHALL 写入 `report_key`；0 条或多条歧义时 SHALL 留 null。
- 关联失败 MUST NOT 阻断文档入库、抽取或按标的合并。
- v1 MUST NOT 使用编辑距离等模糊匹配。

#### Scenario: 命中元数据行

- **GIVEN** 投喂文档抽取为机构 UBS、日期 2026-09-30、标题归一后与某 `research_reports` 行一致
- **WHEN** 关联 job 运行
- **THEN** `report_documents.report_key` SHALL 等于该元数据行的 `report_key`

#### Scenario: 无匹配留空

- **GIVEN** `research_reports` 中没有同机构同日期同标题的行
- **WHEN** 关联 job 运行
- **THEN** `report_key` SHALL 为 null
- **AND** 文档 SHALL 仍可被抽取与合并

#### Scenario: 多条歧义不猜

- **GIVEN** 同一机构同日存在两篇归一标题相同的元数据行
- **WHEN** 关联 job 运行
- **THEN** `report_key` SHALL 为 null
- **AND** 运行记录 SHALL 记 `ambiguous_match` 计数

### Requirement: 报告深度分级

系统 SHALL 对投喂文档做确定性深度分级，落 `depth_class ∈ {deep, comment, unknown}`。

- 标题含「深度/专题/策略/展望」SHALL 归 `deep`；含「点评/跟踪/快评/更新」SHALL 归 `comment`；其余归 `unknown`。
- 分级 MUST NOT 用于丢弃文档或阻断抽取。
- v1 MUST NOT 用 `depth_class` 改变合并摘要、一致预期或任何决策权重。

#### Scenario: 深度报告分类

- **GIVEN** 投喂文档标题含「深度」
- **WHEN** 入库完成
- **THEN** `depth_class` SHALL 为 `deep`

#### Scenario: 点评分类

- **GIVEN** 投喂文档标题含「点评」
- **WHEN** 入库完成
- **THEN** `depth_class` SHALL 为 `comment`

#### Scenario: 分级不改变合并结果

- **GIVEN** 同一标的有一篇 deep 与一篇 comment 投喂研报
- **WHEN** 按标的合并
- **THEN** 合并结果 SHALL 包含两篇
- **AND** MUST NOT 因分级丢弃或降权任何一篇（v1）

### Requirement: 评级符号归一

系统 SHALL 把各机构评级确定性归一为买入/增持/中性/减持/卖出/未知六档。

- 归一表 SHALL 覆盖常见中英文同义词（优于大市、跑赢行业、推荐、强烈推荐、Accumulate、Overweight 等）。
- 未识别评级 SHALL 保留原文并把归一档记为 `unknown`，MUST NOT 丢弃该条记录。
- 归一 SHALL 在抽取层完成并落库，合并与展示 SHALL 使用归一结果，同时保留原文供核对。

#### Scenario: 同义词归一

- **GIVEN** 原文评级为「优于大市」
- **WHEN** 抽取结果归一化
- **THEN** 归一档 SHALL 为「买入」
- **AND** 原文 SHALL 保留为「优于大市」

#### Scenario: 未知评级不丢弃

- **GIVEN** 原文评级为归一表未覆盖的词
- **WHEN** 抽取结果归一化
- **THEN** 归一档 SHALL 为「未知」
- **AND** 该条个股记录 SHALL 正常保留

### Requirement: 重解析与幂等

系统 SHALL 提供投喂文档重解析能力，用于补齐页偏移、关联键与深度分级。

- `inbox --force-reparse` SHALL 按 `doc_key` 更新现有行，MUST NOT 新增重复行。
- 重解析 SHALL NOT 重复调用 LLM；只有显式 `extract --force` 才重抽。
- 原文件缺失时 SHALL 保留原行与全文，记 `file_missing`，MUST NOT 删除文档。
- 普通重复扫描 SHALL 仍按内容哈希跳过（既有幂等语义不变）。

#### Scenario: 重解析补齐偏移

- **GIVEN** 一份存量文档 `page_offsets=null` 且原 PDF 仍存在
- **WHEN** 执行 `inbox --force-reparse`
- **THEN** 该行 `page_offsets` SHALL 被补齐
- **AND** 文档行数 SHALL 不变

#### Scenario: 原文件缺失不删行

- **GIVEN** 一份存量文档的原 PDF 已被移动
- **WHEN** 执行 `inbox --force-reparse`
- **THEN** 该行 SHALL 保留，`page_offsets` 保持原值
- **AND** 运行记录 SHALL 含 `file_missing`

### Requirement: 策略与宏观观点抽取

投喂研报的结构化抽取 SHALL 为策略/宏观类报告输出行业观点与市场观点，不得因缺少个股条目而丢弃观点信息。

- 新增 `sector_views[]`：每条含 `industry`、`direction ∈ {看多, 中性, 看空, 未知}`，可选 `rationale` 与 `source_page`。
- 新增 `market_view`：含可选 `sentiment`、`positioning`、`key_catalysts`；只抽原文明确表述，MUST NOT 推断仓位或情绪。
- 缺失新字段的旧抽取 SHALL 视为空，MUST NOT 要求重抽或阻断合并。
- 抽取 MUST NOT 调用 LLM 之外的规则做二次发挥；仍遵循「只抽原文」。

#### Scenario: 策略报告产出行业观点

- **GIVEN** 一份策略报告正文明确写着「看好银行、电池与部分工业疲弱」
- **WHEN** 抽取运行
- **THEN** `sector_views` SHALL 含银行（看多）与电池/工业（看空或中性）
- **AND** `direction` SHALL 取自原文语义，无法判断时为「未知」

#### Scenario: 仓位表述只抽不推

- **GIVEN** 原文写「外资仓位较 6 月减轻」
- **WHEN** 抽取运行
- **THEN** `market_view.positioning` SHALL 记录该表述
- **AND** MUST NOT 补写原文没有的仓位百分比或方向

#### Scenario: 旧抽取兼容

- **GIVEN** 一份没有 `sector_views` / `market_view` 的存量抽取
- **WHEN** 聚合或注入运行
- **THEN** SHALL 把缺失字段视为空
- **AND** MUST NOT 报错或要求重抽

### Requirement: 策略报告个股清单边界

策略/宏观报告的 `stocks[]` SHALL 只包含正文中真正被讨论、带个股论据的标的；附录、免责声明或「全覆盖评级表」中的条目 MUST NOT 进入 `stocks[]`。

- 判断标准：该标的在正文中有独立论述（观点、逻辑、评级依据、目标价、盈利预测）之一。
- 仅出现在披露表中的「12 个月评级 + 当日收盘价」SHALL 视为覆盖披露，不是本篇报告的个股结论。
- 抽取器无法判断时 SHALL 宁可为空，MUST NOT 为凑数而列出。
- 如未来需要保留披露表评级，SHALL 使用独立字段（如 `disclosure_ratings[]`），不得复用 `stocks[]`。

#### Scenario: 披露表条目不入个股清单

- **GIVEN** 一份策略报告正文只讨论行业，附录列出 8 只 A 股评级与当日价格
- **WHEN** 抽取运行
- **THEN** `stocks[]` SHALL 为空
- **AND** MUST NOT 把附录条目当作个股覆盖写入

#### Scenario: 正文讨论的个股可入清单

- **GIVEN** 报告正文用一段分析某只个股并给出评级依据
- **WHEN** 抽取运行
- **THEN** 该标的 SHALL 进入 `stocks[]`
- **AND** SHALL 携带正文来源页或 null

### Requirement: 策略观点聚合读取

系统 SHALL 提供确定性的策略/宏观观点聚合读取，供宏观分析师消费。

- 选取范围 SHALL 为 `report_type ∈ {策略报告, 宏观研究}` 的抽取结果。
- 默认取最近 30 天内、按报告日期降序的前 3 篇；阈值 SHALL 可配置。
- 聚合 SHALL 输出机构、日期、行业观点方向分布与论据、核心逻辑、催化剂、风险、分歧点；MUST NOT 调用 LLM。
- 无记录时 SHALL 返回 None；最新一篇超过 30 天时 SHALL 返回 None 并记 `stale`，MUST NOT 以陈旧观点冒充当前观点。
- 聚合结果 SHALL 受硬上限约束（行业数、每行业论据数、各清单条数），超出截断并标注。

#### Scenario: 多篇聚合并标出分歧

- **GIVEN** 两篇 30 天内的策略报告，一篇看多银行、一篇看空银行
- **WHEN** 聚合读取运行
- **THEN** `sector_views` SHALL 含银行组的方向分布
- **AND** `disagreements` SHALL 记录该行业存在方向冲突

#### Scenario: 陈旧观点不注入

- **GIVEN** 库中最新策略报告日期距今 45 天
- **WHEN** 聚合读取运行
- **THEN** SHALL 返回 None 并记 `stale`
- **AND** 宏观上下文 MUST NOT 出现该观点

#### Scenario: 无策略报告为空态

- **GIVEN** 库中没有任何策略/宏观抽取
- **WHEN** 聚合读取运行
- **THEN** SHALL 返回 None
- **AND** MUST NOT 生成空段或占位文本

