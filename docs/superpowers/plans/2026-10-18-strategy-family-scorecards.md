# Strategy Family Scorecards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付「策略族成绩单」：按族（观点线 `predictions`、执行线 `execution`）输出四槽读数（净期望/最大回撤/容量提示/极端样本），含只读端点 `GET /api/scorecards`、战绩页区块与 E2E 覆盖。

**Architecture:** 新增纯计算模块 `outcome/scorecards.py`（注册表驱动；数据访问用薄查询函数，DB 路径调用期读 env）；`api.py` 加只读端点（`asyncio.to_thread` 包同步 IO）；前端在战绩页挂独立卡片组件 `StrategyScorecardCard.tsx`（仿 `IndexCompareCard.tsx` 的 fetch-in-component 模式）；E2E 走 track-record 专属套件（seed 注入 predictions + paper_equity）。

**Tech Stack:** Python 3.12 / SQLite / FastAPI / pytest；React 18 + TS + vitest；Playwright（track-record suite）。

## Global Constraints

- 工作目录 = `D:\WorkSpace\finance_analysis_agent\.worktrees\personal-edge-program`（git worktree，分支 `feat/personal-edge-program`）。**不要在任何其他目录改代码**。
- 规范：`openspec/changes/add-strategy-family-scorecards/`（spec.md 是行为契约，design.md 是口径明细——两者已定，实现必须逐条对得上）。
- 口径先登记：Task 1 必须先改 `docs/evals/metrics.md` §1，再动代码（AGENTS.md「口径变更先改 §1」）。
- **TDD 铁律**：每个任务先写失败测试 → 跑出失败 → 最小实现 → 跑绿 → 提交。没有先写失败测试的代码 = 删除重写。
- 缺数据一律 `null` + 原因码，**MUST NOT 折算 0**；`insufficient_sample`（样本 < 10）/ `insufficient_history`（回撤结算日 < 20）/ `no_liquidity_data`（容量全缺）。
- 端点/页面 MUT NOT 把胜率置于主位（四槽在前、胜率在后；dict 插入序即断言对象）。
- 既有测试全绿是硬门槛：`uv run pytest` 相关文件、`ruff check`、新文件 mypy 零新增；前端 `npx tsc --noEmit` + `npm test`。
- 提交信息：`feat(scorecards): ...` / `docs(scorecards): ...`，中文描述与仓库风格一致。

---

### Task 1: 口径登记（docs/evals/metrics.md §1）

**Files:**
- Modify: `docs/evals/metrics.md`（§1 末尾追加小节；不改既有条目）

**Interfaces:**
- Produces: 「策略族成绩单」口径文本（后续任务的数值断言以此为准）。

- [ ] **Step 1: 在 `docs/evals/metrics.md` §1 末尾（§1.12 之后）追加小节**

```markdown
### 1.13 策略族成绩单（delta `add-strategy-family-scorecards`，2026-10-18 登记）

**族定义**：观点线 `predictions`（`status ∈ {resolved_win, resolved_loss, resolved_neutral}`，与既有 prediction_stats 分母一致）
／执行线 `execution`（`paper_equity` 净值派生，已含撮合费模型）。

**四槽口径**：
1. **期望值**：毛 = 样本收益均值（观点线 = `raw_return`；执行线 = 组合级日收益均值）；净 = 毛 − 往返成本估算率
   （= `max(commission_rate × 100_000, min_commission)/100_000 × 2 + stamp_tax_rate + 中档 slippage_rate × 2`，
   参数同源 `config/matching_engine.json`，默认 ≈ 0.30%）；执行线毛=净（已含费）。样本 < 10 → null + `insufficient_sample`。
2. **最大回撤**：按日期聚合样本等权收益（`resolved_at` 前 10 字符）→ 复利净值 → `MDD = 1 − min(nav/峰值)`（输出 ≤ 0）；
   执行线直接用净值序列。结算日样本 < 20 → null + `insufficient_history`。
3. **容量提示**：最近 20 个样本标的（观点线 = 最近判定 symbol；执行线 = 当前持仓）近 20 交易日日均成交额（快照 `amount`）
   × 参与率 1% 的最小值 → 建议资金上限；缺数据标的剔除计数，全缺 → null + `no_liquidity_data`。
4. **极端样本**：观点线 `raw_return ≤ -0.15`、执行线组合日收益 ≤ -0.05（具名常量）；清单含 ticker/日期/收益。

**胜率定位**：`win_rate` 保留为展示字段（四槽之后）；MUST NOT 作为任何自动处置/门禁/告警输入。
```

- [ ] **Step 2: 提交**

```bash
git add docs/evals/metrics.md
git commit -m "docs(scorecards): metrics.md §1 登记策略族成绩单口径（先登记后动代码）"
```

### Task 2: scorecards 纯计算模块

**Files:**
- Create: `src/finance_agent/outcome/scorecards.py`
- Test: `tests/outcome/test_scorecards.py`

**Interfaces:**
- Consumes: `finance_agent.execution.config.load_matching_config`（兜底默认）；`finance_agent.screening.constraints`（无——不新增常量到此）；`finance_agent.data.snapshot.read_bars / latest_snapshot_date`；`finance_agent.outcome.track_record.model._default_db_path`（predictions 库）；`finance_agent.execution.store.list_positions / _default_db_path`。
- Produces（后续任务消费的确切签名）：
  - `FAMILY_REGISTRY: tuple[FamilySpec, ...]`（`FamilySpec(family_id: str, name: str, extreme_threshold: float)`）
  - `estimate_round_trip_cost_rate(config=None) -> float`
  - `max_drawdown_from_returns(daily_returns: Sequence[float]) -> float`（输出 ≤ 0）
  - `aggregate_daily_returns(samples: Sequence[Sample]) -> list[float]`（按日等权、升序）
  - `build_family_payload(spec: FamilySpec, samples: Sequence[Sample], *, contract: FamilyContract, capacity_inputs: dict[str, float] | None = None) -> dict`
  - `get_scorecards(*, sessions_db_path=None, execution_db_path=None, snapshot_db_path=None) -> dict`
  - `Sample = NamedTuple("Sample", [("ticker", str), ("date", str), ("ret", float)])`
  - `FamilyContract = NamedTuple("FamilyContract", [("win_rate", float | None), ("caliber_note", str)])`

- [ ] **Step 1: 写失败测试**（`tests/outcome/test_scorecards.py`）

```python
from __future__ import annotations

import math

import pytest

from finance_agent.outcome.scorecards import (
    FAMILY_REGISTRY,
    Sample,
    aggregate_daily_returns,
    build_family_payload,
    estimate_round_trip_cost_rate,
    max_drawdown_from_returns,
)


def test_registry_families() -> None:
    ids = [spec.family_id for spec in FAMILY_REGISTRY]
    assert ids == ["predictions", "execution"]


def test_cost_rate_matches_matching_config() -> None:
    rate = estimate_round_trip_cost_rate()
    # 0.00025*2 佣金 + 0.0005 印花税 + 0.001*2 中档滑点 = 0.003
    assert math.isclose(rate, 0.003, abs_tol=1e-9)


def test_max_drawdown_known_sequence() -> None:
    assert math.isclose(max_drawdown_from_returns([0.1, -0.2, 0.05, -0.3]), -0.412, abs_tol=1e-3)
    assert max_drawdown_from_returns([]) == 0.0
    assert max_drawdown_from_returns([0.01, 0.02]) == 0.0


def test_aggregate_daily_returns_equal_weight() -> None:
    samples = [
        Sample("600519", "2026-09-01", 0.10),
        Sample("000001", "2026-09-01", -0.02),
        Sample("600000", "2026-09-02", 0.04),
    ]
    assert aggregate_daily_returns(samples) == pytest.approx([0.04, 0.04])


def test_build_payload_null_semantics() -> None:
    spec = FAMILY_REGISTRY[0]
    payload = build_family_payload(spec, [], contract=_contract())
    assert payload["expectancy"] is None
    assert payload["expectancy_reason"] == "no_samples"
    assert payload["max_drawdown"] is None
    assert payload["capacity"] is None
    assert payload["extreme_samples"] == []


def test_build_payload_field_order() -> None:
    spec = FAMILY_REGISTRY[0]
    payload = build_family_payload(spec, [_s("600519", "2026-09-01", 0.05)] * 3, contract=_contract(win_rate=1.0))
    keys = list(payload.keys())
    assert keys.index("expectancy") < keys.index("max_drawdown") < keys.index("capacity") < keys.index("extreme_samples")
    assert keys.index("extreme_samples") < keys.index("win_rate")


def test_insufficient_sample_and_extreme() -> None:
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", -0.20 if d == 1 else 0.01) for d in range(1, 7)]
    payload = build_family_payload(spec, samples, contract=_contract())
    assert payload["expectancy"] is None  # n=6 < 10
    assert payload["expectancy_reason"] == "insufficient_sample"
    assert [row["ticker"] for row in payload["extreme_samples"]] == ["600519"]
```

（`_contract()` / `_s()` 为测试内 helper：`FamilyContract(win_rate=None, caliber_note="…")` / `Sample(ticker, date, ret)`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/outcome/test_scorecards.py -v`
Expected: FAIL（`ModuleNotFoundError: finance_agent.outcome.scorecards`）

- [ ] **Step 3: 最小实现**

`src/finance_agent/outcome/scorecards.py` 要点（完整实现按此骨架展开）：

```python
"""策略族成绩单（add-strategy-family-scorecards）：注册表驱动 + 四槽计算（纯函数优先）。"""

FAMILY_REGISTRY = (
    FamilySpec("predictions", "观点线", extreme_threshold=-0.15),
    FamilySpec("execution", "执行线", extreme_threshold=-0.05),
)
SCORECARD_NOTIONAL = 100_000.0
CAPACITY_PARTICIPATION = 0.01
MIN_SAMPLES = 10
MIN_HISTORY_DAYS = 20


def estimate_round_trip_cost_rate(config=None) -> float:
    cfg = config if config is not None else load_matching_config()
    commission = max(cfg.commission_rate * SCORECARD_NOTIONAL, cfg.min_commission) / SCORECARD_NOTIONAL
    slippage_mid = sorted(cfg.slippage_tiers, key=lambda t: t.quantile_max)[1].slippage_rate  # 中档
    return commission * 2 + cfg.stamp_tax_rate + slippage_mid * 2


def max_drawdown_from_returns(daily_returns) -> float:
    nav, peak, mdd = 1.0, 1.0, 0.0
    for ret in daily_returns:
        nav *= 1.0 + ret
        peak = max(peak, nav)
        mdd = min(mdd, 1.0 - nav / peak)   # ≤ 0
    return mdd


def aggregate_daily_returns(samples):
    by_date: dict[str, list[float]] = {}
    for sample in samples:
        by_date.setdefault(sample.date[:10], []).append(sample.ret)
    return [sum(vals) / len(vals) for _, vals in sorted(by_date.items())]


def build_family_payload(spec, samples, *, contract) -> dict:
    payload: dict = {}
    # ① expectancy（毛/净；样本 0 → null+no_samples；<10 → null+insufficient_sample）
    # ② max_drawdown（aggregate_daily_returns → mdd；日数 < 20 → null+insufficient_history）
    # ③ capacity（由 get_scorecards 注入预取 adv 数据；缺 → null+no_liquidity_data；此处接受
    #    `capacity_inputs` 可选参数：dict[symbol, adv20]；空/全缺→ null）
    # ④ extreme_samples（[r <= spec.extreme_threshold] 明细，键 ticker/date/ret）
    # ⑤ win_rate（展示位；插入序在四槽之后）
    ...
    return payload
```

（实现细节以 design.md §口径细则 为准；`build_family_payload` 增补可选参数 `capacity_inputs: dict[str, float] | None = None` 供测试注入。）

- [ ] **Step 4: 跑绿 + 提交**

Run: `uv run pytest tests/outcome/test_scorecards.py -v` → PASS
```bash
git add src/finance_agent/outcome/scorecards.py tests/outcome/test_scorecards.py
git commit -m "feat(scorecards): 四槽计算纯函数 + 注册表（先红后绿）"
```

### Task 3: 数据访问与组装 `get_scorecards`

**Files:**
- Modify: `src/finance_agent/outcome/scorecards.py`
- Test: `tests/outcome/test_scorecards.py`（追加）

**Interfaces:**
- Consumes: `predictions` 表（`resolved_at/raw_return/status/symbol`）；`paper_equity(equity)`；`positions`；`snapshot.read_bars`。
- Produces: `get_scorecards(...) -> {"families": [各 family payload + family_id/name/as_of/caliber_note]}`；族读取函数 `load_prediction_samples(*) -> list[Sample]`、`load_execution_samples(*) -> list[Sample]`（执行线样本 = 净值日收益，ticker 置 `"<portfolio>"`）。

- [ ] **Step 1: 追加失败测试**（tmp DB 造数；env 注入用 `monkeypatch.setenv`）

```python
def test_get_scorecards_end_to_end(tmp_path, monkeypatch) -> None:
    sessions = tmp_path / "s.db"
    # 用 track_record.model.init_predictions + insert（沿用既有 insert 函数）造 12 条已判定记录：
    # 10 条 resolved_win + 2 条 resolved_loss（raw_return 含 1 条 -0.20）；resolved_at 铺 22 个日期
    # execution 库：init_execution_db + upsert paper_equity（22 天曲线，含一天 -0.06）
    # snapshot 库：init_snapshot + 21 天两标的 amount 行
    monkeypatch.setenv("SESSIONS_DB_PATH", str(sessions))
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snap.db"))
    result = get_scorecards()
    families = {f["family_id"]: f for f in result["families"]}
    pred = families["predictions"]
    assert pred["expectancy"]["n"] == 12
    assert pred["expectancy"]["net"] < pred["expectancy"]["gross"]      # 净 = 毛 − 0.003
    assert pred["max_drawdown"] is not None
    assert pred["capacity"]["suggested_capital"] > 0
    assert [r["ticker"] for r in pred["extreme_samples"]] == ["600519"]
    assert list(pred.keys()).index("extreme_samples") < list(pred.keys()).index("win_rate")
    exe = families["execution"]
    assert exe["extreme_samples"][0]["ret"] <= -0.05                     # 组合级阈值
    assert exe["expectancy"]["gross"] == exe["expectancy"]["net"]        # 已含费
```

- [ ] **Step 2: 跑失败** → `uv run pytest tests/outcome/test_scorecards.py -v`（`get_scorecards` 未定义 → FAIL）
- [ ] **Step 3: 实现数据访问**（薄函数、调用期读 env；快照空库/不新鲜 → 容量槽 null + 原因，**不抛给调用方**）

实现要点：
- `load_prediction_samples`：`SELECT symbol, resolved_at, raw_return FROM predictions WHERE status IN ('resolved_win','resolved_loss','resolved_neutral') AND raw_return IS NOT NULL`；`resolved_at` 非空；按 `resolved_at` 升序。
- `load_execution_samples`：`list_paper_equity` → 相邻 `equity` 计算日收益（首日跳过）；`equity<=0` 的行跳过并计数。
- 容量：`snapshot.latest()` 取最新交易日；`read_bars(codes, start=最新-40 自然日, end=最新, as_of=最新)`；每 code 取尾部 20 行 `amount` 均值；`SnapshotEmptyError/SnapshotNotFreshError` → 全 None。
- `get_scorecards`：组装两族 payload（把容量输入注入 `build_family_payload`），`as_of` 取 `max(sample.date)`。

- [ ] **Step 4: 跑绿 + 提交** → `git commit -m "feat(scorecards): 族数据读取与四槽组装（先红后绿）"`

### Task 4: 只读端点

**Files:**
- Modify: `src/finance_agent/api.py`（track-record 段附近，~L2779）
- Test: `tests/test_api_scorecards.py`（新）

**Interfaces:**
- Consumes: `get_scorecards`。
- Produces: `GET /api/scorecards`（200；载荷 = `get_scorecards()` 原样；只读零写入）。

- [ ] **Step 1: 失败测试**

```python
def test_scorecards_endpoint(monkeypatch, tmp_path):
    # env 三段注入（同 Task 3）+ TestClient(app).get("/api/scorecards")
    # 断言 200、families 两族、字段序（四槽 < win_rate）、空库时 null+原因码而非 500
```

- [ ] **Step 2: 跑失败**：`uv run pytest tests/test_api_scorecards.py -v`
- [ ] **Step 3: 实现**（照既有最小只读端点模式）：

```python
@app.get("/api/scorecards")
async def get_scorecards_endpoint() -> dict[str, Any]:
    return await asyncio.to_thread(get_scorecards)
```

- [ ] **Step 4: 跑绿 + 提交** → `git commit -m "feat(scorecards): GET /api/scorecards 只读端点（先红后绿）"`

### Task 5: 前端成绩单卡片

**Files:**
- Create: `frontend/src/pages/trackRecord/StrategyScorecardCard.tsx`
- Modify: `frontend/src/pages/trackRecord/TrackRecordPage.tsx`（挂载）、`frontend/src/types.ts`（类型）
- Test: `frontend/src/test/trackRecord/strategyScorecardCard.test.tsx`

**Interfaces:**
- Consumes: `GET /api/scorecards`（Task 4 载荷）。
- Produces: `data-testid="strategy-scorecard-card"`；子元素 `scorecard-family-tabs` / `scorecard-expectancy` / `scorecard-drawdown` / `scorecard-capacity` / `scorecard-extremes` / `scorecard-caliber-note` / `scorecard-insufficient` / `scorecard-empty` / `scorecard-error`。

- [ ] **Step 1: vitest 失败测试**（mock fetch，仿 `test/trackRecord/indexCompareCard.test.tsx`）：
  ① 两族切换渲染四槽（期望值毛/净 + 样本量、回撤、容量、极端样本表）；
  ② `insufficient_sample` → 展示「样本不足」且无 0 值；
  ③ 空数据族 → `scorecard-empty`；
  ④ fetch 失败 → `scorecard-error`。
- [ ] **Step 2: 跑失败**：`cd frontend && npx vitest run src/test/trackRecord/strategyScorecardCard.test.tsx`
- [ ] **Step 3: 实现组件**（仿 `IndexCompareCard.tsx`：组件内 fetch + alive 清理；族 tab 本地 state；四槽卡片为 CSS grid；极端样本表 `<table>`；`null` 槽展示原因码对应文案：`insufficient_sample`「样本不足」/`insufficient_history`「结算日不足」/`no_liquidity_data`「成交额数据缺失」）并挂载到战绩页（`IndexCompareCard` 相邻位置，独立 `<StrategyScorecardCard />`）。
- [ ] **Step 4: 跑绿 + `npx tsc --noEmit` + 提交** → `git commit -m "feat(scorecards): 战绩页策略族成绩单区块（先红后绿）"`

### Task 6: E2E（track-record 套件）

**Files:**
- Create: `tests/e2e/playwright/tests/strategy-scorecard.spec.ts`
- Modify: `tests/e2e/playwright/global-setup-track-record.ts`（若要补种——先读再定）、必要时 `tests/e2e/playwright/playwright.track-record.config.ts`

**Interfaces:**
- Consumes: `/api/test/seed`（predictions / paper_equity 块——先读 `api.py` seed 实现确认块名与字段，缺块则扩展 seed：`predictions: [...]`）。
- Produces: `strategy-scorecard.spec.ts`（serial；造数 → `/track-record` → 断言卡片四槽渲染 + 切换执行线 + 样本不足文案）。

- [ ] **Step 1: 写 spec（先红）**——注意 track-record 套件 seed 走 `global-setup-track-record.ts` 的 `track_record_reset`；spec 内用 `request.post('/api/test/seed', ...)` 补种 predictions（≥12 条已判定）与 paper_equity（≥22 天）。跑：
  `cd tests/e2e/playwright && npx playwright test --config=playwright.track-record.config.ts strategy-scorecard` → FAIL（卡片不存在）
- [ ] **Step 2: 实现至绿**（组件/端点已就绪时多为 seed 细节调整）；**红线：不 mock 业务接口**。
- [ ] **Step 3: 提交** → `git commit -m "test(scorecards): track-record E2E 覆盖四槽渲染与空态"`

### Task 7: 收口验证

- [ ] `uv run pytest tests/outcome/test_scorecards.py tests/test_api_scorecards.py -q` 全绿
- [ ] `uv run ruff check src/finance_agent/outcome/scorecards.py tests/outcome/test_scorecards.py tests/test_api_scorecards.py` 干净
- [ ] `uv run mypy src/finance_agent/outcome/scorecards.py`（新文件零新增错误；存量基线不扩大）
- [ ] `cd frontend && npx tsc --noEmit && npm test` 全绿
- [ ] `openspec validate add-strategy-family-scorecards --strict` 通过
- [ ] 胜率静态核查：`grep -rn "scorecards" src/finance_agent/execution/ src/finance_agent/screening/` 零命中（胜率/成绩单不进入自动处置与门禁链）
- [ ] 提交全部分批 commit；报告状态（DONE / DONE_WITH_CONCERNS）
