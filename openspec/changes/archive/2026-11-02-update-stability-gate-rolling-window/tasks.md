# Tasks: update-stability-gate-rolling-window

- [x] `store.py`：新增 `days_in_top_recent`（同模式滚动命中计数，LEFT JOIN 宽待无批次行日子、
      `as_of` 无模式退回全轴、当日不在榜 → 0）+ 改写 `eligible_for_selection`（滚动门槛，
      `min_days` 可配置语义不变）；`consecutive_days_in_top` 原语义保留（信息列）
- [x] 测试先红后绿：`tests/screening/test_screening_store.py` 新增 7 例（同模式窗口 / 换挡不清零 /
      单日跌出 / 门槛不足 / 窗口滚动 / 当日不在榜 / 无批次行降级）；旧契约断言「断档重新计数
      → 不进池」随 delta 翻转为「4/5 命中 → 进池」
- [x] 全套 screening 测试 102 passed；ruff / mypy 对改动文件零新增
- [x] `openspec validate update-stability-gate-rolling-window --strict` 通过
- [x] 合并后在生产观察 11-02 因子模式恢复日：池子不清零（人工验证项，随部署记录）
      ——**前提日期漂移**：生产实际因子模式恢复日为 10-26（10-21~10-23 为 fallback），非 11-02。
      观察到的池轨迹 10-26 2 → 10-27 2 → 10-28 16 → 10-29 25 → 10-30 35 → 11-02 33：10-26/27 的
      「2」是同模式历史稀疏（仅 3~4 个 factor 日可用）叠加混合轴缺陷（#500 修复前），非清零；
      #500 修复后无任何 factor 日清零。证据与逐日两轴复算见
      `tests/validation/2026-11-02-fix-stability-gate-mode-axis-validation.md`
