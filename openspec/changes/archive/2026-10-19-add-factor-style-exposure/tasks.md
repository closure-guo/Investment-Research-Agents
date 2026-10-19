# Tasks: add-factor-style-exposure

## T1 诊断模块（factors/style_exposure.py）+ 单测
- [x] 篮子 = builtin 注册包同一真源；同表达式 → ρ≈1 + flagged；样本不足/常量 → 全 None（不臆造 0）

## T2 详情端点接线（api.py）+ 用例隔离修复
- [x] 详情 `style_exposure` 字段；面板不可得 → None（诚实）
- [x] tests/test_api_factors.py 注入 SNAPSHOT_DB_PATH（修复：改造前测试触碰生产快照库）

## T3 前端披露块 + 单测 + E2E
- [x] 展开块：主导风格 + 逐风格读数 + 重合标注 / 数据不足两态
- [x] 单测覆盖两态；E2E spec 断言块存在与两态之一

## T4 运维脚本（scripts/factor_style_exposure.py）
- [x] 批诊断矩阵输出；只读；容器内推荐用法随库 docstring

## T5 验证报告
- [x] `tests/validation/2026-10-19-add-factor-style-exposure-validation.md`（实测矩阵 + 立场 + 决策项）

## 收口
- [x] `uv run pytest tests/factors tests/test_api_factors.py` 全绿；前端 build/单测绿；ruff（改动面）干净
- [x] `openspec validate add-factor-style-exposure --strict` 通过；E2E 门禁走 CI stub
- [x] PR + sync + archive（验证通过后执行）
