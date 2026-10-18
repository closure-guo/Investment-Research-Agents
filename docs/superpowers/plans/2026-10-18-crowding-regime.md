# Crowding Regime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付「拥挤度指标 + 总仓位系数」：日批四指标落库与历史分位、确定性 regime 判定与配置、执行链组合聚合消费系数（含 0 清仓目标与缺失披露式降级）、只读端点 `GET /api/regime`、选股页温度计区块与 E2E 覆盖。

**Architecture:** 新包 `outcome/regime/`（`indicators` / `judgment` / `config` / `model` / `job` / `payload` 单职责小文件；纯函数优先）；调度经 `outcome/scheduler.py` + `ops/jobs.py` 四件套；执行链在 `execution/portfolio.py::aggregate_targets` 加系数参数、`execution/driver.py` 读取并记摘要；前端独立卡片 `MarketRegimeCard.tsx` 挂选股页；E2E 默认套件 + `REGIME_DB_PATH` 隔离注入。

**Tech Stack:** Python 3.12 / SQLite / APScheduler（既有）/ FastAPI / pytest；React 18 + TS + ECharts + vitest；Playwright（默认 stub 套件）。

## Global Constraints

- 工作目录 = `D:\WorkSpace\finance_analysis_agent\.worktrees\personal-edge-program`（分支 `feat/personal-edge-program`）。**不得触碰主检出**与其他变更的文件。
- 规范：`openspec/changes/add-crowding-regime-positioning/`（spec.md 合同 + design.md 口径，binding）。
- 口径先登记 `docs/evals/metrics.md` §1（§1.14），再动代码。
- 判定链路 MUST NOT 调用 LLM；同输入同输出。
- 配置范式逐字照抄 `execution/config.py`：缺文件回落内置默认 + WARN；损坏（JSON 非法/缺键/类型不符/系数越界 [0,1]）→ `ValueError` fail loud。
- `aggregate_targets` 默认调用行为（不传系数）**逐字节不变**——既有测试必须全绿。
- 缺数据一律 null/不产行 + 原因码（`cap_data_missing` / `insufficient_history` / `no_snapshot_data` / `no_regime_data`），MUST NOT 折算 0 或静默。
- 调度计数断言同步改：`tests/outcome/test_scheduler.py`（两处 `len(ids)==14` → 15）与 `tests/outcome/test_ops_jobs.py`（四处 `len(JOB_IDS)==14` → 15）。
- E2E 红线：不 mock 业务接口；新 spec 落默认套件；`REGIME_DB_PATH` 在 `playwright.config.ts` 与 `playwright.isolated.config.ts` 两处注隔离路径；`.gitignore` 补 `data/regime.db*`、`data/test-e2e-regime.db*`。
- TDD：先红后绿；提交信息 `feat(regime): ...` 风格，中文。

---

### Task 1: 口径登记（metrics.md §1.14）

**Files:** Modify `docs/evals/metrics.md`（§1 末尾追加）

- [ ] **Step 1: 追加小节**（与 design.md 一致）：

```markdown
### 1.14 市场拥挤度与总仓位系数（delta `add-crowding-regime-positioning`，2026-10-18 登记）

**指标**：小市值组成交额占比（按流通市值升序前 30%，停牌/零成交剔除；流通市值与快照任务同源 spot 取数，
全量覆盖缺失即当日不产行）/ 涨跌家数（pct_chg 符号）/ 涨停跌停家数（limit_up_sealed / limit_down_sealed）/
全市场成交额。**分位**：当日值在最近 250 交易日窗口内的百分位（`#{x≤v}/n`）；样本 < 60 → null + `insufficient_history`。
**判定**：触发指标 = 小市值占比分位、成交额分位；任一 ≥ danger 阈值（默认 0.9）→ danger、≥ elevated（默认 0.8）→ elevated、
否则 normal；系数映射 1.0 / 0.5 / 0.0（全部可配置）；分位不可用不触发且在 reasons 注明。**陈旧**：≥ 7 自然日（可配置）。
**执行链**：目标权重 × 系数；系数 ≠ 1.0 逐票 `REGIME_SCALE`，摘要记状态与系数；缺失/陈旧 → 1.0 + `no_regime_data`。
```

- [ ] **Step 2: Commit** `docs(regime): metrics.md §1.14 登记拥挤度与总仓位系数口径`

### Task 2: 指标与判定纯函数 + 配置加载

**Files:**
- Create: `src/finance_agent/outcome/regime/__init__.py`、`indicators.py`、`judgment.py`、`config.py`
- Test: `tests/outcome/test_regime_indicators.py`、`tests/outcome/test_regime_judgment.py`

**Interfaces（后续任务按此签名消费）:**
- `indicators.compute_indicators(universe: Sequence[dict], caps_by_code: Mapping[str, float]) -> dict | None`
  → `{"small_cap_share","up_count","down_count","flat_count","limit_up_count","limit_down_count","total_amount"}`；`universe` 空或 caps 对（停牌/零成交剔除后）有一缺 → `None`（原因由调用方记 `cap_data_missing`）。
- `indicators.percentile_rank(values: Sequence[float], value: float, *, min_history: int = 60) -> float | None`
- `indicators.SMALL_CAP_QUANTILE = 0.3 / HISTORY_WINDOW = 250 / MIN_HISTORY = 60`
- `config.RegimeConfig`（dataclass：`elevated_threshold=0.8, danger_threshold=0.9, multiplier_normal=1.0, multiplier_elevated=0.5, multiplier_danger=0.0, small_cap_quantile=0.3, history_window=250, min_history=60, stale_days=7`）
- `config.load_regime_config(path: str | Path | None = None) -> RegimeConfig`（env `REGIME_CONFIG_PATH`；默认 `config/regime.json`；缺文件回落 + WARN；损坏 `ValueError`）
- `judgment.judge(row: Mapping | None, config: RegimeConfig) -> dict` → `{"state","multiplier","reasons"}`（row None → `{"state":"normal","multiplier":1.0,"reasons":["no_regime_data"]}`）
- `judgment.staleness(trade_date: str, *, now: date | None = None, stale_days: int = 7) -> tuple[int, bool]`

- [ ] **Step 1: 写失败测试**（关键用例，实施者补齐 helper）：

```python
def test_small_cap_group_and_share():
    universe = [
        {"code": "A", "amount": 10.0, "pct_chg": 1.0, "is_suspended": 0, "limit_up_sealed": 0, "limit_down_sealed": 0},
        {"code": "B", "amount": 30.0, "pct_chg": -1.0, "is_suspended": 0, "limit_up_sealed": 0, "limit_down_sealed": 0},
        {"code": "C", "amount": 60.0, "pct_chg": 0.0, "is_suspended": 1, "limit_up_sealed": 0, "limit_down_sealed": 0},
        {"code": "D", "amount": 100.0, "pct_chg": 9.9, "is_suspended": 0, "limit_up_sealed": 1, "limit_down_sealed": 0},
    ]
    caps = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 10.0}
    out = compute_indicators(universe, caps)
    # 剔除停牌 C；剩余 A/B/D 按 cap 升序前 30% → A（ceil(3*0.3)=1 只）
    assert out["small_cap_share"] == pytest.approx(10.0 / 200.0)
    assert (out["up_count"], out["down_count"], out["flat_count"]) == (2, 1, 1)  # C 停牌仍计入涨跌家数? -> 决定：停牌剔除于涨跌家数
    ...
```

> ⚠️ 实施前裁决（按此实现，不要再问）：**停牌标的从全部四个指标中剔除**（含涨跌家数）；`pct_chg == 0` 计「平」；caps 缺失（任一非停牌标的无 cap）→ 返回 `None`。

- [ ] **Step 2: 跑失败** → `uv run pytest tests/outcome/test_regime_indicators.py -v`
- [ ] **Step 3: 实现** `indicators.py` / `config.py` / `judgment.py`；判定规则按 Global Constraints；`reasons` 形如 `["small_cap_share_pctile=0.93>=0.9"]`；分位 None 时 reason `insufficient_history:total_amount_pctile`。
- [ ] **Step 4: 跑绿**（含 judgment 测试：three 态 / 阈值边界 0.8、0.9 两侧 / 缺文件回落 + WARN / 损坏 `ValueError` / 系数越界 `ValueError` / 同输入同输出）+ 提交 `feat(regime): 指标与判定纯函数 + 配置加载（先红后绿）`

### Task 3: 存储层（`regime.db`）

**Files:**
- Create: `src/finance_agent/outcome/regime/model.py`
- Test: `tests/outcome/test_regime_model.py`

**Interfaces:**
- `model.init_regime_db(db_path=None) -> Path`；`_default_db_path()` 调用期读 env `REGIME_DB_PATH`（默认 `data/regime.db`）
- `model.upsert_regime_daily(trade_date, indicators: Mapping, pctiles: Mapping, *, computed_at, db_path=None) -> None`（trade_date 唯一，重跑覆盖）
- `model.latest_regime(db_path=None) -> dict | None`；`model.list_regime_history(limit=250, db_path=None) -> list[dict]`

- [ ] **Step 1: 失败测试**：upsert 幂等（同日双写仍单行、值覆盖）；`latest_regime` 排序；空库 → None（先红后绿）
- [ ] **Step 2: 实现 + 跑绿 + 提交** `feat(regime): regime_daily 存储层（幂等 upsert）`

### Task 4: 日批 job + 调度注册

**Files:**
- Create: `src/finance_agent/outcome/regime/job.py`
- Modify: `src/finance_agent/outcome/scheduler.py`、`src/finance_agent/outcome/ops/jobs.py`、`tests/outcome/test_scheduler.py`、`tests/outcome/test_ops_jobs.py`
- Test: `tests/outcome/test_regime_job.py`

**Interfaces:**
- `job.run_regime_daily(as_of=None, *, db_path=None, snapshot_db_path=None, client=None) -> dict`
  流程：`latest_snapshot_date` →（无 → `{"produced": False, "skipped_reason": "no_snapshot_data"}`）→ `client.fetch_market_spot()` 建 caps（含 `流通市值`；源/列缺失 → `cap_data_missing`）→ `read_universe(as_of)` → `compute_indicators` → `percentile_rank`（以 `list_regime_history` 为窗口）→ `upsert_regime_daily` → `{"produced": True, "trade_date": ...}`。
- 四件套：`JOB_IDS` 在 `market_snapshot_daily` 之后插入 `"regime_daily"`（顺序=展示序=时刻序）；`SCHEDULES["regime_daily"]` 取 15:35（按文件内现有 schedule 形状填写）；scheduler 加 `_regime_daily_job` wrapper（照抄 `_paper_execute_job` 形态）。

- [ ] **Step 1: 失败测试**：job 三态（正常产行 / 快照缺 → 不产行 + reason / caps 缺 → 不产行 + reason）；幂等重跑；调度计数断言 14→15 同步更新
- [ ] **Step 2: 跑失败 → 实现 → 跑绿**（`uv run pytest tests/outcome/test_regime_job.py tests/outcome/test_scheduler.py tests/outcome/test_ops_jobs.py -q`）
- [ ] **Step 3: 提交** `feat(regime): 日批 job 与调度注册（15 任务）`

### Task 5: 执行链消费系数

**Files:**
- Modify: `src/finance_agent/execution/portfolio.py`、`src/finance_agent/execution/driver.py`
- Test: `tests/execution/test_portfolio.py`（存在则追加）、`tests/execution/test_driver.py`

**Interfaces:**
- `aggregate_targets(signals, *, method="equal", single_cap=..., total_cap=..., regime_multiplier: float = 1.0) -> list[TargetPosition]`
  — 在总仓缩减之后应用：`weight *= regime_multiplier`；`regime_multiplier != 1.0` 时逐票 append `ADJUSTMENT_REGIME_SCALE = "REGIME_SCALE"`；默认 1.0 行为零变化（含不追加调整码）。
- `driver.run_execution_daily(..., regime_db_path=None)`：聚合前读取 `latest_regime` + `judge` + `staleness`；`summary["regime"] = {"state","multiplier","source_date"}`，缺失/陈旧 → `{"state": None, "multiplier": 1.0, "source_date": None, "no_regime_data": True}`；系数与状态透传 `_plan_long_entries` → `aggregate_targets`。

- [ ] **Step 1: 失败测试**：portfolio——系数 0.5 缩放 + `REGIME_SCALE`；系数 0 → 全 0 权重；系数 1.0 与不传时逐项一致且无调整码。driver——有 regime 行（elevated 0.5）摘要含状态与系数、逐票含 `REGIME_SCALE`；无行/陈旧 → `no_regime_data` 且结果与基线一致
- [ ] **Step 2: 跑失败 → 实现 → 跑绿**（`uv run pytest tests/execution/ -q`——**既有执行测试必须全绿**）
- [ ] **Step 3: 提交** `feat(regime): 执行链总仓位系数（缺失披露式降级）`

### Task 6: 只读端点 + payload

**Files:**
- Create: `src/finance_agent/outcome/regime/payload.py`
- Modify: `src/finance_agent/api.py`
- Test: `tests/test_api_regime.py`

**Interfaces:**
- `payload.build_regime_payload(db_path=None, config=None) -> dict`
  → `{"available", "reason"?, "as_of"?, "state"?, "multiplier"?, "reasons"?, "indicators"?, "history"?, "age_days"?, "stale"?}`；无记录 → `available=False, reason="missing"`；配置损坏 → `available=False, reason="corrupt"`（端点 200，不 500）。
- `GET /api/regime` → `await asyncio.to_thread(build_regime_payload)`。

- [ ] **Step 1: 失败测试**：有记录（含 history、age_days/stale 7 日两侧）；空库 missing；config 损坏 corrupt；只读零写入
- [ ] **Step 2: 实现 → 跑绿 → 提交** `feat(regime): GET /api/regime 只读端点（先红后绿）`

### Task 7: 前端选股页温度计区块

**Files:**
- Create: `frontend/src/pages/screening/MarketRegimeCard.tsx`
- Modify: `frontend/src/pages/screening/ScreeningPage.tsx`（header 与滚动容器之间插入）、`frontend/src/types.ts`
- Test: `frontend/src/test/screeningMarketRegime.test.tsx`

**Interfaces:** testid：`market-regime-card`、`regime-state-badge`、`regime-multiplier`、`regime-metrics`、`regime-chart`、`regime-reasons`、`regime-stale-notice`、`regime-empty`。三态徽章用**组件内本地实现**（`StatusKey` 不含 normal/elevated/danger，勿扩展设计令牌）；图表复用 `ReactECharts` + `getChartTheme()`（先例 `pages/broker/BrokerConsolePage.tsx`）。

- [ ] **Step 1: vitest 失败测试**：三态渲染（徽章/系数/四指标/理由）/ 曲线渲染 / → `stale=true` 陈旧披露 / `available=false` 空态（missing 与 corrupt 文案区分）/ 无控制件
- [ ] **Step 2: 跑失败 → 实现 → `npx tsc --noEmit` + vitest 绿 → 提交** `feat(regime): 选股页市场温度计区块（先红后绿）`

### Task 8: E2E（默认套件）

**Files:**
- Create: `tests/e2e/playwright/tests/market-regime.spec.ts`
- Modify: `tests/e2e/playwright/playwright.config.ts`、`tests/e2e/playwright/playwright.isolated.config.ts`（env +`REGIME_DB_PATH=data/test-e2e-regime.db`）、`src/finance_agent/api.py`（TESTING seed 增 `regime` 块）、`.gitignore`

- [ ] **Step 1: seed 扩展**（先读 `/api/test/seed` 实现，按既有块风格加 `regime: [rows]`，写 `REGIME_DB_PATH` 库）
- [ ] **Step 2: spec**：造数（含 elevated 行）→ `/screening` → 断言 `regime-state-badge`/`regime-multiplier`/指标卡可见；空态一条（默认套件里第二个用例清理后或独立 fixture）。跑：
  `cd tests/e2e/playwright && npx playwright test market-regime` → 迭代至绿。**红线：不 mock `/api/regime`。**
- [ ] **Step 3: 提交** `test(regime): 默认套件 E2E 覆盖温度计区块与空态`

### Task 9: 收口验证

- [ ] `uv run pytest tests/outcome/ tests/execution/ tests/test_api_regime.py -q` 全绿；`uv run ruff check` 改动文件干净
- [ ] `uv run mypy src/finance_agent/outcome/regime/` 新包零新增
- [ ] 前端 `npx tsc --noEmit` + `npm test` 全绿
- [ ] `openspec validate add-crowding-regime-positioning --strict` 通过
- [ ] 报告状态（DONE / DONE_WITH_CONCERNS）
