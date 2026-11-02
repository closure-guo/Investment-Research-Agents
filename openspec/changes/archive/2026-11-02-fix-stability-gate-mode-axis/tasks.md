# Tasks: fix-stability-gate-mode-axis

- [x] 测试先红：`tests/screening/test_screening_store.py` 新增——①日批形态（当日批次行未落库 +
      显式 `mode`）→ 同模式轴生效，fallback 日不稀释（10-27 生产形态做用例：因子口径 3 命中
      入池、混合轴 2 命中不复发）；②非法 `mode` 值 fail loud；③既有「无批次行降级」用例保持
      通过（默认路径兼容不变）
- [x] `store.py`：`days_in_top_recent` / `eligible_for_selection` 增加显式 `mode` 入参
      （提供时作 `as_of` 轴模式、跳过批次行查找；缺省保持既有降级路径；非法值 `ValueError`）
- [x] `pipeline.py` 精选池计算处（`eligible_for_selection` 调用点）传入当日 `selection_mode`
- [x] 全套 screening 测试通过；ruff / mypy 对改动文件零新增
- [x] `openspec validate fix-stability-gate-mode-axis --strict` 通过
- [x] 部署后次一交易日人工核验：池成员与同模式口径逐只一致（人工验证报告落
      `tests/validation/`；通过标准 = 口径一致，非「池变大」）
      ——报告 `tests/validation/2026-11-02-fix-stability-gate-mode-axis-validation.md`：11-02 池 33
      与同模式口径逐只一致（零混入零漏入，复算 vs 生产函数 99/99）；判别证据在 10-28（首个判别日
      actual==同模式 16 ≠ 混合轴 15），10-26/10-27 复现修复前缺陷（actual==混合轴）；10-30 起两轴
      恒等无判别力
- [x] 归档顺序：先归档 `update-stability-gate-rolling-window`，本 delta 归档时按 sync 后
      主规范全文重写 MODIFIED 块（drift 陷阱处置）
