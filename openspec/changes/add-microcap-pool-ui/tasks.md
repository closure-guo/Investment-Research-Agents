# Tasks: add-microcap-pool-ui

- [x] 1. 后端：`latest_successful_pool` / `_screening_pool_payload` 加 pool 参数，池响应透传 `frontier_gate` / `exec_constraints`；`GET /api/screening/pool` 与 `GET /api/screening/batches` 暴露 `pool: Literal["main","microcap"]` 查询参数（缺省 main、非法 422、microcap 空态不报错）；后端测试覆盖缺省不变 / 按档取数 / 非法 422 / 空态（TDD 先红后绿）
- [x] 2. 前端：选股页档位切换（主档/小市值，缺省主档）+ 小市值视图（同构池表 + 门禁横幅两态可区分 + 缺失要点可读文案 + 执行约束建议口径披露 + 「仅观察、不产生订单」声明 + 空态 + 不渲染分析触发）；前端 vitest 覆盖切换/披露/空态/主档不受影响
- [x] 3. E2E：新增交互 spec 覆盖档位切换、门禁两态披露、空态、主档不受影响；stub 种子含 microcap 批次（frontier_gate 两态至少各一例）
- [ ] 4. E2E 门禁全绿（`cd e2e && npx playwright test`）+ 人工验证报告落 `tests/validation/`
