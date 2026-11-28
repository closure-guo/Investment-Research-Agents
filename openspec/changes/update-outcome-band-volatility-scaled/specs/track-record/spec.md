# Delta for track-record

> 2026-11-28 owner 拍板进入实施。对「判定规则（Outcome Resolution）」与「基础统计」为
> MODIFIED 合并形态（全文替换，非 diff）；同窗随机基准为 ADDED。

## MODIFIED Requirements

### Requirement: 判定规则（Outcome Resolution）

系统 SHALL 按统一规则判定观点：**默认判定窗口 T+20 交易日**（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`；观点自带 `horizon_days` 则以自带为准，上限 1 年）；**判定入场基准 SHALL 为 `settle_entry_price`（判定时从行情派生，非参考价）**；对同一标的发出方向相反或目标价不同的新观点时，旧的 long/short 观点 SHALL 立即以当前价结算（superseded，入场基准同为派生 `settle_entry_price`）；**neutral 观点 SHALL NOT 走 superseded 提前结算**（回避判定以完整 horizon 窗口为准）。方向判定：long 观点区间超额收益 > `band` → resolved_win；< −`band` → resolved_loss；±`band` 区间内 → resolved_neutral（计入总数但不计入胜率分子分母）；short 对称。**`band` 为波动率自适应中性带（update-outcome-band-volatility-scaled，2026-11-28 拍板）：`max(1.5%, 0.5 × 决策归属日前 20 个交易日日收益样本标准差 × √20)`，按每条观点在判定时独立计算，并随判定结果落库 `neutral_band_used`；K 线不足 21 个收盘点或数值异常 → `band` 回落 2%（与切点前行为一致）。** **中性方向（neutral）观点 SHALL 按回避语义判定**：horizon 到点按 long 口径计算区间超额收益，超额 < −`band` → avoidance_win（回避下跌正确）、> +`band` → avoidance_loss（错过上行）、±`band` 内 → avoidance_neutral；结果写 `avoidance_status`，SHALL NOT 写 resolved_* 状态、SHALL NOT 进入胜率。**superseded 提前结算 SHALL 使用该行按入场日前 20 日波动率计算的同一 `band`（与 horizon 到点判定同一口径）。** 停牌/退市/长期无行情 SHALL 标记 unresolvable，计入样本量，不计入胜率，UI 单独标识。**判定链路 SHALL 对 long/short 观点向 Langfuse 上报 decision_hit / decision_return / decision_excess（沿既有规范），neutral 观点 SHALL NOT 上报决议类 Score。** **切点（2026-11-28）之前已结算记录的分类 SHALL NOT 回填或重判，按产出时口径（固定 ±2%）解释；`neutral_band_used` 为 NULL 即标识该存量口径。**

(Previously: 中性带为全局常量 ±2%（回避判定共用），superseded 与到期判定同用该固定带；无逐行带宽落库，存量与新判定不可区分。)

#### Scenario: 到期判定

- **GIVEN** 某 long 观点到达 horizon 终点（默认第 20 个交易日）
- **WHEN** 判定任务结算
- **THEN** 按区间超额收益与该行 `band` 判定 resolved_win / resolved_loss / resolved_neutral

#### Scenario: 高波动标的中性带按行放宽

- **GIVEN** 某 long 观点入场日前 20 个交易日日收益标准差 2%（窗口波动 ≈ 8.94%）
- **WHEN** 判定该观点，区间超额 +3%
- **THEN** `band` SHALL 取 max(1.5%, 4.47%) ≈ 4.47%
- **AND** 该观点 SHALL 判 resolved_neutral（固定 2% 带下会是 resolved_win）
- **AND** `neutral_band_used` SHALL 落库该带宽

#### Scenario: 低波动标的保底带宽

- **GIVEN** 某观点入场日前 20 日窗口波动 1%（0.5 × 1% = 0.5% < 1.5%）
- **WHEN** 判定
- **THEN** `band` SHALL 取保底 1.5%

#### Scenario: 波动不可得回落固定带

- **GIVEN** 某观点 K 线不足 21 个收盘点（无法计算 20 个日收益）
- **WHEN** 判定
- **THEN** `band` SHALL 取 2%，`neutral_band_used` SHALL 为 0.02

#### Scenario: 存量分类不重判

- **GIVEN** 库内存在切点前按 ±2% 结算的 resolved_* 行（`neutral_band_used` 为 NULL）
- **WHEN** 读取战绩或重跑日批判定
- **THEN** 其分类与读数 SHALL 保持不变，SHALL NOT 按新带重判或回填

#### Scenario: 中性观点回避判定

- **GIVEN** 某 neutral 观点（watch 映射）到达 horizon 终点，按 long 口径区间超额 −5%（超出该行 band）
- **WHEN** 判定任务结算
- **THEN** avoidance_status SHALL 为 avoidance_win（回避正确）
- **AND** 该观点 SHALL NOT 获得 resolved_* 状态，SHALL NOT 进入胜率分子分母

#### Scenario: 回避错过上行

- **GIVEN** 某 neutral 观点到达 horizon 终点，按 long 口径区间超额 +6%（超出该行 band）
- **WHEN** 判定任务结算
- **THEN** avoidance_status SHALL 为 avoidance_loss（错过上行）

#### Scenario: 提前结算（观点变更）

- **WHEN** 对同一标的发出方向相反或目标价不同的新观点
- **THEN** 旧观点 SHALL 立即以当前价格结算判定（band 同按该行入场日前 20 日波动率计算）
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

系统 SHALL 计算并返回**连续超额读数族**作为主指标，并保留胜率作为辅助分层。**主读数（人口 = long/short 日主已判定三态 resolved_win/loss/neutral）：`mean_excess`（均值）、`median_excess`（中位）、`excess_win_rate`（超额 > 0 的占比）、`excess_std` 与 `t_stat`（均值 /（标准差/√n））、`distinct_symbols`（去重标的数）。** `distinct_symbols` SHALL 与读数条数并排披露——同一标的多条观点 SHALL NOT 冒充独立样本。胜率 = resolved_win / (resolved_win + resolved_loss)，neutral 与 unresolvable 不进分母；**胜率与连续超额 SHALL 仅统计 long/short 方向观点**。neutral 观点的回避判定结果 SHALL 单独统计为**回避正确率** = avoidance_win / (avoidance_win + avoidance_loss)（avoidance_neutral 与 unresolvable 不进分母），作为独立辅助指标以单独字段返回，SHALL NOT 混入胜率；回避正确率展示门槛与胜率一致（样本 <10 不展示）。**显著性门槛 SHALL 同时约束主读数与胜率：settled（resolved_win + resolved_loss）< 10 时 SHALL NOT 返回 `mean_excess` / `median_excess` / `excess_win_rate` / `t_stat` / 胜率（只返回样本数与去重标的数，标注「样本积累中」）；样本量 10–29 展示并标注「样本较少」；样本量 ≥ 30 完整展示。上述胜率/连续超额/回避正确率/样本量的分母 SHALL 仅计日主观点：duplicate_of_day 关闭行 SHALL NOT 计入任何分母与样本量（总数与 UI 列表展示不受限）。**「全观点 → 日主观点」分母切换与「默认判定窗口 252→20」SHALL 均按 `track-record-versioning`「战绩分段不混算」机制登记口径切点，切点前后已结算行 SHALL NOT 混入同一读数。** 切点前后 SHALL 可区分：`band.settled_with_band` SHALL 披露切点后带波动率自适应带宽落库的已结算条数。

(Previously: 只有二元胜率 + 平均超额两个聚合读数；无中位/超额胜率/IR、无去重标的数披露，样本量按条数计（同一标的重复观点不区分）。)

#### Scenario: 胜率口径

- **GIVEN** 10 条日主观点（4 win / 4 loss / 2 neutral）
- **WHEN** 计算胜率
- **THEN** win_rate SHALL 为 0.5（辅助分层）

#### Scenario: 连续超额读数族

- **GIVEN** 4 条日主观点超额为 +5% / +3% / −2% / 0%
- **WHEN** 计算统计
- **THEN** `excess_n` SHALL 为 4、`median_excess` 为 1.5%、`excess_win_rate` 为 0.5
- **AND** `excess_std` 与 `t_stat` SHALL 按总体三态样本计算，不因中性带截断而丢弃样本

#### Scenario: 去重标的数披露

- **GIVEN** 12 条日主观点分布在 10 个标的（其中两个标的各 2 条）
- **WHEN** 请求总览
- **THEN** `continuous.n` SHALL 为 12、`continuous.distinct_symbols` SHALL 为 10
- **AND** 前端读数条 SHALL 同时展示两者，不得只展示条数

#### Scenario: 连续读数受样本门槛约束

- **GIVEN** settled = 9
- **WHEN** 请求总览
- **THEN** `mean_excess` / `median_excess` / `excess_win_rate` / `t_stat` SHALL 为 null
- **AND** 仅返回 `n` 与 `distinct_symbols`，并标注「样本积累中」

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
- **THEN** 响应 SHALL 不返回胜率、连续超额均值/中位/超额胜率/t 与评级
- **AND** SHALL 返回样本数、去重标的数与「样本积累中」标注

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
- **THEN** 返回空态（total=0、各读数为 null），SHALL NOT 抛错

## ADDED Requirements

### Requirement: 同窗随机基准（随机股票对照）

系统 SHALL 为对外展示的聚合超额读数提供**同窗口随机股票对照**：以每条已结算方向性观点（long/short 三态）的 `[决策归属日, resolved_at]` 为窗口，取该窗口全市场等权平均相对收益（`market_snapshot.close_raw`；任一日期缺行的标的剔除，不插值），基准腿为 000300.SH 同窗口收益；再对窗口等权平均（每窗口一票）。接口 SHALL 返回 `available` / `windows` / `skipped_windows` / `stocks_per_window`（窗口中位股票数）/ `mean_excess` / `median_excess` / `caliber`。**基准不可得（快照库或指数收盘缺失 / 空库 / 计算异常）SHALL 返回 `available=false` 且 `reason` 非空，SHALL NOT 折算 0 或以任何替代值冒充。** 该对照为**只读观测**：SHALL NOT 写入 predictions/audit，SHALL NOT 参与判定状态机，SHALL NOT 进入胜率/回避正确率读数。

#### Scenario: 同窗随机基准与系统读数并排

- **GIVEN** 12 条已结算 long 观点、5 个唯一窗口，快照库与指数收盘均可用
- **WHEN** 请求 overview
- **THEN** `baseline.available` SHALL 为 true、`windows` 为 5
- **AND** `mean_excess` SHALL 为 5 个窗口各自全市场等权平均超额的等权均值

#### Scenario: 基准不可用如实披露

- **GIVEN** 专属测试库无指数收盘序列
- **WHEN** 请求 overview 并渲染战绩页
- **THEN** `baseline.available` SHALL 为 false 且 `reason` 非空
- **AND** 页面 SHALL 显示「同窗随机基准：<原因>」，SHALL NOT 显示 0%

#### Scenario: 窗口缺行剔除不插值

- **GIVEN** 某窗口的某一日期在快照库缺行
- **WHEN** 计算该窗口
- **THEN** 该窗口 SHALL 计入 `skipped_windows`
- **AND** SHALL NOT 用前值填充或插值补齐
