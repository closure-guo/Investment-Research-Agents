# Delta for research-report-dataplane

## ADDED Requirements

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
