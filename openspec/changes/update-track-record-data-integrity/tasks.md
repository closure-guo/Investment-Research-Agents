# Tasks: update-track-record-data-integrity

- [x] 口径先行：`docs/evals/metrics.md` §1 新增 §1.10 登记 neutral 盯市符号/净值首日 0/交易日历年化 n/as_of 语义（先改台账再动代码）
- [x] incident 032 归因报告落 `docs/incidents/` 并更新 README 索引
- [x] neutral 观点盯市改多头口径，且组合日收益/净值/指标聚合排除 neutral（复现测试先行：neutral marks 不改变净值与年化）
- [x] ingest 参考价交叉校验护栏：quote 偏离 K 线收盘 > 阈值降级收盘价 + WARN，K 线缺失保留 quote + WARN（复现测试：1800 vs 1330 案例形状）
- [x] 盯市侧参考价失效防护：entry 与 created 后首个交易日收盘偏离 > 阈值 → skipped + WARN 不写 marks（复现测试：茅台 1800/1316 案例形状）
- [x] 净值/指标交易日历口径：观点首盯市日贡献 0、基准净值按日历推进、空仓交易日补 0 点、年化/波动 n = 交易日数（复现测试：缺口场景年化不放大、死分支消除）
- [x] 总览 `portfolio.as_of` 改为 equity_curve 最新数据日期（API 测试：停更场景 as_of 如实）
- [x] derived 表重建脚本 `scripts/track_record_rebuild.py`（wipe 三表 + rebuild + 重算核对输出）
- [x] 生产库重算核对（副本演练：4 条坏参考价茅台观点被隔离、long/short 无误伤偏离 0.3%~1.6%、指标量级恢复正常年化 0.0/波动 None；正式库重建待运维在交易日 16:30 后执行）：年化/波动回到常态量级、净值不再含 neutral 幻影损益、坏参考价观点不再产生新 marks
- [ ] `uv run pytest` 全量 + `uv run ruff check` + `uv run mypy` 通过
- [ ] 【部署门禁】正式库执行 `scripts/track_record_rebuild.py --yes` 重建后才可 archive/合入——否则线上残留旧 neutral 幻影净值段，与新口径曲线拼接展示（审查 Important#3）
