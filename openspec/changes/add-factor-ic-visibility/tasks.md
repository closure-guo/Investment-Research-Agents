## 1. 后端就绪度纯函数

- [x] 1.1 新增 `src/finance_agent/factors/readiness.py`：`expected_factor_mode_date`（工作日推算）+ `factor_signal_payload`（池接口组装）
- [x] 1.2 新增 `tests/factors/test_readiness.py`：周五跨周末、样本达标、无 IC 记录、日期缺失、批次最早值五类用例先红后绿

## 2. 后端接口字段

- [x] 2.1 `/api/factors` 与 `/api/factors/{id}` 追加 `ic_window` / `min_samples_for_scoring` / `expected_factor_mode_date`，并补测试
- [x] 2.2 `/api/screening/pool` 追加 `factor_signal` 块（逐 active 因子进度 + 批次原因 + 最早恢复日），并补测试

## 3. 前端因子库页

- [x] 3.1 新增 `IcSeriesChart.tsx`（ECharts 折线 + 空态）并接入 `FactorLibraryPage` 详情
- [x] 3.2 列表 ICIR 旁注样本数、详情展示 `样本 n/60` 与 `门槛 n/10`、预计恢复日；样本不足显式标注
- [x] 3.3 `factorLibraryPage.test.tsx` 增加图/样本/门槛/恢复日断言（先红后绿）

## 4. 前端选股页

- [x] 4.1 新增 `FactorSignalCard.tsx`（无因子 / 未达标 / 已达标三态）并挂载到 `ScreeningPage`
- [x] 4.2 `types.ts` 增加 `FactorIcReadiness` / `ScreeningFactorSignal`，与后端字段逐字对齐
- [x] 4.3 `screeningFactorSignal.test.tsx` 三态断言（先红后绿）

## 5. 门禁与验证

- [x] 5.1 新增 E2E：`factor-library.spec.ts` 扩展（真实浏览器展开因子看折线图与就绪度）
- [x] 5.2 新增 E2E：`screening-page.spec.ts` 增加因子信号卡用例（真实浏览器可见）
- [x] 5.3 `uv run pytest` / `npm test` / `npx playwright test` 全绿
- [ ] 5.4 人工验证报告落 `tests/validation/2026-10-23-add-factor-ic-visibility-validation.md`
