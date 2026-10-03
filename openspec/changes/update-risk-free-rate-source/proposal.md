# Proposal: update-risk-free-rate-source

## Why

track-record 组合指标的夏普与 Jensen α 当前用固定常数无风险利率 2%（`RISK_FREE_RATE`，`metrics.py:19`），显著高于当前市场真实水平（中债 1 年期国债到期收益率 ≈ 1.22%，2026-09-30），导致夏普被系统性低估约 `(2% − 1.22%) / 年化波动率`。作为战绩页头条指标，应改用官方发布的真实利率数据，并让 rf 随时间序列逐日对齐，而非全窗口单常数。

## What Changes

- 新增 `risk_free_rates` 表（rate_date 主键 + rate + source），经幂等迁移建表；日频 1 年期中债国债到期收益率落库缓存
- 新增 AKShare 取数：`AKShareClient.fetch_bond_yield_curve()` 封装 `ak.bond_china_yield`（源主机 yield.chinabond.com.cn，官方中债估值中心，不受东财 IP 封禁影响）
- 日批（daily marking）挂钩：盯市后、指标快照前同步 rf 落库，失败隔离（不得使盯市失败，回退链见下）
- **夏普口径变更为逐日超额收益**：`Sharpe = mean(r_t − rf_t/252) / std(r_t − rf_t) × √252`（标准定义）；Jensen α 同步改用逐日 `rf_t/252`；β 不受影响（OLS 斜率不含 rf）
- 回退链：库里无当日 rf → 沿用最近可得历史 rf（carry-forward）；全库无数据 → 回退 `TRACK_RISK_FREE_RATE` 常数（默认 0.02）
- 回填脚本 `scripts/backfill_risk_free_rates.py` + 历史 `agent_metrics_daily` 快照重算（口径一致性）

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**:
  - `track-record-metrics`（夏普/α 的 rf 口径从常数改为逐日中债 1Y 真实序列 + 回退链）
  - `track-record-data-pipeline`（日批新增 rf 同步任务位，失败隔离；若该 capability 归属不同名请以 specs/ 实际为准）

## Impact

- 代码：`src/finance_agent/outcome/track_record/{model.py,metrics.py,marking.py,risk_free.py(新)}`、`src/finance_agent/data/akshare_client.py`
- 脚本：`scripts/backfill_risk_free_rates.py`（新增）
- 读数影响：`agent_metrics_daily.sharpe` / `jensen_alpha` 历史快照值将随重算变化（预期夏普小幅上修）；`annual_return` / `volatility` / `beta` / 回撤 / 风险分不变
- API/前端：无契约变化（sharpe 数值变化，字段结构不变），不属交互类变更，不触发 E2E 门禁
