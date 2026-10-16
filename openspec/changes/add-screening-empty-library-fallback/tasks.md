# Tasks: add-screening-empty-library-fallback

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T1 打分器谓词 + 降级排序（ranker.py）

- [x] 先红后绿：`has_scorable_factors(factors, ic_stats)`（无 active 因子 → False；全零 IC → False；
      任一 |IC 均值| ≥ 阈值 → True；非有限 IC → True 交回打分路径 fail loud；与 `factor_weights`
      守卫共用同一阈值常量）
- [x] 先红后绿：`liquidity_fallback_rank(...)`（近 20 日日均成交额降序、同额按 symbol 升序、Top-N
      截断、非有限均值剔除、空输入 → 空列表；`composite_score`=日均成交额、`factor_contribs=()`）

## T2 管道降级分支（pipeline.py）

- [x] 先红后绿：库空 → 批次 ok + `selection_mode=fallback` + L2 名单/池照常产出（池行
      `factor_contribs=[]`）+ 在榜门槛照常累计
- [x] 先红后绿：库有可打分因子 → `selection_mode=factor`、走原打分路径（回归钉死）
- [x] 先红后绿：降级 + L3 网关整体不可用 → 批次 failed、零池（fail-closed 不放宽）
- [x] 降级路径日志明示（运维可判读）

## T3 库层列与迁移（store.py）

- [x] 先红后绿：`selection_mode` 列（幂等迁移：旧库 ALTER 补列默认 `factor`）；`record_screening_batch`
      携带该列；`list_batches` / `latest_screening_batch` / `latest_successful_batch` 回读携带

## T4 API 披露（api.py）

- [x] 先红后绿：`/api/screening/pool` 载荷含 `selection_mode` 与 `notice`（fallback 时非空且含
      「非因子信号」；factor 时 notice=None）
- [x] `/api/screening/batches` 经 store 自动携带；`/api/test/seed` 造数支持 `selection_mode`

## T5 前端（ScreeningPage + types）

- [x] 先红后绿（vitest）：fallback → 降级披露横幅（testid `screening-fallback-banner`，文案含
      「非因子信号」）+ 综合分列标题/数值口径切换；factor → 无横幅（回归钉死）
- [x] `ScreeningPoolResponse` 类型扩展

## T6 E2E（真实后端 + 隔离库，红线：不 mock 业务接口）

- [x] `screening-page.spec.ts` 增降级用例：`/api/test/seed` 造 fallback 批次 + 池 → 页面横幅可见、
      列标题切换（6/6 过）；既有用例（factor 模式）回归绿
- [x] 像素基线（专属 config 8006/5179）：14/14 过——factor 模式渲染零视觉变更

## 收口

- [x] `uv run pytest tests/screening tests/test_api_screening.py -q` 全绿（174）；`ruff check` /
      `mypy` 干净；前端 `tsc --noEmit` + `vitest` 绿
- [x] `openspec validate add-screening-empty-library-fallback --strict` 通过
- [ ] 生产实跑：部署后手动补跑选股日批（ops 通道）→ 批次 ok + `selection_mode=fallback` +
      页面横幅可见 + 池产出（人工验证报告落 `tests/validation/`）
- [ ] 验证报告 + PR；**sync + archive 在实跑验证通过后执行**
