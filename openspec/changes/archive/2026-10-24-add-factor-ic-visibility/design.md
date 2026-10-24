# Design: add-factor-ic-visibility

## Approach

**后端：新增纯函数模块 `src/finance_agent/factors/readiness.py`**，只做两件事：

1. `expected_factor_mode_date(latest_ic_date: str | None, samples: int, min_samples: int = 10) -> str | None`
   - `samples >= min_samples` 或 `latest_ic_date is None` → `None`；
   - 否则从 `latest_ic_date` 起按**工作日**前进 `needed + 2` 天（`needed = min_samples - samples`）。
     解释：最新一条 IC 是在 `latest_ic_date + 1` 个交易日 15:45 写入的；之后每交易日再写 1 条，
     第 `needed` 条落在第 `needed` 个交易日收盘后；15:40 的选股批次要等**下一个**交易日才看到，
     故总共前进 `needed + 2` 个工作日（跳过周六/周日，不含法定节假日）。
2. `factor_signal_payload(factors, ic_samples_by_id, latest_ic_date_by_id, selection_mode, min_samples)`
   - 供池接口组装 `factor_signal` 块；`reason` 依据 `selection_mode` 与逐因子达标情况生成；
   - 批次级 `expected_factor_mode_date` 取各因子达标日的**最早值**。

日期推算不引入交易日历依赖（项目无节假日表），在字段文案与 UI 上统一标注「按工作日推算、不含节假日」，避免把估算冒充精确值。

**API 契约**（只增字段，不改既有字段语义）：

- `GET /api/factors` → 每个因子追加 `ic_window: 60`、`min_samples_for_scoring: 10`、`expected_factor_mode_date: str|null`（`ic_points` 已有）。
- `GET /api/factors/{id}` → 同上；`style_exposure` 行为不变，但 IC 点数 < 30 时**短路返回 null**，不构建准入面板（已实现并纳入规格）。
- `GET /api/screening/pool` → 追加：

```json
"factor_signal": {
  "selection_mode": "fallback",
  "reason": "IC 样本不足（门槛 10）",
  "min_samples": 10,
  "expected_factor_mode_date": "2026-11-02",
  "factors": [
    {"id": 1, "ic_samples": 4, "eligible": false, "expected_eligible_date": "2026-11-02"},
    {"id": 2, "ic_samples": 3, "eligible": false, "expected_eligible_date": "2026-11-03"}
  ]
}
```

`factors` 仅含 `active` 因子、按 `id` 升序；`ic_samples` 为库内 `factor_ic` 实际条数（窗口 60 内的计数与总量取 `COUNT(*)`，与 `ic_sample_counts` 同源）。

**前端**：

- `IcSeriesChart.tsx`（新）：ECharts 折线图，props `{points: {trade_date, rank_ic}[]}`；空数组时展示「暂无 IC 记录」空态；使用项目既有 ECharts 封装与主题令牌。
- `FactorSignalCard.tsx`（新，选股页）：接收 `factor_signal`，三态渲染（无 active 因子 / 未达标 / 已达标）；逐因子进度条用纯 CSS 宽度（`n/10`），不引入新依赖。
- `FactorLibraryPage`：列表 ICIR 单元格追加 `样本 n` 小字；详情区用 `IcSeriesChart` 替换文本列表，并加「样本 4/60 · 门槛 4/10 · 预计 …」一行。
- `ScreeningPage`：在 `MarketRegimeCard` 之后挂 `FactorSignalCard`，数据直接取 `pool` 载荷（`factor_signal`），不额外发请求。

**类型与测试**：

- `frontend/src/types.ts` 增加 `FactorIcReadiness` 与 `ScreeningFactorSignal` 类型，字段与后端逐字对齐。
- 后端 pytest：`tests/factors/test_readiness.py` 覆盖工作日推算（周五跨周末、样本达标、无记录、日期缺失）与 `factor_signal` 组装；`tests/test_api_factors.py` 与 `tests/test_api_screening.py` 增加字段断言。
- 前端 vitest：`factorLibraryPage.test.tsx`（图/样本/门槛/恢复日）、`screeningFactorSignal.test.tsx`（三态）。
- E2E：`tests/e2e/playwright/tests/factor-library-ic.spec.ts` 新增一条真实浏览器用例（进入 /factors → 展开含 IC 的因子 → 折线图与就绪度可见）；`tests/e2e/playwright/tests/screening-factor-signal.spec.ts` 一条（选股页卡片三态之一）。E2E 依赖既有 seed 接口造数，不 mock 业务接口。

## Alternatives Considered

- **前端自己算恢复日期**：需要在 TS 里复制工作日推算与门槛逻辑，两处口径漂移风险高；且日期依赖库内最新 IC 日期，前端拿不到权威值。否决。
- **引入交易日历库（如 exchange_calendars）**：能精确到节假日，但新增依赖、且当前快照库本身用「有数据的工作日」定义交易日；先用工作日近似并在文案标注，等有明确需求再升级。否决（YAGNI）。
- **把 `factor_signal` 做成独立端点**：选股页要多一次请求与加载态，且该数据与池同源（同一批次的 selection_mode），并入池载荷更一致。否决。

## Risks

- **工作日推算遇节假日会偏早**：文案已标注「按工作日推算、不含节假日」；若偏差影响决策，再接入交易日历。
- **`expected_factor_mode_date` 依赖 IC 采集不漏跑**：漏一天即顺延一天；这与既有「IC 采集无补洞」缺陷同源，本变更不修采集侧，只在 UI 如实展示。
- **ECharts 在 vitest/jsdom 不可渲染**：测试用 `echarts` 的 mock 或断言容器与数据 prop（不依赖 canvas 像素），E2E 负责真实渲染。
- **详情接口未来仍可能变慢**：IC 点数 ≥30 后风格暴露会重新构建 500 日面板；本变更只保证「不足 30 时不构建」，长期方案（批量读面板/预计算）留待单独变更。
