# Tasks: calibrate-decision-confidence

> 收口完成（2026-10-22）：单变量 round15 为**阴性结果**，分布未移动；prompt 保留为显式契约，后续候选见验证报告 §4。

- [x] 基线取证：近 14 天 84 条预测 confidence 分布 = `[0.5,0.6)` 63 条 / `[0.6,0.7)` 20 条 / `<0.5` 1 条 / **≥0.7 零条**
- [x] 先红后绿：三节点模板必须含三档示例（0.85/0.55/0.30）与证据判据（先红 3 failed → 改后 12 passed）
- [x] 改 `prompts/trader.md` / `risk_judge.md` / `fund_manager.md` 的 confidence 段（仅该段）
- [x] `uv run python scripts/deploy_prompts.py` 发布（3 个 OK，18 跳过，0 失败；trader/risk_judge v35、fund_manager v32）
- [x] 评估工具：`evals/task.py` 透传 `decision{action,confidence}`；`evals/run.py` 汇总分布字段（单测钉死）
- [x] 同 dataset 复测 `round15-confidence-calibration`：9 条 deep、judge_failures=0、四维无缺失；confidence `n=9/median 0.55/hist {0.5:9}/尾部 0`，9/9 watch
- [x] 收口：metrics.md §1.20 首轮读数 + 时间线一行 + runs.jsonl `confidence_hist`
- [x] 验证报告 `tests/validation/2026-10-22-calibrate-decision-confidence-validation.md`（含阴性结论与下一轮候选）
- [ ] sync + archive
