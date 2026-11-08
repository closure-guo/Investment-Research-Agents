# Tasks: add-track-record-caliber-disclosure

- [x] 后端 `build_index_compare` 返回 `caliber`（五字段，缺失为 0），既有字段取值零改动
- [x] 后端单测覆盖：正常披露 / 空库 / 窗口内无方向性观点 / 已结算按窗口过滤
- [x] 前端 `IndexCompareResponse` 类型追加可选 `caliber`
- [x] 前端卡片渲染披露行（`data-testid="index-compare-caliber"`），旧响应无该键时不渲染
- [x] 前端单测覆盖：正常披露 / 缺键兼容 / 空态不渲染
- [x] `uv run pytest tests/outcome/test_index_compare.py` 全绿（20 passed）
- [x] `cd frontend && npm test` 相关用例全绿（79 passed）
- [x] `openspec validate add-track-record-caliber-disclosure --strict` 通过
- [x] 人工验证报告落 `tests/validation/`（战绩页实际渲染 + 无回归；Playwright 实抓，截图 `tmp/track-record-caliber.png`）
- [x] 部署（后端容器重建 + 前端重建，2026-11-04 20:50–20:55）
