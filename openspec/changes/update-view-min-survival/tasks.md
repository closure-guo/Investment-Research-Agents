# Tasks: update-view-min-survival

- [x] `should_supersede` 改为只按方向翻转判定（同向含目标价调整 → 不结算）
- [x] 单测：同向不同目标价 → False；方向翻转 → True；不同标的 → False
- [x] `tests/outcome/test_track_record_judgment.py` + `test_track_record_job.py` 全绿（65 passed）
- [x] `openspec validate update-view-min-survival --strict` 通过
- [ ] 部署（后端重启；`src` bind mount，无需重建镜像）
- [ ] `metrics.md` §1.9 计划口径切点行（观察期开始，2~3 周后评估存活率）
- [ ] 上线后观察：方向性观点存活分布、`superseded` 占比、`expiry` 首次出现
