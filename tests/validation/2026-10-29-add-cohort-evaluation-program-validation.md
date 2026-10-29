# 人工验证报告：add-cohort-evaluation-program 首轮读数（cohort-eval-20261029）

验证日：2026-10-29｜验证人：实施会话（ZCode）｜环境：生产容器 finance-agent-backend-1（data/ bind mount，只读 URI）

## 1. 抽查锚点（tasks 3.1）

| 锚点 | 方法 | 结果 |
|---|---|---|
| ① 轨道 A 单份成本 | 10-29 job_runs run 383 手算：2,208,598/10 = 220,859.8 | 脚本 cost_days 10-29 = 220,859.8 ✅ |
| ② 反事实超额手算 | 独立脚本对 9 条早期 short 日主样本手算 k=10 超额（生产行情库） | n=9、一致率 0.3333，与脚本 shelf_life k10 完全一致 ✅ |
| ③ citation 覆盖率 | 正确 trace 过滤后逐 trace 有分数 | scored 183 / total 183 ✅ |

注：锚点②同时覆盖 `_window_excess` 的个股腿/基准腿/日历推进实现；锚点③一度报假值（见 §3）。

## 2. neutral 反事实手算样例（fixture 级）

单测 `test_counterfactual_r_e_hand_computed`：个股腿 11/10.05−1、基准腿 3060/3000−1，断言到 1e-12；`test_daily_cluster_aggregation_unit` 验证先日横截面均值再跨日的聚类口径。

## 3. 读数事故（被锚点拦下，修复后重跑）

1. **job_runs 聚合缺失**：同日补跑多行未合并、failed 残行（summary=None）被误判熔断日。修复：按日聚合 ok 行 + 残行计数（单测 `test_window_split_and_circuit_breaker_day` 钉住）。
2. **Langfuse scores API `traceId` 参数失效**（v3 实例实测：任意 traceId 返回同一全局分页，totalItems=5750）。首读 coverage/citation 均为全局混排假象。修复：全量分页预取 + 客户端 `score.traceId` 过滤（单测 `test_score_index_filters_by_trace_id` 钉住）；连带更正「judge 四维分从未挂上 cohort trace」为「9 月底零星存在、10 月起断供」。
3. **非 citation 分数混入轨道 B**（decision_return 被均值化）。修复：citation 前缀白名单（单测 `test_track_b_only_citation_scores` 钉住）。

## 4. confidence 归因（tasks 3.2）

分桶：0.55 为跨窗口持续众数（38%，10-08 起零星、10-17 后常态），与 10-13/10-22 切点无清晰起点关系。人工判读 5 条 Langfuse 原文（p_53454bafa8b0 / p_259eb7060aef / p_6763ad41193b / p_77c8ff613f6e / p_565bc2f8b353）：5/5 为 risk_judge 终稿同向 watch 场景，reasoning 显式引用「同向延续无需增量」「证据均衡带」判据文本。**结论三态 = prompt-anchor**；处置去向 = 全观望锁定处置同面。归因前未产出任何 confidence 下游读数（readout 结构上 `attribution_required=true` 恒置）。

## 5. 零写入与只读验证（tasks 4.1）

- 脚本以 sqlite `mode=ro` URI 连接（结构性只读）。
- 生产库 SHA256 前后对比（data/sessions.db、data/market_snapshot.db，跨三次读数运行）：**逐字节一致**。

## 6. 门禁自查（tasks 4.4）

- [x] 报告无合成总分、无跨段混合均值（分段表逐段给值）
- [x] 全部段 n<15 → 报告头部与各节均为量级描述措辞
- [x] confidence 下游读数为零（归因闸）
- [x] citation_pass 退役口径残留已标注不作读数
- [x] directional/preliminary 标注、neutral 空窗如实披露
- [x] `uv run pytest tests/test_cohort_eval_readout.py` 11 passed；ruff / ruff format / mypy 全绿
- [x] `openspec validate add-cohort-evaluation-program --strict` 通过

## 7. 遗留

- neutral 反事实首批读数 ≥ 11-03（结构性等待，非缺陷）；到期闸 formal 复查 11 月中。
- judge 四维补批、citation 变化人工抽验、日频/预算拍板——三项待 owner，登记于报告 §8。
