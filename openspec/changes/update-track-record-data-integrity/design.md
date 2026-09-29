# Design: update-track-record-data-integrity

## Context

生产库核实（2026-09-28 快照）：组合指标年化 +2853%、波动 91%、夏普 31.3、风险分恒 10。分桶归因（incident 032）三个独立根因：

1. `marking.py:115` `sign = 1.0 if direction == "long" else -1.0` 把 neutral 观点当空头盯市，与 `judgment.py:120` 回避判定"neutral 按 long 口径"矛盾。生产库 12 条有盯市观点全部为 neutral（150 条 marks），整条净值线是幻影空头损益。
2. `ingest.py` 参考价取 quote 无任何校验。茅台 600519 于 09-05/09-06（周末）创建的 4 条观点记录 entry=1800，而行情核实 09-01~09-09 qfq 收盘在 1290~1330（hfq 连续，排除除权）——坏 quote 偏离 35% 直接入库；同管道 09-08 上午又批量拿不到价（39 条 legacy `missing_entry_price` unresolvable）。
3. `metrics.py` 口径缺陷：`daily_portfolio_returns` 首盯市日日收益 = 相对入场参考价的整段 cum_return（周末坏价缺口 27% 全记在一天）；年化 `final_nav^(252/n)-1` 的 n 只数有盯市的日期（14 天），spec"空仓日记 0 收益"对应的 `if rets else 0.0` 是死分支。三者相乘：首日组合收益 +21.7% → 1.2069^18-1 = 28.53，与快照逐位吻合（已复算闭环）。

另发现运维问题：daily_marks/equity_curve 停在 09-24，而 metrics 快照 09-25/09-28 仍在写且逐位相同——页面"快照截至 09-28"展示的是 09-24 数据。as_of 诚实性随本变更一并修正；marking 断更的根因需按红线查后端日志 + Langfuse（不在本变更代码范围，重建脚本落地后运维验证）。

## Goals / Non-Goals

**Goals:**

- neutral 盯市与组合聚合语义修正；净值/指标对坏参考价免疫；年化/波动 n 口径对齐 spec（交易日全序列）。
- ingest 与盯市两道参考价护栏，阈值配置化。
- 存量 derived 表（daily_marks/equity_curve/agent_metrics_daily）wipe+rebuild 重建工具与核对。
- as_of 诚实性；metrics.md §1 口径登记；incident 032 归因记录。

**Non-Goals:**

- 不动 predictions 原始事实（append-only，坏 entry_price 不修改，由盯市防护隔离 + 人工甄别）。
- 不动胜率/回避正确率/校准/切片统计链路（不经过 marks，未失真）。
- 不处理 overview 的 version/source 过滤与净值曲线/切片的联动（P6 残留，独立变更）。
- 不重标定风险分映射（现行公式有测试与观测案例背书，规范内设计）。
- 不修复 marking 09-24 断更的运维根因（先查日志，另行处置）。

## Approach

**D1 neutral 语义（marking.py + metrics.py）**：`mark_open_predictions` 对 neutral 取 long 口径 sign=1.0（与回避判定同号，marks 语义变为"标的走势"）；组合聚合侧 `daily_portfolio_returns` / `compute_metrics_from_marks` 新增 `exclude_prediction_ids` 参数，调用方（build_equity_curve_points / compute_metrics_snapshot）从 predictions 表查出 direction='neutral' 的 prediction_id 集合传入。备选一：不写 neutral marks——违反现行"全部 open 观点盯市"条款且详情页盯市叠加缺数据，否定。备选二：neutral 按 short 进组合——即现状，回避决策不应产生持仓损益，否定。

**D2 净值口径（metrics.py）**：观点在组合内的首个盯市日贡献 0（现金口径），此后日收益 = 相邻盯市日 cum_return 差分。入场参考价从此完全不进 NAV（仅 marks.cum_return 展示口径），净值对参考价错记免疫。agent 与 benchmark 双线对称：均以首个盯市日为基日、首日 0 收益。

**D3 交易日历骨架（metrics.py + marking.py）**：`daily_portfolio_returns(marks, calendar_dates=None)`：给定排序交易日列表（基准指数日 K 日期）时，序列覆盖首个盯市日以来全部交易日，无盯市日记 0；缺省时维持 marks-only 行为（向后兼容纯函数测试）。基准净值改为直接取基准日 K 收盘按日历推进（marks 内 benchmark_price 在空仓日天然缺失，不可作骨架）。`run_daily_marking` 与 `persist_metrics_snapshot` 各拉一次基准日 K 透传日历；拉取失败降级 marks-only + WARN。

**D4 年化/波动（metrics.py）**：n = 日历序列长度（交易日数，含空仓日）；波动为全序列日收益总体标准差 ×√252；夏普公式不变；回撤基于全序列 NAV。

**D5 参考价护栏（ingest.py + marking.py + 新模块 reference_price.py）**：阈值 `TRACK_ENTRY_PRICE_MAX_DEVIATION`（默认 0.30）在 track_record 包内新模块单点定义。ingest：quote 与 accumulated.kline 最新收盘交叉校验，偏离超阈值 → entry_price 改用 kline 收盘 + WARN；kline 缺失 → 保留 quote + WARN。marking：entry_price 与 created 后首个交易日收盘偏离超阈值 → skipped + WARN 不写 marks（存量 4 条茅台坏价观点因此自隔离）。备选：仅 ingest 护栏不管存量——存量坏价将继续污染 marks 与详情页，否定。

**D6 as_of（api.py + model.py）**：新增 `latest_equity_date()`，overview 的 `portfolio.as_of` 取 `MAX(equity_curve.curve_date)`，无数据为 None。

**D7 重建工具（scripts/track_record_rebuild.py）**：wipe 三张 derived 表 → `run_daily_marking`（真实行情 client，支持 `--db-path`/`--yes`）→ 打印重算后最新快照与净值点数供核对。predictions 不动。

**D8 台账与事故**：先在 `docs/evals/metrics.md` §1 新增 §1.10（盯市符号/净值口径/年化 n/as_of），再动代码；incident 032 按模板落 `docs/incidents/` + README 索引。

## Risks

- [净值的"首日 0"低估了合法的入场日行情] → 量级为单日涨跌幅且方向对称，净值语义（盯市跟踪）本就不含决策时点前损益；口径在 §1.10 与预登记披露。
- [30% 阈值误伤极端合法行情]（北交所一字板 ±30% 边界）→ 阈值配置化 + 仅跳过（可人工甄别恢复），不破坏数据；WARN 逐次可观测。
- [重建脚本误删生产数据] → 仅 wipe 三张可重算 derived 表，predictions/audit_log 不碰；需 `--yes` 显式确认；重算前自动备份 db 文件提示。
- [基准日 K 拉取失败导致日历缺失] → 降级 marks-only（现行行为）+ WARN，不阻塞日批。
- [与并发会话竞态] → 实施在独立 worktree 分支进行，主检出不动。
