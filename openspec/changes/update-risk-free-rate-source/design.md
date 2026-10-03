# Design: update-risk-free-rate-source

## Approach

**数据通路**：复用 add-index-performance-compare 的「外部日频序列 → SQLite 缓存 → 读数层消费」范式。新增 `risk_free_rates` 表（rate_date 主键、rate、source），新模块 `track_record/risk_free.py` 提供 `sync_risk_free_rates()`（经 `AKShareClient.fetch_bond_yield_curve()` 封装 `ak.bond_china_yield`，只取「中债国债收益率曲线」1 年期列；走 `_call_ak` 超时重试包装）与 `risk_free_series(dates, db_path)`（把目标日期序列解析为逐日年化 rf，库内 carry-forward，全库无数据回退 `TRACK_RISK_FREE_RATE` 常数）。

**口径变更**：夏普从 `(年化收益 − rf) / 年化波动率`（几何年化 − 单常数）改为标准逐日超额定义 `mean(r_t − rf_t/252) / std(r_t − rf_t) × √252`；Jensen α 改逐日 CAPM 残差年化（rf_t 与夏普同源）；β 不变（OLS 斜率不含 rf）。`compute_metrics_from_marks` 保持纯函数，新增可选参数 `rf_series: dict[str, float] | None`（None → 全窗口常数，向后兼容既有调用）；`compute_metrics_snapshot` 自 DB 装载 marks + rf 序列后传入，调用方签名不变。

**日批挂钩**：daily marking 顺序调整为 盯市 → 净值曲线 → **rf 同步（失败隔离）** → 指标快照 → 指数收盘同步（失败隔离）。rf 同步在快照前，保证当日利率可被快照消费；同步失败不阻断（快照走回退链），不计入 marked/skipped/errors。

**回填与历史重算**：`scripts/backfill_risk_free_rates.py` 一次拉全历史落库（幂等 INSERT OR REPLACE）。历史 `agent_metrics_daily` 快照重算必须 **as-of 过滤**：重算某 metric_date 行只用 `mark_date ≤ metric_date` 的 marks 与 rf（不得用今日知识改写历史读数）。夏普序列口径一致性要求重算（同 add-portfolio-beta-alpha 的历史快照补齐先例）。

**期限与源选择依据**：观点结算持有期 20 个交易日（约 1 月），rf 应匹配现金替代品持有期限 → 短端；1 年期国债是 A 股夏普计算的国内研报惯例档位且比 6M/隔夜平滑。源主机 `yield.chinabond.com.cn`（中央结算公司·中债估值中心，官方发布方），非东财域，不受 09-26 东财 IP 封禁影响（备选 `bond_zh_us_rate` 为东财源，弃用）。

## Alternatives Considered

- **最新快照当常数**（最小改动，公式不动）：弃用——长回溯窗口混入不同利率水平，与旧口径同病；且日频序列反正要落库，增量复杂度极小。
- **东财源 `bond_zh_us_rate`**：弃用——单次调用更简单，但东财域存在现行 IP 封禁风险。
- **SHIBOR 3M / DR007**：弃用——货币市场利率含银行信用利波，惯例用于衍生品贴现而非股票夏普；隔夜档波动过大。
- **10 年期**：弃用——更适合长期股权风险溢价场景，与 20 日持有期不匹配。

## Risks

- **堆叠 PR**（本分支基于 `fix/track-record-data-integrity`，#213 未合）：#213 持续更新会带来 rebase 成本；对策——diff 聚焦（rf 相关文件 + metrics.py 公式行），#213 合并后立即 rebase 到新 main 并 retarget PR（注意堆叠 PR retarget 的 CI 触发坑，见 #183/#184 档案）。
- **chinabond 接口可用性/限流**：`_call_ak` 已带超时+线性退避重试；rf 是低敏感参数（±30bp → 夏普 ±0.02 @15% 波动），DB 缓存 + carry-forward + 常数兜底三层回退足够。
- **夏普定义切换的读数跳变**（几何年化−常数 → 算术逐日超额）：除 rf 效应外定义本身有微小差异；对策——人工核读报告给新旧对照表，逐行解释差异来源。
- **spec sync 撞车**：`add-portfolio-beta-alpha`（#207 已合代码、未 sync spec）与本 delta 同 MODIFIED「风险收益指标引擎」；本 delta 的 MODIFIED 文本已包含 β/α 措辞（防谁后 sync 都成立）；若对方先 sync，按 §6 以合并结果为准再套 rf 改动。
