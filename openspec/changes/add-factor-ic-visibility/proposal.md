## Why

选股降级为 fallback 时，用户只能看到「降级」两个字，看不到每个因子积累了几条 IC、离 10 条打分门槛还差多少、预计哪天恢复因子打分；因子库页的 IC 也只有一行文本，无法判断走势。2026-10-22 的实际排查中，这个盲区直接导致「因子信号不达标算不算 bug」无法自证。

## What Changes

- 因子库只读接口新增「IC 就绪度」字段：`ic_samples`（已有 `ic_points`）、`min_samples_for_scoring`、`ic_window`、`expected_factor_mode_date`（按工作日推算，跳过周末，不含节假日）。
- 选股池接口新增 `factor_signal` 块：逐 active 因子的样本数 / 是否达标 / 预计达标日，以及批次 `selection_mode` 的原因与「预计恢复因子打分」日期。
- 因子库页：IC 序列由纯文本改为折线图（ECharts），并展示样本 `x/60`、门槛 `x/10`、预计恢复日期；列表 ICIR 列旁标注样本数，样本不足时明确提示。
- 选股页：新增「因子信号状态」卡片（逐因子进度、门槛、降级原因、预计恢复日），空态/降级态/达标态三态如实披露。
- 详情接口在 IC 点数不足风格暴露门槛时直接返回 `style_exposure: null`，不再触发 500 日面板构建（已修，纳入规格约束）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `factor-library`: 「因子库只读查询」增加 IC 就绪度字段与「不足门槛时不得构建风格暴露面板」的约束。
- `stock-screening`: 「无可打分因子降级通道」增加 `factor_signal` 载荷与预计恢复日期披露。
- `frontend`: 「因子库页」增加 IC 折线图与样本/门槛/恢复日；「选股页」增加因子信号状态卡片。

## Impact

- 后端：`src/finance_agent/api.py`（`/api/factors`、`/api/factors/{id}`、`/api/screening/pool`）、新增 `src/finance_agent/factors/readiness.py`（纯函数日期推算）。
- 前端：`frontend/src/pages/factors/FactorLibraryPage.tsx`、`frontend/src/pages/screening/ScreeningPage.tsx`、新增 `IcSeriesChart.tsx` / `FactorSignalCard.tsx`、`frontend/src/types.ts`。
- 测试：后端 pytest（就绪度纯函数 + 接口字段）、前端 vitest、Playwright E2E（因子库页 + 选股页）。
- 无数据库迁移、无新依赖（ECharts 已在用）。
