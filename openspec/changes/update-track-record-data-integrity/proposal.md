# Proposal: update-track-record-data-integrity

## Why

历史战绩页的组合风险指标已被生产数据证实失真：最新快照年化 +2853%、波动率 91%、夏普 31.3、风险分恒 10（极高）。归因（分桶核实，见 incident 032）为三个独立缺陷叠加——① neutral（hold/watch）观点被按空头符号盯市并混入组合净值（`marking.py` sign 与 `judgment.py` 回避判定口径矛盾），生产库 12 条有盯市观点全部为 neutral，整条净值线是"幻影空头"损益；② ingest 参考价无交叉校验，坏 quote 价（茅台 entry=1800 vs 实际 ~1330，偏离 35%）直接入快照并污染盯市；③ 净值/年化口径缺陷——观点首盯市日把"入场→首盯"整段漂移记为单日收益、年化 n 只数有盯市日期（空仓日不补 0，违反现行 spec"空仓日记 0 收益"）。三者共同把周末坏价缺口放大成 +21.7% 的"首日组合收益"，再经 252/14 外推成 +2853% 年化。

## What Changes

- **neutral 盯市语义**（BREAKING 对内存量 marks 数据）：neutral 方向观点的 daily_marks.cum_return 改按多头口径记录（= 标的相对参考价走势，与回避判定引擎同号）；组合日收益/净值曲线/风险指标聚合 SHALL 排除 neutral 观点（回避决策不产生幻影持仓损益）。
- **ingest 参考价交叉校验**：quote 价与最近 K 线收盘偏离超过阈值（默认 30%，`TRACK_ENTRY_PRICE_MAX_DEVIATION` 可配）时拒绝采信 quote、降级 K 线收盘并 WARN；K 线不可得时保留 quote 并 WARN。
- **盯市侧参考价失效防护**：open 观点 entry_price 与首盯市日收盘偏离超阈值 → 跳过该观点不写 marks 并 WARN（存量坏参考价观点不再污染新盯市，供人工甄别）。
- **净值/指标交易日历口径**：观点在组合内的首盯市日贡献 0 收益（现金口径），入场参考价不再参与净值与指标计算（净值纯盯市差分，对坏参考价免疫）；净值序列以基准交易日历为骨架覆盖首个盯市日以来全部交易日，空仓日 agent 日收益 0、基准净值按日历推进；年化/波动/夏普/回撤基于该全序列重算（n = 交易日数）。
- **组合指标 as_of 诚实性**：总览 portfolio.as_of 改为指标所依据盯市/净值数据的最新日期，不再用快照写入日期冒充数据日期（修"快照截至 09-28 实际数据停在 09-24"的误导）。
- **存量 derived 数据重建**：daily_marks / equity_curve / agent_metrics_daily 为可重算 derived 表，提供重建脚本 wipe+rebuild，重算后核对指标回到常态量级。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `track-record-metrics`: neutral 盯市语义与组合隔离（ADDED）、参考价失效盯市防护（ADDED）、组合净值曲线交易日历口径（MODIFIED）、风险收益指标引擎 n 口径（MODIFIED）、组合指标数据日期诚实性（ADDED）。
- `decision-outcome`: 决策落库参考价采信交叉校验护栏（ADDED）。

## Impact

- 后端 `src/finance_agent/outcome/track_record/`：marking.py（neutral 符号、参考价防护、日历透传）、metrics.py（组合聚合排除 neutral、首日 0、日历补 0、年化 n）、ingest.py（交叉校验）、model.py（as_of 查询辅助）、job.py/marking.py 的 run_daily_marking（基准日历透传）。
- API `src/finance_agent/api.py`：overview portfolio.as_of 来源。
- 指标台账 `docs/evals/metrics.md` §1 新增盯市/净值/年化口径条目（口径变更先改 §1 再动代码）。
- 事故记录 `docs/incidents/032-*`（系统性数据失真归因）。
- 存量数据：daily_marks/equity_curve/agent_metrics_daily 全量重建；predictions 为 append-only 原始事实不动（坏参考价 4 条茅台 neutral 观点因冻结语义不修 entry_price，由盯市侧防护隔离 + 人工甄别）。
- 前端无需改动（as_of 字段语义不变，数值更诚实）；胜率/回避正确率统计链路不受影响（不经过 marks）。
