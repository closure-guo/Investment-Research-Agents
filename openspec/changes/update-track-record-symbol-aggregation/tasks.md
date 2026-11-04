# Tasks: update-track-record-symbol-aggregation

- [x] `metrics.md` §1.12② 口径先行修订（按标的去重、1/M）+ 修订记录
- [x] `model.prediction_symbols` 提供 prediction_id → symbol 映射
- [x] `daily_portfolio_returns` 支持 `symbol_by_prediction`：先按标的平均、再按 M 等权 1/M
- [x] `compute_metrics_from_marks` / `build_equity_curve_points` / `compute_metrics_snapshot` / rf 重算四处透传
- [x] 单测：同标的跨日合并 / 不同标的等权 / 缺映射退化 / 既有用例零回归（`tests/outcome` 999 passed）
- [x] `openspec validate update-track-record-symbol-aggregation --strict` 通过
- [x] 影响评估：生产库副本重算，净值 -5.71% → -4.60%，回撤 5.71%→4.60%，风险分 7→6
- [x] 部署（后端容器重建 + 前端重建，2026-11-04 20:50–20:55，健康检查通过）
- [x] 生产库非破坏性重算：备份 `sessions.db.bak-caliber-20261104-205028`，20 点写入，1 行指标覆盖
- [x] `metrics.md` §2 切点行（已写入）
- [x] 部署后页面复核：本组合 -4.60%、跑赢 4/5、披露行「累计多 0 / 空 49」
