# index-comparison Specification

## Purpose
TBD - created by archiving change add-index-performance-compare. Update Purpose after archive.
## Requirements
### Requirement: 指数区间收益对比读数

系统 SHALL 提供 `GET /api/v1/track-record/index-compare` 端点:入参 `span`(all/3m/6m/1y,缺省 all),基于 equity_curve 组合净值窗口与 `index_closes` 表各指数收盘窗口,返回响应 `{span, window: {start, end}, agent_return, indices, as_of, disclaimer}`;`indices` 为固定指数集(INDEX_COMPARE_UNIVERSE:上证指数 000001、沪深300 000300、中证500 000905、中证1000 000852、创业板指 399006)逐条 `{code, name, return, effective_start_date, beat}`。组合区间收益 SHALL 等于窗口内首尾 agent_nav 之比减一(与净值曲线同源);各指数区间收益 SHALL 等于各自窗口内首尾收盘之比减一。`beat` SHALL 为组合收益与该指数收益的直读比较(组合高 → true,低 → false),SHALL NOT 复用 win/loss 判定链的 ±2% 中性带;该指数窗口内无数据时 `return` 与 `beat` SHALL 为 null。窗口起点 SHALL 取组合净值窗口首日;指数在该日无值时 SHALL 向过去取最近可得交易日为基期并在 `effective_start_date` 如实返回,SHALL NOT 向未来取值。

#### Scenario: 正常对比读数

- **GIVEN** equity_curve 有 2026-09-28 至 2026-10-02 净值点,index_closes 有指数集同期收盘
- **WHEN** 请求 `GET /api/v1/track-record/index-compare?span=all`
- **THEN** 响应 window 为 {start: "2026-09-28", end: "2026-10-02"},agent_return 为两端净值之比减一
- **AND** 每个指数 return 为同口径区间收益,beat 按直读比较给出 true/false
- **AND** 响应含 as_of 与 disclaimer,与 equity-curve 端点语义一致

#### Scenario: 指数基期回退

- **GIVEN** 某指数在组合窗口首日(如 09-28)无收盘记录,最近可得日为 09-26
- **WHEN** 请求该跨度对比
- **THEN** 该指数 return 以 09-26 为基期计算,`effective_start_date` 为 "2026-09-26"
- **AND** 其他不受影响的指数不受该指数缺数影响

#### Scenario: 指数数据晚于窗口起点

- **GIVEN** 某指数在窗口起点之前无任何收盘记录,窗口内自 2026-10-01 起才有数据
- **WHEN** 请求该跨度对比
- **THEN** 该指数以窗口内最早可得日(2026-10-01)为基期,`effective_start_date` 为 "2026-10-01"
- **AND** SHALL NOT 以窗口起点之前的未来日期虚构基期

#### Scenario: 指数窗口内全缺

- **GIVEN** 某指数在窗口内无任何收盘记录(如新指数未回填)
- **WHEN** 请求该跨度对比
- **THEN** 该指数条目 return 与 beat 为 null,effective_start_date 为 null
- **AND** 端点整体返回成功,其余指数正常给出读数

#### Scenario: 组合净值不足

- **GIVEN** equity_curve 少于 2 个净值点
- **WHEN** 请求任意跨度
- **THEN** 响应 window 为实际覆盖区间,agent_return 为 null,各指数 beat 为 null
- **AND** 端点返回成功(空态由前端渲染),SHALL NOT 报错

#### Scenario: 跨度窗口按实际覆盖截断

- **GIVEN** 请求 span=1y 但 equity_curve 仅覆盖 3 个月
- **WHEN** 请求对比
- **THEN** window SHALL 为实际覆盖区间(而非请求跨度推算的区间),响应 span 字段保留请求值

### Requirement: 指数收盘落库与回填

系统 SHALL 维护 `index_closes` 表(主键 index_code+trade_date,列 close),由日批盯市任务(`daily_marking`)每个交易日对 INDEX_COMPARE_UNIVERSE 各指数拉取当日收盘并幂等落库(INSERT OR REPLACE);单指数拉取失败 SHALL 仅记 WARNING 并跳过,SHALL NOT 使盯市任务失败,SHALL NOT 计入盯市汇总的 errors。系统 SHALL 提供一次性历史回填脚本(拉取近 400 自然日),回填与日批共用同一幂等写入,重跑安全;000300 的指数集落库与既有 `daily_marks.benchmark_price` 语义互不影响(判定链路仍读后者)。

#### Scenario: 日批顺带落库

- **WHEN** 日批 daily_marking 运行且指数集行情拉取成功
- **THEN** 各指数当日收盘 upsert 进 index_closes,重跑同日不产生重复行
- **AND** 盯市汇总返回的 marked/skipped/errors 不因指数落库而变化

#### Scenario: 单指数失败隔离

- **GIVEN** 日批运行时中证1000 行情拉取失败(限流/接口异常)
- **WHEN** 盯市任务完成
- **THEN** 其余指数正常落库,任务状态为成功
- **AND** 日志含该指数 WARNING,该指数当日缺数由后续日批或回填自然补齐

#### Scenario: 历史回填幂等

- **WHEN** 回填脚本连续执行两次(同区间)
- **THEN** index_closes 无重复行,收盘值以最后一次拉取为准

### Requirement: 跑赢指数对比的口径披露

系统 SHALL 在 `GET /api/v1/track-record/index-compare` 的响应中追加 `caliber` 对象，包含：

- `nav_points`：窗口内 `equity_curve` 净值点数；
- `directional_views`：窗口内存在 `daily_marks` 记录、且 `direction ∈ {long, short}`、且 `status != 'duplicate_of_day'` 的去重观点数；
- `long_views` / `short_views`：上述口径按方向拆分；
- `settled_count`：`resolved_at` 落在窗口内、且 `status ∈ {resolved_win, resolved_loss}` 的观点数。

数据缺失时各整数字段 SHALL 为 `0`，SHALL NOT 为 null。

系统 SHALL 在对比卡片内渲染该披露（`data-testid="index-compare-caliber"`），文案 MUST 明示「仅统计买入/卖出观点，观望/持有不计入组合仓位」与窗口交易日数、多空条数、期内已结算笔数。

披露 SHALL NOT 改变 `agent_return`、任一指数 `return`/`beat`、`window` 的既有取值与算法。

#### Scenario: 正常披露

- **GIVEN** 窗口内 `equity_curve` 有 20 个净值点、196 条 `short` 观点的盯市记录、0 条 `long`、5 条窗口内已结算
- **WHEN** 请求 `GET /api/v1/track-record/index-compare?span=all`
- **THEN** 响应 `caliber` = `{nav_points: 20, directional_views: 196, long_views: 0, short_views: 196, settled_count: 5}`
- **AND** 前端渲染「多 0 / 空 196」「20 个交易日」「期内已结算 5 笔」

#### Scenario: 空库

- **GIVEN** `equity_curve` 为空
- **WHEN** 请求该端点
- **THEN** `caliber` 各字段均为 `0`
- **AND** `agent_return` 为 null，卡片渲染「净值数据积累中」空态且不渲染披露行

#### Scenario: 旧响应缺 caliber 键

- **GIVEN** 后端返回的响应体不含 `caliber`
- **WHEN** 前端渲染卡片
- **THEN** 不渲染披露行，其余内容照常渲染，SHALL NOT 抛错

#### Scenario: 窗口内无方向性观点

- **GIVEN** 窗口内只有 `neutral` 观点的盯市记录
- **WHEN** 请求该端点
- **THEN** `directional_views` / `long_views` / `short_views` 均为 `0`
- **AND** 披露行 SHALL 仍渲染（明确告知读者组合无方向性仓位）

