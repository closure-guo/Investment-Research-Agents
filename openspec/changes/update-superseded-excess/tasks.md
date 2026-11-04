# Tasks: update-superseded-excess

- [x] 抽出 `judgment.settle_at_exit`（收益/超额/分类唯一实现），`resolve_prediction` 改为委托
- [x] `job.py` superseded 分支改用 `settle_at_exit`，并把 `excess_return` 落库
- [x] 单测：superseded 携带超额 / 按超额分类 / 无基准降级 / horizon 口径零变化
- [x] `test_superseded_reports_scores` 更新为 3 个 Score（补 `decision_excess`）
- [x] `pytest` 定向子集 176 passed（job/judgment 68 + report/metrics/significance/stage_c/index-compare/model 108）
- [x] `openspec validate update-superseded-excess --strict`
- [x] 部署（后端重启 00:00，health 通过）
- [x] 触发 `decision_settle_daily` run_id=512 ok（本批无新 supersede 事件 → 读数待方向翻转出现）
- [ ] `metrics.md` §1.9 观察：首批 superseded 超额读数出现后核对（预计数日内，翻转率 31.6%/日）
