# track-record Specification

## Purpose
TBD - created by archiving change add-track-record. Update Purpose after archive.
## Requirements
### Requirement: 观点数据模型（append-only + 快照冻结）

系统 SHALL 建立 `predictions` 表记录每条观点（Agent 一次可判定输出的最小单元：标的、方向、时间窗口，缺任一要素不进入统计）。表含 `source_type`（backtest/live，回测实盘分离）、`direction`（long/short/neutral）、`confidence`（0-1，校准用）、`rationale_snapshot`（**观点摘要字段**：`action`、`fund_manager_decision` 及其 reasoning、**TradeDecision 申报价位 entry/stop/target**；完整分析原文 SHALL 存于会话报告并可由该观点的 `session_id` 关联，不重复存于快照）、`entry_price`（**参考价：决策时点实时价或最近收盘，供展示与盯市**）、`horizon_days`（默认取配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`，默认值 20 交易日，上限 1 年）、`avoidance_status`（neutral 观点回避判定结果，系统计算写入，可空）、`settle_entry_price`（**结算入场价：判定时按标准化口径从行情派生**——决策归属日收盘；收盘后决策取次一交易日收盘；非交易日归属其后首个交易日；判定前 NULL，判定产出写入后冻结）。观点快照（rationale_snapshot/direction/entry_price/created_at）写入后 SHALL 冻结，禁止任何接口修改；判定结果由系统计算，只允许状态流转（open → resolved_* / unresolvable；neutral 观点 → 终态 `status="avoidance"`，细粒度结果写 `avoidance_status`）。`PREDICTIONS_STATUSES` SHALL 含终态 `avoidance`。
(Previously: entry_price 为「参考价」且兼作判定基准、horizon_days 默认 252、rationale_snapshot 不含申报价位、无 avoidance_status / settle_entry_price 列。另本次收敛 rationale_snapshot 需求——原「完整分析原文 + 引用数据快照 + 申报价位」收敛为已实现的「观点摘要字段 + 申报价位」，完整原文改由 `session_id` 关联会话报告。)

#### Scenario: 观点写入即冻结

- **WHEN** 一条观点写入 predictions
- **THEN** rationale_snapshot SHALL 含观点摘要字段（`action`、`fund_manager_decision` 及其 reasoning）与申报价位（如有）
- **AND** 完整分析原文 SHALL 存于会话报告并可由该观点的 `session_id` 关联（不重复存于快照）
- **AND** 后续任何接口（含服务端内部接口）尝试修改 direction/entry_price/rationale_snapshot/created_at SHALL 失败并留记录

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
(Previously: 默认判定窗口 T+252、判定基于参考价（entry_price）、neutral 观点按 short 符号判定并计入胜率（实现缺口）、判定链路无 Score 上报。)

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
- **WHEN** 判定
- **THEN** avoidance_status SHALL 为 avoidance_loss（错过上行）

#### Scenario: 提前结算（观点变更）

- **WHEN** 对同一标的发出方向相反或目标价不同的新观点
- **THEN** 旧观点 SHALL 立即以当前价格结算判定
- **AND** resolution_rule SHALL 记录为 superseded

#### Scenario: 不可判定

- **GIVEN** 标的停牌 / 退市 / 长期无行情
- **WHEN** 判定任务处理
- **THEN** 该观点 SHALL 标记 unresolvable
- **AND** 计入样本量，不计入胜率

### Requirement: 基础统计（胜率 + 平均超额 + 显著性门槛）

系统 SHALL 计算并返回胜率与平均超额收益。胜率 = resolved_win / (resolved_win + resolved_loss)，neutral 与 unresolvable 不进分母；**胜率与平均超额 SHALL 仅统计 long/short 方向观点**。neutral 观点的回避判定结果 SHALL 单独统计为**回避正确率** = avoidance_win / (avoidance_win + avoidance_loss)（avoidance_neutral 与 unresolvable 不进分母），作为独立辅助指标以单独字段返回，SHALL NOT 混入胜率；回避正确率展示门槛与胜率一致（样本 <10 不展示）。显著性门槛 SHALL 约束展示：样本量 < 10 不展示胜率与评级（仅展示样本数与「样本积累中」）；样本量 10–29 展示胜率并标注「样本较少」；样本量 ≥ 30 完整展示。**默认判定窗口 252→20 为口径切点**：切点 SHALL 按 `track-record-versioning`「战绩分段不混算」机制登记，切点前后已结算行 SHALL NOT 混入同一胜率读数。评级（0–5 星）属后续增量（阶段 A 不做）。
(Previously: 无回避正确率统计；无默认窗口口径切点分段约束。)

#### Scenario: 胜率口径

- **GIVEN** 10 条观点（4 win / 4 loss / 2 neutral）
- **WHEN** 计算胜率
- **THEN** win_rate SHALL 为 0.5

#### Scenario: 回避正确率独立统计

- **GIVEN** 12 条 neutral 观点（6 avoidance_win / 3 avoidance_loss / 3 avoidance_neutral）
- **WHEN** 计算统计
- **THEN** 回避正确率 SHALL 为 6/9 ≈ 0.667，以独立字段返回
- **AND** win_rate SHALL NOT 包含任何 neutral 观点的判定结果

#### Scenario: 回避样本不足不展示

- **GIVEN** 已判定 neutral 观点（avoidance_win + avoidance_loss）< 10
- **WHEN** 请求统计
- **THEN** 回避正确率 SHALL 返回 null 与「样本积累中」标注

#### Scenario: 样本量门槛

- **GIVEN** 某 Agent 已判定观点数为 9
- **WHEN** 请求总览
- **THEN** 响应 SHALL 不返回胜率与评级
- **AND** SHALL 返回样本数与「样本积累中」标注

#### Scenario: 口径切点分段

- **GIVEN** 库内同时存在 252 窗口与 20 窗口结算的观点
- **WHEN** 计算对外展示的胜率
- **THEN** SHALL 按 `track-record-versioning` 分段机制区分口径，SHALL NOT 混算单一读数

#### Scenario: 空库

- **GIVEN** 无观点记录
- **WHEN** 请求总览
- **THEN** 计数类字段 SHALL 返回 0，胜率与超额 SHALL 返回 null

### Requirement: track-record 只读 API

系统 SHALL 提供统一前缀 `/api/v1/track-record` 的只读接口：总览（核心指标 + 样本量 + as_of）与观点日志列表（分页，默认时间倒序，包含全部状态）。写入接口仅服务端内部调用（Agent 编排层），SHALL 带鉴权，服务端生成权威 created_at。所有接口响应 SHALL 带 `as_of` 与 `disclaimer: "历史业绩不代表未来表现"`。

观点日志列表 `GET /api/v1/track-record/predictions` SHALL 支持可选查询参数进行排序与过滤（缺省保持全部状态、`created_at DESC`，不传任何参数时行为不变）：

- 排序：`sort_by`（可取值 `created_at`/`symbol`/`direction`/`status`/`entry_price`/`exit_price`/`raw_return`/`excess_return`）+ `sort_dir`（`asc`/`desc`，默认 `desc`）。
- 关键字：`keyword`，大小写不敏感，匹配标的代码 `symbol`、标的名称 `symbol_name`，或方向中文标签（看多/看空/中性）与状态中文标签（进行中/命中/未中/中性/不可判定）。
- 时间段：`date_from` / `date_to`，按观点创建日期（`created_at` 的日期部分）过滤，含两端，最细粒度到日。

列表接口 SHALL 校验非法排序字段，非法值 SHALL 回退默认排序；分页参数（`page`/`page_size`，上限 50）与 `total` 语义保持不变，`total` 反映过滤后的总条数。

#### Scenario: 总览响应含 as_of 与免责声明

- **WHEN** 请求总览
- **THEN** 响应 SHALL 含 as_of、sample_size、win_rate（或 null）、avg_excess（或 null）
- **AND** SHALL 含 disclaimer 文案

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
- **AND** 请求携带方向/状态中文标签（如 `keyword=命中`）时 SHALL 仅返回对应方向或状态的记录

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

### Requirement: 战绩页面（总览 + 观点日志）

系统 SHALL 在前端提供战绩页面：总览区（胜率、平均超额、样本量、as_of）+ 观点日志列表。页面 SHALL 固定展示风险提示「历史业绩不代表未来表现」，不可关闭；观点日志默认视图 SHALL 包含 loss 记录（不可隐藏）；进行中观点 SHALL 展示当前浮动收益并标注「未结算」；状态标签以颜色区分（命中=绿、未中=红、中性=灰、进行中=蓝、不可判定=灰斜杠、**回避=灰**——neutral 观点的回避终态 `status="avoidance"`，标签文本「回避」）。

观点日志表格 SHALL 新增「建立日期」列，展示观点创建日期，并作为可排序列之一。表格列头 SHALL 支持点击切换升/降序，可排序列 SHALL 包含建立日期/标的/方向/状态/入场价/结算价/区间收益/基准超额，当前排序 SHALL 有可见指示。表格上方 SHALL 提供过滤控件：关键字输入框（匹配代码/名称/方向/状态）与起止日期选择（按创建日，到日，含两端）；提交过滤后 SHALL 重新向后端拉取，并在服务端分页。表格 SHALL 提供分页控件以浏览过滤/排序后的完整结果。
(Previously: 状态标签映射未含终态 `avoidance`——已结算 neutral 行会渲染空白标签。)

#### Scenario: 页面渲染总览与观点日志

- **GIVEN** 已有若干不同状态观点
- **WHEN** 用户进入战绩页
- **THEN** SHALL 展示总览指标与观点日志列表
- **AND** 页面固定展示风险提示文案

#### Scenario: 默认视图不可隐藏 loss

- **WHEN** 用户打开观点日志默认视图
- **THEN** SHALL 同时展示 win 与 loss 记录
- **AND** SHALL 不存在「只看好单」类预设筛选

#### Scenario: 回避终态标签渲染

- **GIVEN** 存在 `status="avoidance"` 的 neutral 终态观点
- **WHEN** 战绩页观点日志渲染该行
- **THEN** 状态标签 SHALL 显示「回避」文本（由前端 `predictionStatus.ts` 的单一状态映射产出）
- **AND** SHALL NOT 渲染为空白标签

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

