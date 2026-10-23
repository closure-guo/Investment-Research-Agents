# Tasks: update-stability-gate-rolling-window

- [x] `store.py`：新增 `days_in_top_recent`（同模式滚动命中计数，LEFT JOIN 宽待无批次行日子、
      `as_of` 无模式退回全轴、当日不在榜 → 0）+ 改写 `eligible_for_selection`（滚动门槛，
      `min_days` 可配置语义不变）；`consecutive_days_in_top` 原语义保留（信息列）
- [x] 测试先红后绿：`tests/screening/test_screening_store.py` 新增 7 例（同模式窗口 / 换挡不清零 /
      单日跌出 / 门槛不足 / 窗口滚动 / 当日不在榜 / 无批次行降级）；旧契约断言「断档重新计数
      → 不进池」随 delta 翻转为「4/5 命中 → 进池」
- [x] 全套 screening 测试 102 passed；ruff / mypy 对改动文件零新增
- [x] `openspec validate update-stability-gate-rolling-window --strict` 通过
- [ ] 合并后在生产观察 11-02 因子模式恢复日：池子不清零（人工验证项，随部署记录）
