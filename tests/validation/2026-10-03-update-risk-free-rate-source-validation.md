# 人工验证报告: update-risk-free-rate-source

**日期**: 2026-10-03
**验证人**: Closure（ZCode 会话执行，逐项核读）
**关联 delta**: openspec/changes/update-risk-free-rate-source/
**E2E 门禁**: 不适用（纯后端指标/数据管道变更，无交互行为变更）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| rf 表幂等迁移 | 否（单测覆盖） | 存量库自动建表，重跑不重复 | 3 用例绿；生产库拷贝演练建表成功 | ✅ |
| chinabond 取数 | 否（单测+真源） | 中债国债曲线 1Y，紧凑日期 | 真源拉取 23 交易日（08-31..09-30，国庆假期后无新日）利率 1.2197%–1.2525%，量级正确 | ✅ |
| 回退链 | 否（单测覆盖） | carry-forward → 常数 | 5 用例绿（含序列前 backward-fill、空表常数） | ✅ |
| 日批失败隔离 | 否（单测覆盖） | 同步异常不阻断盯市/快照 | 2 用例绿（marked>0、metrics_date 照常） | ✅ |
| 夏普/α 新口径 | 否（单测覆盖） | 逐日超额手算对照 | 3 用例绿（含 cum 差分交叉项、常数兜底） | ✅ |
| as-of 历史重算 | 否（单测+演练） | 只改 rf 三列 | 单测绿；演练 12 行重算，annual/vol/回撤列未动 | ✅ |

## 演练数据核读（生产库拷贝 D:/WorkSpace/tmp-rf-rehearsal/）

对生产库拷贝执行 `backfill_risk_free_rates.py`（回填 23 行利率 + 重算 12 行快照）：

1. **利率序列**：2026-08-31..2026-09-30 共 23 个交易日，1Y 国债 1.2197%–1.2525%，与中债官网量级一致（当前约 1.22%）。
2. **新旧夏普对照的归因（关键）**：末期行 old_sharpe=+28.38 → new_sharpe=−11.47，差异**主因不是本 delta 的 rf/公式变更**，而是生产 marks 仍带 incident 032 失真：
   - old 值由旧口径快照留存：年化 2358%（坏参考价漂移）+ neutral 幻影空头未排除 → sharpe=(23.58−0.02)/0.83=28.38，公式自洽但输入失真；
   - new 值按 #213 修复后的聚合口径（84 条 neutral 观点排除，非 neutral marks 仅 09-28..09-30 三个交易日）as-of 重算：n=3、均值 −0.22%/日、sd 0.31% → −11.47 是**小样本噪声读数**，机制正确；
   - 早期 8 行（09-07..09-22）as-of 无非 neutral marks → new_sharpe=None，符合「不产出噪声读数」纪律。
3. **β/α**：全部行 None——重叠日收益对 3 < 20 门槛，正确置空。
4. **未涉及列**：annual_return/volatility/max_drawdown/risk_* 重算后保持原值（抽查 09-29 行 annual=0.123456 注入用例 + 演练全表核对）。

## 与 #213（incident 032 重建）的执行顺序

正式库的 marks/快照仍待 #213 的 `track_record_rebuild.py` wipe+重建（挂账）。**正序：#213 先重建 → 本 delta 再回填重算**。重建会 wipe agent_metrics_daily 仅重写当日行，本 delta 的日批挂钩此后自动按新口径+真实 rf 产出后续快照；历史 rf 列重算对重建后的库重跑一次 `backfill_risk_free_rates.py` 即可（幂等）。

## 异常记录

- 演练首跑 chinabond 取数 ValueError（"No tables found"）：端点要求紧凑日期（无连字符），已在 `fetch_bond_yield_curve` 边界归一化修复（cafb1f71，含单测契约断言）。
- #213 基分支在实施期间被并发会话推进（b06ce4cf → b30e592d，仅 E2E spec 适配），与本 delta 改动无交集；PR 时 rebase。

## 结论

- [x] 全部通过，可 archive（archive 前置：#213 合并后 rebase + 正式库按正序执行回填）
- [ ] 存在失败项，需修复后重新验证
