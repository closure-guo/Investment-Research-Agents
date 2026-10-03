# Delta for track-record-metrics

## ADDED Requirements

### Requirement: 无风险利率序列同步

系统 SHALL 将中债国债收益率曲线 1 年期到期收益率（源：中债估值中心，经 AKShare `bond_china_yield` 获取）按交易日落库至 `risk_free_rates` 表（rate_date 主键 + rate + source），写入幂等（同日覆盖）。日批 SHALL 在指标快照计算前执行 rf 同步；同步失败 SHALL 隔离——不得使盯市或快照失败，不计入 marked/skipped/errors。快照消费侧 SHALL 经回退链解析逐日 rf：库内 carry-forward（目标日无记录时沿用最近可得历史日）→ 全库无数据时回退 `TRACK_RISK_FREE_RATE` 常数（默认 0.02）。

#### Scenario: 日批同步落库

- **WHEN** 日批运行且 chinabond 接口正常
- **THEN** 当日（及缺失区间内）1 年期国债收益率写入 risk_free_rates，重跑幂等

#### Scenario: 同步失败隔离

- **WHEN** chinabond 接口超时或异常
- **THEN** 日批盯市与指标快照仍成功完成，夏普/α 按回退链（carry-forward 或常数）计算，rf 同步失败仅记 WARNING

#### Scenario: 库缺当日记录时向前沿用

- **GIVEN** risk_free_rates 最新记录为 2026-09-28（1.2257%），当日 09-29 无记录
- **WHEN** 解析 09-29 的 rf
- **THEN** 使用 1.2257%（carry-forward），不因单日缺数据回退常数

#### Scenario: 全库无数据兜底

- **GIVEN** risk_free_rates 为空表（首次部署未回填）
- **WHEN** 指标快照计算
- **THEN** 全部日期 rf 取 `TRACK_RISK_FREE_RATE` 常数，快照正常产出

## MODIFIED Requirements

### Requirement: 风险收益指标引擎

系统 SHALL 日批重算 `agent_metrics_daily`：样本量/胜率/平均超额/年化收益/波动率/夏普/**组合 β 与年化 Jensen α**/最大回撤/风险分 `clip(round(0.6*dd+0.4*vol),1,10)`（映射表配置化）。**无风险利率口径**：rf 为逐日年化序列，取自 `risk_free_rates`（中债国债收益率曲线 1 年期，回退链见「无风险利率序列同步」需求）；夏普为标准逐日超额定义 `mean(r_t − rf_t/252) / std(r_t − rf_t) × √252`。β/α 口径：由 equity 双净值序列推导同日期日收益对（组合日收益 = 相邻 agent_nav 之比减一；基准日收益 = 相邻 benchmark_nav 之比减一；仅取两序列均非空的日期，首个盯市日不计入），β 为 OLS 斜率 `Σ((rb−μb)(ra−μa))/Σ((rb−μb)²)`；α 为日频 CAPM 残差年化 `mean(ra_t − rf_t/252 − β×(rb_t − rf_t/252))×252`，rf_t 与夏普同源。重叠日收益对 < 20 时 β 与 α SHALL 为 null（不产出噪声读数）。历史快照重算 SHALL as-of 过滤（重算某 metric_date 行仅用 mark_date/rf ≤ 该日的数据），可经手动重算补齐。

(Previously: 夏普口径为 `(年化收益 − rf) / 年化波动率`，rf 为全窗口单一常数（默认 2%，env `TRACK_RISK_FREE_RATE` 配置化）；α 的 rf 亦为常数 `rf/252`。β/α 定义同 add-portfolio-beta-alpha。)

#### Scenario: 指标快照

- **WHEN** `metrics-snapshot` 任务运行
- **THEN** agent_metrics_daily 写入当日全量指标行，重跑幂等（同日覆盖）

#### Scenario: 夏普按逐日 rf 计算

- **GIVEN** 净值序列日收益 [0.01, −0.02, 0.015]，对应日 rf_t 年化 [1.20%, 1.21%, 1.22%]
- **WHEN** 指标引擎重算
- **THEN** 夏普 = mean(r_t − rf_t/252)/std(r_t − rf_t)×√252（逐日超额），年化收益/波动率/β 口径不变

#### Scenario: β/α 正常计算

- **GIVEN** equity 双净值序列有 ≥ 20 个重叠日收益对，组合日收益与基准日收益存在稳定线性关系（β=0.8）
- **WHEN** 指标引擎重算
- **THEN** beta ≈ 0.8（OLS 斜率），jensen_alpha 按逐日 rf_t 的 CAPM 残差年化，两值随快照落库

#### Scenario: 样本不足时置空

- **GIVEN** 重叠日收益对 < 20（如当前净值曲线仅 17 个交易日）
- **WHEN** 指标引擎重算
- **THEN** beta 与 jensen_alpha 为 null，其余指标不受影响
- **AND** 快照落库 null 值，overview 不展示误导性读数
