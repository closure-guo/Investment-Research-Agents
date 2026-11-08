# Delta for index-comparison

## ADDED Requirements

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
