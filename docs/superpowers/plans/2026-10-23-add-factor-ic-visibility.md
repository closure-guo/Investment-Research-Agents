# Factor IC Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在因子库页与选股页如实展示每个因子的 IC 样本进度（x/60 窗口、x/10 门槛）、IC 折线图与预计恢复因子打分日期。

**Architecture:** 后端新增纯函数 `factors/readiness.py` 负责工作日推算与 `factor_signal` 组装，`/api/factors` 与 `/api/screening/pool` 只增字段；前端复用 ECharts 画折线、内联进度条画样本，两页各自消费已有接口，不新增请求。

**Tech Stack:** Python 3.12 / FastAPI / pytest；React 18 / TS / ECharts / vitest；Playwright E2E。

## Global Constraints

- 日期口径：按工作日推算、**不含法定节假日**，字段与 UI 文案必须标注。
- 不改既有字段语义；`style_exposure` 不足 30 点时返回 `null` 且不得构建准入面板。
- 无新增第三方依赖（ECharts 已在用）。
- 交互类变更：E2E 不得 mock 业务接口；人工验证报告须落 `tests/validation/`。

---

### Task 1: readiness 纯函数

**Files:**
- Create: `src/finance_agent/factors/readiness.py`
- Test: `tests/factors/test_readiness.py`

**Interfaces:**
- Produces: `expected_factor_mode_date(latest_ic_date: str | None, samples: int, min_samples: int) -> str | None`；`factor_signal_payload(factors, ic_samples, latest_ic_dates, *, selection_mode, min_samples) -> dict`

- [ ] **Step 1: 写失败测试**（周五跨周末：2026-10-21 + 6 needed → 2026-11-02；达标→None；无日期→None；无因子→reason 无 active）
- [ ] **Step 2: 跑测试确认失败** `pytest tests/factors/test_readiness.py -q`
- [ ] **Step 3: 实现纯函数**（`needed + 2` 个工作日推算，见 design.md）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** `feat(factors): IC 就绪度纯函数（样本→恢复日期）`

### Task 2: 因子接口字段

**Files:**
- Modify: `src/finance_agent/api.py`（`_factor_summary_payload`、`factor_library_list`、`_factor_detail_payload`）
- Test: `tests/test_api_factors.py`

**Interfaces:**
- Produces: 列表/详情每项含 `ic_window`、`min_samples_for_scoring`、`expected_factor_mode_date`。

- [ ] **Step 1: 测试**：种子因子（2 条 IC）→ `ic_points=2`、`min_samples_for_scoring=10`、`ic_window=60`、`expected_factor_mode_date` 非空且为工作日
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**：summary payload 接收 `latest_ic_date`，调用 `expected_factor_mode_date`
- [ ] **Step 4: 通过**
- [ ] **Step 5: 提交**

### Task 3: 池接口 `factor_signal`

**Files:**
- Modify: `src/finance_agent/api.py`（`_screening_pool_payload`）
- Test: `tests/test_api_screening.py`

**Interfaces:**
- Produces: `pool.factor_signal = {selection_mode, reason, min_samples, expected_factor_mode_date, factors:[{id, ic_samples, eligible, expected_eligible_date}]}`

- [ ] **Step 1: 测试**：monkeypatch `list_factors` 返回两 active 因子 + `ic_sample_counts`，断言逐因子进度与最早恢复日
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**（仅 active，按 id 升序）
- [ ] **Step 4: 通过**
- [ ] **Step 5: 提交**

### Task 4: 因子库页 IC 图与就绪度

**Files:**
- Create: `frontend/src/pages/factors/IcSeriesChart.tsx`
- Modify: `frontend/src/pages/factors/FactorLibraryPage.tsx`、`frontend/src/types.ts`
- Test: `frontend/src/test/factorLibraryPage.test.tsx`

- [ ] **Step 1: 写失败测试**（详情渲染 `data-testid="factor-ic-chart"`、`factor-ic-samples` 文案含 `4/60`、`factor-ic-threshold` 含 `4/10`、`factor-ic-expected` 含日期；列表 `factor-icir-*` 旁含 `样本 2`）
- [ ] **Step 2: 确认失败** `npm test -- factorLibraryPage`
- [ ] **Step 3: 实现组件与页面接入**
- [ ] **Step 4: 通过 + `npx tsc --noEmit`**
- [ ] **Step 5: 提交**

### Task 5: 选股页因子信号卡

**Files:**
- Create: `frontend/src/pages/screening/FactorSignalCard.tsx`
- Modify: `frontend/src/pages/screening/ScreeningPage.tsx`、`frontend/src/types.ts`
- Test: `frontend/src/test/screeningFactorSignal.test.tsx`

- [ ] **Step 1: 写失败测试**（三态：无因子 / 未达标显示 `4/10` 与恢复日 / 达标显示「因子信号已恢复」）
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现并挂到 `MarketRegimeCard` 之后**
- [ ] **Step 4: 通过 + tsc**
- [ ] **Step 5: 提交**

### Task 6: E2E 与验证

**Files:**
- Create: `tests/e2e/playwright/tests/factor-library-ic.spec.ts`、`tests/e2e/playwright/tests/screening-factor-signal.spec.ts`
- Create: `tests/validation/2026-10-23-add-factor-ic-visibility-validation.md`

- [ ] **Step 1: 写 E2E（真实浏览器、seed 接口造数）**
- [ ] **Step 2: `cd e2e && npx playwright test factor-library-ic screening-factor-signal` 全绿**
- [ ] **Step 3: 全量回归 `uv run pytest`、`npm test`**
- [ ] **Step 4: 部署后端并人工验证，报告落 tests/validation/**
- [ ] **Step 5: 提交**
