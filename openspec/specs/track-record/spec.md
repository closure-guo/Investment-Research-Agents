# track-record Specification

## Purpose
TBD - created by archiving change add-track-record. Update Purpose after archive.
## Requirements
### Requirement: 观点数据模型（append-only + 快照冻结）

系统 SHALL 建立 `predictions` 表记录每条观点（Agent 一次可判定输出的最小单元：标的、方向、时间窗口，缺任一要素不进入统计）。表含 `source_type`（backtest/live，回测实盘分离）、`direction`（long/short/neutral）、`confidence`（0-1，校准用）、`rationale_snapshot`（**观点摘要字段**：`action`、`fund_manager_decision` 及其 reasoning、**TradeDecision 申报价位 entry/stop/target**；完整分析原文 SHALL 存于会话报告并可由该观点的 `session_id` 关联，不重复存于快照）、`session_id`（关联会话报告——此前为规范承诺但未落库，本次补齐）、`trigger_high`/`trigger_low`（watch 观点的双向触发位，取自终稿决策结构化申报，供后续触发监控消费；未申报或非 watch 决策时为 NULL，存为数值）、`entry_price`（**参考价：决策时点实时价或最近收盘，供展示与盯市**）、`horizon_days`（默认取配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`，默认值 20 交易日，上限 1 年）、`avoidance_status`（neutral 观点回避判定结果，系统计算写入，可空）、`settle_entry_price`（**结算入场价：判定时按标准化口径从行情派生**——决策归属日收盘；收盘后决策取次一交易日收盘；非交易日归属其后首个交易日；判定前 NULL，判定产出写入后冻结）。观点快照（rationale_snapshot/direction/entry_price/created_at）写入后 SHALL 冻结，禁止任何接口修改；`session_id`/`trigger_high`/`trigger_low` 同为写入即冻结的快照字段。判定结果由系统计算，只允许状态流转（open → resolved_* / unresolvable；neutral 观点 → 终态 `status="avoidance"`，细粒度结果写 `avoidance_status`）。`PREDICTIONS_STATUSES` SHALL 含终态 `avoidance`。存量观点的新列 SHALL 为 NULL 且不追溯回填（同存量观点不追溯先例）。

(Previously: 表无 `session_id`/`trigger_high`/`trigger_low` 列——`session_id` 关联为规范承诺但列从未落库；watch 触发位仅存于 `rationale_snapshot` 自由文本，机器不可追踪。)

#### Scenario: 观点写入即冻结

- **WHEN** 一条观点写入 predictions
- **THEN** rationale_snapshot SHALL 含观点摘要字段（`action`、`fund_manager_decision` 及其 reasoning）与申报价位（如有）
- **AND** `session_id` SHALL 落库为写入该观点的会话 ID，完整分析原文 SHALL 存于会话报告并可由该列关联（不重复存于快照）
- **AND** 后续任何接口（含服务端内部接口）尝试修改 direction/entry_price/rationale_snapshot/created_at/session_id SHALL 失败并留记录

#### Scenario: watch 触发位落库

- **WHEN** 一条 watch 观点落库，终稿决策申报 `trigger_high=24.6`、`trigger_low=22.91`
- **THEN** 该行 `trigger_high`/`trigger_low` SHALL 落库为 24.6/22.91
- **WHEN** 终稿未申报触发位，或决策为 buy/sell
- **THEN** 两列落 NULL，MUST NOT 从 `reeval_triggers` 文本解析回填

#### Scenario: 回测实盘分离

- **WHEN** 任何对外接口返回战绩数据
- **THEN** 响应 SHALL 按 `source_type` 区分 backtest 与 live
- **AND** SHALL 不存在合并 backtest 与 live 的服务端接口

#### Scenario: 缺要素观点不入统计

- **GIVEN** 一条观点缺标的、方向或时间窗口任一要素
- **WHEN** 写入
- **THEN** SHALL 存档但不进入战绩统计

#### Scenario: horizon 默认值显式落库

- **WHEN** 观点落库且未自带 horizon
- **THEN** horizon_days SHALL 显式写入配置默认值（20 交易日）
- **AND** SHALL NOT 依赖表级默认值隐式生效

#### Scenario: 判定基准为派生入场价

- **GIVEN** 某观点参考价（entry_price）已落库
- **WHEN** 判定任务结算该观点
- **THEN** 区间收益 SHALL 基于 `settle_entry_price`（行情派生）计算，SHALL NOT 基于参考价
- **AND** 派生值 SHALL 随判定结果落库并冻结

#### Scenario: 存量观点不追溯

- **GIVEN** 切点日前落库的 open 观点（horizon_days=252）
- **WHEN** 判定任务处理或口径切点生效
- **THEN** 该观点 SHALL 按其落库时的 horizon 判定，SHALL NOT 被改写为新默认值
- **AND** 已结算存量行 SHALL NOT 重算

#### Scenario: 存量观点新列不追溯

- **GIVEN** 切点日前落库的 open 观点（无 session_id/trigger 列值）
- **WHEN** DDL 迁移生效
- **THEN** 存量行新列 SHALL 为 NULL，SHALL NOT 回填或重算
- **AND** 已结算存量行 SHALL NOT 重算

### Requirement: 观点全量记录

系统 SHALL 落库 Agent 发出的**每一条**观点（P1），不再只记被批准（approve）的决策：reject、hold/watch、neutral 方向的观点 SHALL 同样写入 predictions。不允许删除观点记录，只允许状态流转。

#### Scenario: reject 观点同样落库

- **WHEN** Fund Manager 产出 reject 决策
- **THEN** 该观点 SHALL 写入 predictions（direction 由交易决策的 action 映射：buy→long、sell→short、hold/watch→neutral）

#### Scenario: 记录不可删除

- **WHEN** 通过任何接口尝试删除 predictions 记录
- **THEN** SHALL 失败并留记录

### Requirement: 判定规则（Outcome Resolution）

系统 SHALL 按统一规则判定观点：**默认判定窗口 T+20 交易日**（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`；观点自带 `horizon_days` 则以自带为准，上限 1 年）；**判定入场基准 SHALL 为 `settle_entry_price`（判定时从行情派生，非参考价）**；对同一标的发出方向相反或目标价不同的新观点时，旧的 long/short 观点 SHALL 立即以当前价结算（superseded，入场基准同为派生 `settle_entry_price`）；**neutral 观点 SHALL NOT 走 superseded 提前结算**（回避判定以完整 horizon 窗口为准）。方向判定：long 观点区间超额收益 > +2% → resolved_win；< −2% → resolved_loss；±2% 区间内 → resolved_neutral（计入总数但不计入胜率分子分母）；short 对称。**中性方向（neutral）观点 SHALL 按回避语义判定**：horizon 到点按 long 口径计算区间超额收益，超额 < −2% → avoidance_win（回避下跌正确）、> +2% → avoidance_loss（错过上行）、±2% 内 → avoidance_neutral；结果写 `avoidance_status`，SHALL NOT 写 resolved_* 状态、SHALL NOT 进入胜率。中性带 ±2% 为全局配置项（回避判定共用）。superseded 提前结算 SHALL 同样适用该 ±2% 中性带（与 horizon 到点判定同一带）。停牌/退市/长期无行情 SHALL 标记 unresolvable，计入样本量，不计入胜率，UI 单独标识。**判定链路 SHALL 对 long/short 观点向 Langfuse 上报 decision_hit / decision_return / decision_excess（沿既有规范），neutral 观点 SHALL NOT 上报决议类 Score。**

**日主观点与同日重复关闭**：每条观点 SHALL 派生「决策归属日」（决策产出时间在当日收盘前 → 当日；收盘后或非交易日 → 次一交易日；与 `settle_entry_price` 派生规则同源）。同一 (symbol, 决策归属日) 的多条观点中 `created_at` 最晚者 SHALL 为**日主观点**，其余为**同日重复观点**。同日重复观点中已被 superseded 规则结算的 SHALL 保持其终态（观点变更链保留读数）；其余同日重复观点 SHALL 在日批判定中关闭为终态 `duplicate_of_day`——SHALL NOT 进行方向/回避判定、SHALL NOT 产任何结算读数（resolution_rule 记录为 duplicate_of_day）、SHALL NOT 进入任何统计分母与样本量。日主观点的 T+20 时钟与其 created_at 不受同日重复观点影响。落库层 SHALL 保持 append-only 全量记录（审计与全量留痕语义不变），日主判定为判定/统计消费层的视图行为。

(Previously: 无日主观点概念；同股同日多条观点各自独立判定并全部进入统计——重跑流量使 NAV 权重与统计样本成为重跑次数的函数。)

#### Scenario: 到期判定

- **GIVEN** 某 long 观点到达 horizon 终点（默认第 20 个交易日）
- **WHEN** 判定任务结算
- **THEN** 按区间超额收益判定 resolved_win / resolved_loss / resolved_neutral

#### Scenario: 中性带判定

- **GIVEN** 某 long 观点区间超额收益为 +1.5%（在中性带 ±2% 内）
- **WHEN** 判定
- **THEN** 该观点 SHALL 为 resolved_neutral
- **AND** 计入总数但不计入胜率分子分母

#### Scenario: 中性观点回避判定

- **GIVEN** 某 neutral 观点（watch 映射）到达 horizon 终点，按 long 口径区间超额为 −5%（标的跑输基准超 2%）
- **WHEN** 判定任务结算
- **THEN** avoidance_status SHALL 为 avoidance_win（回避正确）
- **AND** 该观点 SHALL NOT 获得 resolved_* 状态，SHALL NOT 进入胜率分子分母

#### Scenario: 回避错过上行

- **GIVEN** 某 neutral 观点到达 horizon 终点，按 long 口径区间超额为 +6%
- **WHEN** 判定任务结算
- **THEN** avoidance_status SHALL 为 avoidance_loss（错过上行）

#### Scenario: 提前结算（观点变更）

- **WHEN** 对同一标的发出方向相反或目标价不同的新观点
- **THEN** 旧观点 SHALL 立即以当前价格结算判定
- **AND** resolution_rule SHALL 记录为 superseded

#### Scenario: 不可判定

- **GIVEN** 标的停牌 / 退市 / 长期无行情
- **WHEN** 判定任务结算
- **THEN** 该观点 SHALL 标记 unresolvable，计入样本量、不计入胜率

#### Scenario: 同日重跑仅日主观点判定

- **GIVEN** 某标的同一交易日产出 7 条观点（重跑流量，同向 neutral）
- **WHEN** 日批判定运行
- **THEN** created_at 最晚 1 条 SHALL 作为日主观点正常进入判定管线
- **AND** 其余 6 条 SHALL 关闭为 duplicate_of_day，不产结算读数、不计样本量

#### Scenario: 同日观点变更链保留读数

- **GIVEN** 某标的同日先产出 short 观点 A，后产出方向相反 long 观点 B
- **WHEN** B 落库
- **THEN** A SHALL 被 superseded 规则立即结算（读数保留）
- **AND** B SHALL 为当日日主观点，后续同向重跑按同日重复关闭

#### Scenario: 收盘后产出归属次日

- **GIVEN** 某标的当日 15:00 已有日主观点，当日 20:00（收盘后）再次分析产出新观点
- **WHEN** 归属日派生
- **THEN** 新观点归属日 SHALL 为次一交易日，SHALL NOT 夺取当日日主地位
- **AND** 两观点各自作为其归属日的日主观点独立判定

### Requirement: 基础统计（胜率 + 平均超额 + 显著性门槛）

系统 SHALL 计算并返回胜率与平均超额收益。胜率 = resolved_win / (resolved_win + resolved_loss)，neutral 与 unresolvable 不进分母；**胜率与平均超额 SHALL 仅统计 long/short 方向观点**。neutral 观点的回避判定结果 SHALL 单独统计为**回避正确率** = avoidance_win / (avoidance_win + avoidance_loss)（avoidance_neutral 与 unresolvable 不进分母），作为独立辅助指标以单独字段返回，SHALL NOT 混入胜率；回避正确率展示门槛与胜率一致（样本 <10 不展示）。显著性门槛 SHALL 约束展示：样本量 < 10 不展示胜率与评级（仅展示样本数与「样本积累中」）；样本量 10–29 展示胜率并标注「样本较少」；样本量 ≥ 30 完整展示。**上述胜率/平均超额/回避正确率/样本量的分母 SHALL 仅计日主观点：duplicate_of_day 关闭行 SHALL NOT 计入任何分母与样本量（总数与 UI 列表展示不受限）。**「全观点 → 日主观点」分母切换与「默认判定窗口 252→20」SHALL 均按 `track-record-versioning`「战绩分段不混算」机制登记口径切点，切点前后已结算行 SHALL NOT 混入同一读数。评级（0–5 星）属后续增量（阶段 A 不做）。
(Previously: 分母为全观点（含同股同日重复行）；无日主观点口径切点；无默认窗口口径切点分段约束。)

#### Scenario: 胜率口径

- **GIVEN** 10 条日主观点（4 win / 4 loss / 2 neutral）
- **WHEN** 计算胜率
- **THEN** win_rate SHALL 为 0.5

#### Scenario: 重复行不计入分母

- **GIVEN** 同一标的同日 7 条 neutral 观点（1 日主 + 6 duplicate_of_day）全部判定完成
- **WHEN** 计算统计
- **THEN** 回避正确率样本量 SHALL 仅计 1（日主观点）
- **AND** 6 条 duplicate_of_day SHALL 不出现在任何统计分母与样本量中

#### Scenario: 回避正确率独立统计

- **GIVEN** 12 条 neutral 日主观点（6 avoidance_win / 3 avoidance_loss / 3 avoidance_neutral）
- **WHEN** 计算统计
- **THEN** 回避正确率 SHALL 为 6/9 ≈ 0.667，以独立字段返回
- **AND** win_rate SHALL NOT 包含任何 neutral 观点的判定结果

#### Scenario: 回避样本不足不展示

- **GIVEN** 已判定 neutral 日主观点（avoidance_win + avoidance_loss）< 10
- **WHEN** 请求统计
- **THEN** 回避正确率 SHALL 返回 null 与「样本积累中」标注

#### Scenario: 样本量门槛

- **GIVEN** 某 Agent 已判定日主观点数为 9
- **WHEN** 请求总览
- **THEN** 响应 SHALL 不返回胜率与评级
- **AND** SHALL 返回样本数与「样本积累中」标注

#### Scenario: 分母口径切点登记

- **WHEN** 「全观点 → 日主观点」分母切换实施
- **THEN** SHALL 按 track-record-versioning 机制登记切点
- **AND** 切点前按全观点口径结算的读数 SHALL NOT 与切点后日主口径读数混算

#### Scenario: 口径切点分段

- **GIVEN** 库内同时存在 252 窗口与 20 窗口结算的观点
- **WHEN** 计算对外展示的胜率
- **THEN** SHALL 按 `track-record-versioning` 分段机制区分口径，SHALL NOT 混算单一读数

#### Scenario: 空库

- **GIVEN** 无观点记录
- **WHEN** 请求统计
- **THEN** SHALL 返回空统计与「样本积累中」标注，SHALL NOT 伪造读数

### Requirement: track-record 只读 API

系统 SHALL 提供统一前缀 `/api/v1/track-record` 的只读接口：总览（核心指标 + 样本量 + as_of + `legacy_open` 旧口径进行中计数）与观点日志列表（分页，默认时间倒序，包含全部状态）。写入接口仅服务端内部调用（Agent 编排层），SHALL 带鉴权，服务端生成权威 created_at。所有接口响应 SHALL 带 `as_of` 与 `disclaimer: "历史业绩不代表未来表现"`。总览响应 SHALL 新增 `legacy_open` 字段：`status='open'` 且 `horizon_days` 不等于当前头条口径（`caliber_horizon`）的观点计数——与 `legacy_settled`（旧口径已结算计数）分列，二者口径披露互补。

观点日志列表 `GET /api/v1/track-record/predictions` SHALL 支持可选查询参数进行排序与过滤（缺省保持全部状态、`created_at DESC`，不传任何参数时行为不变）：

- 排序：`sort_by`（可取值 `created_at`/`symbol`/`direction`/`status`/`entry_price`/`exit_price`/`raw_return`/`excess_return`）+ `sort_dir`（`asc`/`desc`，默认 `desc`）。
- 关键字：`keyword`，大小写不敏感，匹配标的代码 `symbol`、标的名称 `symbol_name`，或方向中文标签（看多/看空/中性）与状态中文标签（进行中/命中/未中/带内中性/回避/同日重复/不可判定）。
- 时间段：`date_from` / `date_to`，按观点创建日期（`created_at` 的日期部分）过滤，含两端，最细粒度到日。

列表接口 SHALL 校验非法排序字段，非法值 SHALL 回退默认排序；分页参数（`page`/`page_size`，上限 50）与 `total` 语义保持不变，`total` 反映过滤后的总条数。

(Previously: 总览响应无 legacy_open；keyword 状态中文标签集合为「进行中/命中/未中/中性/不可判定」（无回避/同日重复，「中性」一词与方向标签撞词）。)

#### Scenario: 总览响应含 as_of 与免责声明

- **WHEN** 请求总览
- **THEN** 响应 SHALL 含 as_of、sample_size、win_rate（或 null）、avg_excess（或 null）
- **AND** SHALL 含 disclaimer 文案

#### Scenario: 总览含旧口径双计数

- **GIVEN** 库内存在 5 条 252 日口径已结算行与 7 条 252 日口径 open 行，头条口径为 T+20
- **WHEN** 请求总览
- **THEN** `legacy_settled` SHALL 为 5，`legacy_open` SHALL 为 7
- **AND** 二者 SHALL 为独立字段，互不并入对方

#### Scenario: 观点日志默认含全部状态

- **WHEN** 请求观点日志列表（不带任何过滤参数）
- **THEN** 默认 SHALL 返回全部状态（含 resolved_loss）
- **AND** status 过滤 SHALL 仅作为查看维度，不支持按结果筛选隐藏 loss

#### Scenario: 按列排序

- **WHEN** 请求观点日志列表并携带 `sort_by=raw_return&sort_dir=desc`
- **THEN** 响应 SHALL 按区间收益降序排列
- **AND** 携带 `sort_dir=asc` 时 SHALL 按升序排列

#### Scenario: 非法排序字段回退默认

- **WHEN** 请求携带非法 `sort_by`（如 `sort_by=unknown`）
- **THEN** 响应 SHALL 回退为默认排序 `created_at DESC`，不报错

#### Scenario: 关键字过滤

- **WHEN** 请求携带 `keyword=平安` 或 `keyword=600000`
- **THEN** 响应 SHALL 仅返回标的代码或名称匹配的记录
- **AND** 请求携带状态中文标签（如 `keyword=带内中性` 或 `keyword=同日重复`）时 SHALL 仅返回对应状态的记录

#### Scenario: 时间段过滤含两端

- **WHEN** 请求携带 `date_from=2026-09-01&date_to=2026-09-30`
- **THEN** 响应 SHALL 仅返回创建日期落在 [2026-09-01, 2026-09-30] 内的记录（含两端）

#### Scenario: 过滤后分页 total

- **WHEN** 携带过滤参数请求列表
- **THEN** 响应 `total` SHALL 反映过滤后的总条数（而非全量总条数）

#### Scenario: 写入接口内部鉴权

- **WHEN** 外部调用方尝试 POST 创建观点
- **THEN** SHALL 因无内部鉴权被拒绝
- **AND** created_at SHALL 由服务端生成，不可由调用方指定

### Requirement: 观点列表过滤

`GET /api/v1/track-record/predictions` 的 `status` 参数 SHALL 支持组值 `resolved`:传 `status=resolved` 时返回全部非 open 状态(resolved_win/resolved_loss/resolved_neutral/avoidance/unresolvable 及后续新增的非 open 终态)的记录;其余取值 SHALL 保持精确匹配语义不变。分页 total SHALL 反映过滤后子集。
(Previously: status 仅精确匹配单一状态值。)

#### Scenario: resolved 组过滤

- **WHEN** 请求 `?status=resolved`
- **THEN** 响应仅含非 open 状态记录,total 为该子集大小

#### Scenario: 精确匹配语义不变

- **WHEN** 请求 `?status=open` 或 `?status=resolved_win`
- **THEN** 行为与既有精确匹配一致

### Requirement: 当前观点只读接口

系统 SHALL 提供只读接口 `GET /api/v1/track-record/current`：按标的返回其最新一条 `status='open'` 的观点（当前立场视图，每标的至多一行，按 `created_at` 倒序）。行结构 SHALL 与观点日志行一致（含 `source_type`/`symbol`/`symbol_name`/`direction`/`entry_price`/`horizon_days`/`status`/`created_at` 等）。`duplicate_of_day`、已结算（`resolved_*`/`avoidance`）与 `unresolvable` 行 SHALL NOT 出现在本接口响应中。接口 SHALL 支持可选 `source` 查询参数（按 `source_type` 过滤；缺省不过滤，但每行 SHALL 携带 `source_type` 供回测/实盘区分，SHALL NOT 产出无法区分口径的合并视图）。响应 SHALL 带 `as_of` 与 `disclaimer`。本接口为纯展示视图：SHALL NOT 改变任何统计口径（胜率/样本量/NAV/IC 分母仍按 `add-prediction-pool-integrity` 日主规则），SHALL NOT 执行任何写操作。

#### Scenario: 每标的仅返回最新一条 open

- **GIVEN** 同一标的存在多条 `status='open'` 的跨日观点（如 601058.SH 有 8 条 open）
- **WHEN** 请求 `/api/v1/track-record/current`
- **THEN** 该标的 SHALL 仅返回 `created_at` 最新的一条
- **AND** 台账中该标的其余 open 行 SHALL NOT 出现在响应中

#### Scenario: 无 open 观点的标的不出现

- **GIVEN** 某标的全部观点均已结算或关闭
- **WHEN** 请求 `/api/v1/track-record/current`
- **THEN** 响应 SHALL NOT 含该标的任何行

#### Scenario: 已关闭行不进入立场视图

- **GIVEN** 某标的最新落库的一条观点为 `duplicate_of_day` 或已结算状态
- **WHEN** 请求 `/api/v1/track-record/current`
- **THEN** 该标的 SHALL 返回其最新一条 `status='open'` 的观点（若存在）
- **AND** SHALL NOT 返回任何已关闭行

#### Scenario: 回测实盘分离

- **WHEN** 请求 `/api/v1/track-record/current` 不带 `source` 参数
- **THEN** 每行 SHALL 携带 `source_type` 字段供区分
- **AND** 请求携带 `source=live` 时 SHALL 仅返回 `source_type='live'` 的行

#### Scenario: 响应带 as_of 与免责声明

- **WHEN** 请求 `/api/v1/track-record/current`
- **THEN** 响应 SHALL 含 `as_of` 与 `disclaimer` 字段

### Requirement: 战绩页面（总览 + 观点日志）

系统 SHALL 在前端提供战绩页面：总览区（胜率、平均超额、样本量、as_of、**回避正确率（含样本数）、当前判定口径（caliber_horizon）、存量旧口径计数（legacy_settled 与 legacy_open 分列）**）+ 当前观点区（每股最新一条 open 观点的立场视图，add-current-stance-view）+ 观点日志列表。总览区样本量术语 SHALL 使用「已结算」（win+loss 口径）——横幅「样本积累中（已结算 N 条，满 10 条解锁胜率）」、胜率卡「胜率（已结算）」、回避卡「回避正确率（中性观点已结算）」；SHALL NOT 再使用「已判定」表述 win+loss 口径（与观点日志 tab 名「已判定」= 非 open 全集消歧）。口径披露行 SHALL 同时披露两个旧口径计数：`legacy_settled`（「另有 n 条旧口径已结算」或 0 时「无存量」）与 `legacy_open`（n>0 时「另有 n 条旧口径进行中，不计入头条口径」；0 时不渲染该分句）。回避正确率 SHALL 与胜率同门槛（settled < 10 不展示，展示「样本积累中」而非 0 值）；口径与存量计数 SHALL 常驻展示（二者为口径披露，不受样本门槛限制）。页面 SHALL 固定展示风险提示「历史业绩不代表未来表现」，不可关闭；观点日志默认视图 SHALL 包含 loss 记录（不可隐藏）；进行中观点 SHALL 展示当前浮动收益并标注「未结算」；状态标签以颜色区分（命中=绿、未中=红、**带内中性=灰**（resolved_neutral，标签文本「带内中性」，与方向「中性」=观望消歧）、进行中=蓝、不可判定=灰斜杠、回避=灰、同日重复=三级灰）。

**切片指标空态折叠**：总览 `settled=0` 时，切片指标区（持有期/行业/市值/市场环境）SHALL 折叠为一行说明文案（如「切片指标将在首批观点结算后可用」），SHALL NOT 渲染全「—」的四维表格；`settled>0` 时渲染行为不变。

观点日志表格 SHALL 新增「建立日期」列与「窗口」列（**展示该观点自带 `horizon_days`，如「T+20」「T+252」，混合口径显式可见**），并作为可排序列之一。表格列头 SHALL 支持点击切换升/降序，可排序列 SHALL 包含建立日期/标的/方向/状态/参考价（盘面）/结算价（后复权）/区间收益/基准超额；窗口列与结算入场价（后复权）列为展示列，不参与排序。表格上方 SHALL 提供过滤控件：关键字输入框（匹配代码/名称/方向/状态）与起止日期选择（按创建日，到日，含两端）；提交过滤后 SHALL 重新向后端拉取，并在服务端分页。表格 SHALL 提供分页控件以浏览过滤/排序后的完整结果。

**进行中浮动收益**：观点日志中 `status='open'` 的行，区间收益列 SHALL 展示该观点最新每日盯市累计收益（`daily_marks.cum_return`）、基准超额列 SHALL 展示累计超额（`cum_excess`），并保留「未结算」标注；展示值 SHALL 附盯市日期提示（title/悬浮）。无盯市记录的 open 行两列 SHALL 显示「—」。已结算行展示口径不变（结算读数）。

**同日重复折叠**：「已判定」与「全部」tab 中，连续的同 (symbol, 建立日期) `duplicate_of_day` 行 SHALL 默认折叠为一行汇总（展示标的、日期与「同日重复 ×n」），点击汇总行 SHALL 展开组内明细行；展开状态 SHALL 仅作用于当前页前端视图。折叠 SHALL NOT 改变服务端分页 `total`、append-only 台账语义与任何统计口径。「当前持有」tab 无 duplicate_of_day 行，不受影响。
**结算价格展示 SHALL 同口径可比**：观点日志表格 SHALL 在入场价与结算价之间提供「结算入场价（后复权）」列——已结算行展示 `settle_entry_price`（hfq 归属日收盘），进行中行展示未结算占位；该列 SHALL NOT 参与排序（后端排序白名单不变）。价格列头 SHALL 标注口径：入场价列头为「参考价（盘面）」（决策时点参考价 `entry_price`），结算两列列头标注「后复权」；SHALL NOT 将结算价折算回盘面口径展示（复权因子随分红漂移，落库 hfq 值为冻结快照）。观点详情页价格区 SHALL 展示三格：参考价（盘面）/ 结算入场价（后复权）/ 结算价（后复权）。

(Previously: 样本量术语用「已判定」（与 tab 名撞词）；口径披露行仅有 legacy_settled（「无存量」与全部 tab 内旧口径 open 行并存的观感矛盾）；表格无窗口列（252 行混入无从分辨）；open 行区间收益/基准超额恒「—」（盯市数据未接）；resolved_neutral 标签「中性」与方向「中性」同词；settled=0 时切片区渲染全「—」四维表格；同日重复行无折叠刷屏；价格两列未标注口径且未渲染 settle_entry_price（盘面参考价与 hfq 结算价并排产生跨口径误读）。当前观点区一段为 add-current-stance-view 引入，update-track-record-display-clarity 不改动其语义。)

#### Scenario: 页面渲染总览与观点日志

- **GIVEN** 已有若干不同状态观点
- **WHEN** 用户进入战绩页
- **THEN** SHALL 展示总览指标与观点日志列表
- **AND** 页面固定展示风险提示文案

#### Scenario: 默认视图不可隐藏 loss

- **WHEN** 用户打开观点日志默认视图
- **THEN** SHALL 同时展示 win 与 loss 记录
- **AND** SHALL NOT 存在「只看好单」类预设筛选

#### Scenario: 样本量术语消歧

- **GIVEN** settled=0 而观点日志「已判定」tab 有 70 行（dup/带内中性/不可判定）
- **WHEN** 渲染总览
- **THEN** 横幅 SHALL 显示「样本积累中（已结算 0 条…）」
- **AND** SHALL NOT 出现「已判定 0 条」表述

#### Scenario: 口径披露行双计数

- **GIVEN** legacy_settled=5、legacy_open=7
- **WHEN** 渲染口径披露行
- **THEN** SHALL 同时展示「另有 5 条旧口径已结算」与「另有 7 条旧口径进行中，不计入头条口径」
- **AND** legacy_open=0 时该分句 SHALL 不渲染（legacy_settled=0 时仍明示「无存量」）

#### Scenario: 窗口列渲染

- **GIVEN** 观点日志同时存在 horizon_days=20 与 252 的行
- **WHEN** 用户查看观点日志
- **THEN** 窗口列 SHALL 分别显示「T+20」与「T+252」
- **AND** 「当前持有」tab 中的旧口径行 SHALL 可通过窗口列识别

#### Scenario: 进行中浮动收益展示

- **GIVEN** 某 open 观点存在盯市记录（最新 mark：cum_return=-1.2%、cum_excess=-0.8%，mark_date=2026-10-08）
- **WHEN** 渲染观点日志该行
- **THEN** 区间收益列 SHALL 显示 -1.20%、基准超额列 SHALL 显示 -0.80%，状态列保留「未结算」标注
- **AND** 展示值 SHALL 附盯市日期提示

#### Scenario: 无盯市 open 行

- **GIVEN** 某 open 观点无任何盯市记录
- **WHEN** 渲染该行
- **THEN** 区间收益与基准超额列 SHALL 显示「—」（SHALL NOT 显示 0 值冒充）

#### Scenario: 同日重复折叠与展开

- **GIVEN** 「全部」tab 存在某标的某日连续 6 条 duplicate_of_day 行
- **WHEN** 渲染
- **THEN** SHALL 默认显示 1 行汇总（含「同日重复 ×6」），6 条明细行隐藏
- **WHEN** 用户点击汇总行
- **THEN** 6 条明细行展开可见；再次点击收起
- **AND** 分页 total SHALL 保持含全部 6 条的计数值不变

#### Scenario: 切片空态折叠

- **GIVEN** 总览 settled=0
- **WHEN** 渲染切片指标区
- **THEN** SHALL 仅显示一行说明（如「切片指标将在首批观点结算后可用」）
- **AND** SHALL NOT 渲染四维分桶表格

#### Scenario: 回避终态标签渲染

- **GIVEN** 存在 `status="avoidance"` 的 neutral 终态观点
- **WHEN** 战绩页观点日志渲染该行
- **THEN** 状态标签 SHALL 显示「回避」文本（由前端 `predictionStatus.ts` 的单一状态映射产出）
- **AND** SHALL NOT 渲染为空白标签

#### Scenario: 带内中性标签渲染

- **GIVEN** 某看空观点判定为 resolved_neutral（超额落 ±2% 带）
- **WHEN** 渲染该行
- **THEN** 状态列 SHALL 显示「带内中性」（SHALL NOT 与方向「中性」同词）
- **AND** keyword=带内中性 SHALL 能过滤出该行

#### Scenario: 展示建立日期列

- **WHEN** 用户查看观点日志
- **THEN** 表格 SHALL 展示每条观点的建立日期（`created_at` 的日期部分）
- **AND** 该列 SHALL 可参与排序

#### Scenario: 按列排序交互

- **WHEN** 用户点击某可排序列头（如「区间收益」）
- **THEN** 表格 SHALL 按该列重新排序并显示升/降序指示
- **AND** 再次点击 SHALL 切换排序方向

#### Scenario: 关键字过滤交互

- **WHEN** 用户在关键字输入框输入「平安」并提交
- **THEN** 表格 SHALL 仅展示标的代码/名称/方向/状态匹配的记录
- **AND** 空关键字或清空后 SHALL 恢复全量视图

#### Scenario: 时间段过滤交互

- **WHEN** 用户选择起止日期（如 2026-09-01 至 2026-09-30）并提交
- **THEN** 表格 SHALL 仅展示创建日期落在区间内（含两端）的记录

#### Scenario: 过滤后分页浏览

- **WHEN** 过滤结果超过单页条数
- **THEN** 表格 SHALL 提供分页控件，可在过滤后的结果中翻页浏览

#### Scenario: 空态与样本不足

- **GIVEN** 无观点或样本量 < 10
- **WHEN** 渲染总览
- **THEN** SHALL 显示「样本积累中」与已有样本数进度，不显示 0 值冒充数据

#### Scenario: 数据缺口不伪造

- **WHEN** 行情存在缺口日
- **THEN** 图表 SHALL 断点处理，不插值伪造

#### Scenario: 回避正确率披露

- **GIVEN** 回避判定已结算样本 ≥ 10
- **WHEN** 渲染总览
- **THEN** SHALL 展示回避正确率与其样本数（win/(win+loss) 口径）
- **AND** 样本 < 10 时 SHALL 展示「样本积累中」而非 0 值或空白

#### Scenario: 口径与存量计数披露

- **WHEN** 渲染总览
- **THEN** SHALL 展示当前判定口径（如「T+20 交易日」）与存量旧口径计数
- **AND** 存量计数为 0 时 SHALL 明示「无存量」而非隐藏该披露

#### Scenario: 当前观点区每股一行

- **GIVEN** 同一标的存在多条 open 观点（跨日）
- **WHEN** 用户进入战绩页
- **THEN** 当前观点区该标的 SHALL 仅展示最新一条 open 观点
- **AND** 区块 SHALL 注明口径说明（每股仅显示最新一条进行中观点）

#### Scenario: 当前观点区空态

- **WHEN** 无任何标的持有 open 观点
- **THEN** 当前观点区 SHALL 展示空态文案（如「暂无进行中观点」）
- **AND** SHALL NOT 隐藏区块结构或以 0 值冒充数据

#### Scenario: 当前观点行点击进详情

- **WHEN** 用户点击当前观点区某行
- **THEN** SHALL 导航至该观点的详情页（与观点日志行一致）

#### Scenario: 台账不受立场视图影响

- **WHEN** 用户查看观点日志
- **THEN** 台账 SHALL 仍逐条展示全部状态记录（含同股跨日多条 open）
- **AND** SHALL NOT 因当前观点区存在而收敛、过滤或隐藏台账行

#### Scenario: 已结算行展示同口径可比价格

- **GIVEN** 存在已结算观点，`settle_entry_price=6.39`、`exit_price=6.50`（同为后复权口径）
- **WHEN** 观点日志渲染该行
- **THEN** 行内 SHALL 同时展示结算入场价 6.39 与结算价 6.50（两数同口径可直接对比）
- **AND** 参考价列展示 `entry_price`（盘面口径）且列头标注「盘面」
- **AND** 结算两列列头 SHALL 标注「后复权」

#### Scenario: 进行中行结算价格列占位

- **GIVEN** 存在进行中观点（`settle_entry_price` 为 NULL）
- **WHEN** 观点日志渲染该行
- **THEN** 结算入场价单元格 SHALL 展示未结算占位（与结算价列占位一致）

### Requirement: 虚拟 vs 仿真双曲线

战绩仪表盘 SHALL 并排展示虚拟决策结算净值与 paper 仿真成交净值两条曲线（同一批决策、同一基准期），并标注两者差值为执行损耗；基期只向过去回退的防前视纪律对两条曲线同样适用。

#### Scenario: 双曲线展示

- **GIVEN** 某月虚拟结算净值 1.043、仿真成交净值 1.031
- **WHEN** 打开战绩仪表盘
- **THEN** 两条曲线同时渲染，图例区分「虚拟结算」「仿真成交」
- **AND** 执行损耗标注为 -1.2%（两值之差）

