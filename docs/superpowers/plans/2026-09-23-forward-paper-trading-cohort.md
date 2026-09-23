# add-forward-paper-trading-cohort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 forward paper-trading cohort——固定标的池按交易日定时走真实分析管线（`api._run_graph_streaming` 快路径），观点自然落 predictions 走既有结算链路，`cohort_runs` 独立记账（含 usage 真值），默认关闭、有预算熔断，为 outcome 评估的 forward 腿定向积累可结算样本。

**Architecture:** 生产侧新包 `src/finance_agent/outcome/cohort/`（`model.py` 记账表 / `universe.py` 登记文件 / `runner.py` 跑批执行器）+ `nodes/_llm_utils.py` 增 additive 的 usage 收集器（contextvar，默认 noop）+ `outcome/scheduler.py` 增 per-job 门控的盘后 job；评估侧 `evals/outcome/cohort_readings.py` 提供 join 导出。**predictions 表与结算链路零改动**（记账走独立表，cohort 观点零特判）。

**Tech Stack:** Python（sqlite3 / contextvars / APScheduler 既有接线 / akshare），pytest（fake graph / fake client / tmp DB）。

## Global Constraints

- **工作区**：`.worktrees/outcome-eval`（分支 `feat/outcome-profitability-eval`）；本 delta 依赖 Δ1（caliber/预登记）与 Δ2（T+20 窗口/回避判定/派生入场价）已落地。
- **执行路径一致性**（spec）：cohort 跑批 SHALL 与 `/api/analyze` 同一代码路径 → 迭代 `api._run_graph_streaming` 生成器；**禁止**为 cohort 另建分析或落库路径；观点经既有挂点落 predictions（`source_type="live"`）。
- **零特判**：预登记/结算/盯市/Score 链路不因 cohort 存在分支。
- **默认关闭**：开关 `COHORT_ENABLED == "1"`（**运行时读取**，非模块级冻结）；关闭时到点零触发零 LLM 调用。**不得**并入 `scheduler.py:65` 的全局 early-return（那会连带禁用 settle/marking/metrics/integrity）。
- **幂等键** = `(universe_version, ticker, 交易日)`；同日重复触发跳过；显式 `force` 以 `run_seq+1` 另记。
- **预算熔断**：单轮 token 上限（`COHORT_MAX_TOKENS_PER_RUN`，默认 2_000_000 ≈ 10 标的 × 166k 余量）；达限停止后续标的、记账 skipped(原因=budget) 并告警；已启动的分析正常完成。
- **usage 真值**（spec「非估算」）：provider 未回 usage → 记 NULL + WARN，**不得记 0**。
- **DB**：`data/sessions.db` 同库新表；`_default_db_path()` **调用期**读 `SESSIONS_DB_PATH`（测试可 monkeypatch）。
- **测试**：一律 `tmp_path` 注入 DB；驱动真实生成器必须 `patch("finance_agent.api.graph", fake)`。
- **命令**：`uv run pytest tests/outcome tests/evals/outcome -v`；`ruff check src/ tests/ evals/ scripts/`；`ruff format --check src/ tests/`（勿对别名再导出文件盲跑 `ruff --fix`）。
- **commit 风格**：中文 + conventional 前缀（`feat(cohort):` / `test(cohort):` / `docs(cohort):`）。

---

### Task 1: `cohort_runs` 记账表 + init 接线

**Files:**
- Create: `src/finance_agent/outcome/cohort/__init__.py`、`src/finance_agent/outcome/cohort/model.py`
- Modify: `src/finance_agent/api.py`（模块导入期 init 接线，`init_track_record_tables()` 之后）
- Test: `tests/outcome/test_cohort_model.py`

**Interfaces:**
- Produces: `init_cohort_runs(db_path=None) -> None`；`insert_cohort_run(record: dict, db_path=None) -> str`；`list_cohort_runs(*, universe_version=None, trade_date=None, since=None, db_path=None) -> list[dict]`；`COHORT_RUNS_DDL`

- [ ] **Step 1: 失败测试**

```python
def test_init_and_insert_roundtrip(tmp_path):
    db = tmp_path / "s.db"
    init_cohort_runs(db)
    init_cohort_runs(db)  # 幂等
    rid = insert_cohort_run(
        {
            "universe_version": "v1",
            "ticker": "600519",
            "trade_date": "2026-09-24",
            "session_id": "s-1",
            "langfuse_trace_id": "t-1",
            "status": "success",
            "run_seq": 0,
            "llm_calls": 42,
            "tokens_prompt": 100000,
            "tokens_completion": 60000,
            "tokens_total": 160000,
            "trigger_time": "2026-09-24T18:00:03",
        },
        db,
    )
    rows = list_cohort_runs(db_path=db)
    assert len(rows) == 1 and rows[0]["run_id"] == rid
    assert rows[0]["tokens_total"] == 160000


def test_same_key_same_seq_conflict_and_force_seq(tmp_path):
    # UNIQUE(universe_version, ticker, trade_date, run_seq)：同 seq 重复插入抛 IntegrityError
    # run_seq=1（force 再跑）可插入 → 两行共存，list 返回 2 行
    ...
```

- [ ] **Step 2: 红** → `ModuleNotFoundError: finance_agent.outcome.cohort`

- [ ] **Step 3: 实现**

`model.py`（照 `track_record/model.py` 范式）：

```python
"""cohort 跑批记账（delta add-forward-paper-trading-cohort）。

独立于 predictions（观点表零改动）；同库（data/sessions.db）便于 join 导出。
"""
COHORT_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS cohort_runs (
  run_id            TEXT PRIMARY KEY,
  universe_version  TEXT NOT NULL,
  ticker            TEXT NOT NULL,
  trade_date        TEXT NOT NULL,
  session_id        TEXT,
  langfuse_trace_id TEXT,
  status            TEXT NOT NULL CHECK (status IN ('success','failure','skipped')),
  failure_reason    TEXT,
  run_seq           INTEGER NOT NULL DEFAULT 0,
  llm_calls         INTEGER,
  tokens_prompt     INTEGER,
  tokens_completion INTEGER,
  tokens_total      INTEGER,
  trigger_time      TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  UNIQUE (universe_version, ticker, trade_date, run_seq)
);
CREATE INDEX IF NOT EXISTS idx_cohort_runs_key ON cohort_runs(universe_version, trade_date);
"""
```

`_default_db_path()` / `_connect()`（WAL + busy_timeout）、`init_cohort_runs`、`insert_cohort_run`（`run_id = uuid4().hex`，`created_at` 服务端生成）、`list_cohort_runs`（可选过滤，`ORDER BY trade_date, ticker, run_seq`）按既有范式实现。

`api.py`：`init_track_record_tables()` 之后加 `init_cohort_runs()`（与该块既有局部 import 风格一致）。

- [ ] **Step 4: 绿 + Commit**

```bash
git add src/finance_agent/outcome/cohort tests/outcome/test_cohort_model.py src/finance_agent/api.py
git commit -m "feat(cohort): cohort_runs 记账表与 init 接线（delta add-forward-paper-trading-cohort）"
```

---

### Task 2: universe 登记文件 + 抽样脚本

**Files:**
- Create: `src/finance_agent/outcome/cohort/universe.py`、`scripts/build_cohort_universe.py`
- Modify: `src/finance_agent/data/akshare_client.py`（新增 `fetch_index_constituents`）
- Create: `data/cohort/universe-v1.json`（脚本产出；若 `data/` 被 gitignore 则加 negation `!data/cohort/`）
- Test: `tests/outcome/test_cohort_universe.py`、`tests/data/test_akshare_constituents.py`

**Interfaces:**
- Produces: `load_universe(path) -> Universe`（`Universe{version, effective_date, seed, strata, constituents: list[Constituent{ticker, name, industry, market_cap_bucket}]}`，字段缺失抛 `ValueError`）；`build_universe(version, *, n=10, seed=42, client=None) -> dict`（抽样 + 富化，供脚本写文件）；`AKShareClient.fetch_index_constituents(index_code="000300") -> list[dict]`

- [ ] **Step 1: 失败测试**（要点）
  - `load_universe`：合法文件解析成功；缺 `version`/`constituents` 任一 → `ValueError`（错误信息含字段名）。
  - `build_universe`：同 seed 两次抽样成分一致（可复现）；成分数 = n；分层维度（`market_cap_bucket`/`industry`）非空；`n` 大于可选池 → `ValueError`。
  - `fetch_index_constituents`：mock akshare 主接口返回 → 解析出 `[{"ticker","name"}]`；主接口抛错时回退接口被调用（用 `monkeypatch` 打桩两条调用路径）。

- [ ] **Step 2: 红**

- [ ] **Step 3: 实现**

`akshare_client.fetch_index_constituents`：
```python
def fetch_index_constituents(self, index_code: str = "000300") -> list[dict[str, str]]:
    """沪深300 等指数成分（中证官网优先，东财回退）。返回 [{"ticker","name"}]。"""
    try:
        df = _call_ak(ak.index_stock_cons_csindex, symbol=index_code)
        # 列名以实跑为准（形如 成分券代码/成分券名称），取代码列→ticker、名称列→name
        ...
    except Exception:
        df = _call_ak(ak.index_stock_cons, symbol=index_code)
        ...
```
（实现时先用一次性脚本实测两接口的真实列名并把结果写进 docstring；`_call_ak` 为文件既有包装。）

`universe.py`：
```python
@dataclass(frozen=True)
class Constituent:
    ticker: str
    name: str
    industry: str
    market_cap_bucket: str  # "large" / "mid" / "small"（按总市值分位）

@dataclass(frozen=True)
class Universe:
    version: str
    effective_date: str
    seed: int
    strata: dict          # {"by": "industry+market_cap", "n": 10}
    constituents: tuple[Constituent, ...]

REQUIRED_KEYS = ("version", "effective_date", "seed", "strata", "constituents")

def load_universe(path: str | Path) -> Universe: ...
def build_universe(version: str, *, n: int = 10, seed: int = 42, client=None) -> dict: ...
```
`build_universe`：`fetch_index_constituents` → 逐只 `fetch_industry` + `fetch_stock_quote`（取 `market_cap`）富化（带缓存字典避免重复请求）→ `np.random.default_rng(seed)` 按 (industry, market_cap_bucket) 分层无放回抽 n 只 → 返回可 JSON 序列化 dict。

`scripts/build_cohort_universe.py`：`uv run python scripts/build_cohort_universe.py --version v1 --n 10 --seed 42 --out data/cohort/universe-v1.json`（`uv run` 必需——脚本 import 包）。

- [ ] **Step 4: 绿 + 实跑产出 v1 + Commit**

Run（需网络）: `uv run python scripts/build_cohort_universe.py --version v1 --seed 42`
Expected: 生成 `data/cohort/universe-v1.json`（含 10 成分 + 分层字段）；文件入库（或 .gitignore negation）

```bash
git commit -m "feat(cohort): 沪深300 成分获取 + 分层抽样 universe 登记文件（delta add-forward-paper-trading-cohort）"
```

---

### Task 3: usage 收集器（`_llm_utils` additive）

**Files:**
- Modify: `src/finance_agent/nodes/_llm_utils.py`
- Test: `tests/nodes/test_llm_usage_collector.py`

**Interfaces:**
- Produces: `usage_collector() -> ContextManager[UsageAccumulator]`（contextvar；未激活时零行为变化）；`UsageAccumulator{calls:int, prompt_tokens:int, completion_tokens:int, total_tokens:int, add(usage: dict|None)}`

- [ ] **Step 1: 失败测试**
  - 未使用收集器时：`_call_llm_streaming` 行为与 tokens 无关（既有用例零回归）。
  - `with usage_collector() as acc:` 内消费含 `ev.usage` 的事件流 → `acc.calls/total_tokens` 累加正确；`usage=None` 事件只计 `calls` 不计 tokens。
  - 嵌套/退出后：退出上下文后事件不再累加。

- [ ] **Step 2: 红** → `ImportError: usage_collector`

- [ ] **Step 3: 实现**

```python
_usage_acc: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "llm_usage_acc", default=None
)

@contextlib.contextmanager
def usage_collector() -> Iterator[UsageAccumulator]:
    """激活后，本上下文内所有 LLM 调用的 provider usage 汇入累加器（默认 noop）。"""
    acc = UsageAccumulator()
    token = _usage_acc.set(acc)
    try:
        yield acc
    finally:
        _usage_acc.reset(token)
```
在 `_call_llm_streaming` 的消费循环（`ev.kind` 分支旁）加：
```python
        acc = _usage_acc.get()
        if acc is not None and ev.kind == "finished":
            acc.add(getattr(ev, "usage", None))
```
（`UsageAccumulator.add` 对 None/缺键安全，只 `calls += 1`。）

- [ ] **Step 4: 绿 + Commit**（`uv run pytest tests/nodes -v` 零回归）

```bash
git commit -m "feat(llm): usage 收集器（contextvar，默认 noop）——cohort 记账真值来源（delta add-forward-paper-trading-cohort）"
```

---

### Task 4: runner——跑批执行器

**Files:**
- Create: `src/finance_agent/outcome/cohort/runner.py`
- Test: `tests/outcome/test_cohort_runner.py`

**Interfaces:**
- Consumes: Task 1 记账、Task 2 `load_universe`、Task 3 `usage_collector`、`api._run_graph_streaming` + `api.AnalyzeRequest` + `session_store.create_session`
- Produces: `run_cohort_batch(*, universe_path=None, db_path=None, trade_date=None, force=False, max_tokens=None, enabled=None, graph_runner=None) -> dict`（返回 `{enabled, universe_version, trade_date, success, failure, skipped, skipped_reasons, tokens_total, budget_stopped}`）

- [ ] **Step 1: 失败测试**（要点；`graph_runner` 注入 fake 生成器，避免真实 LLM）
  - `enabled=False`（或 env 非 "1"）→ 直接返回 `{"enabled": False, "success": 0}` 且**零** fake 调用。
  - 正常：池内 2 只 → 2 行 success 记账（含 session_id/trace_id/tokens）、fake 被调 2 次、观点由 fake 落库挂点写入。
  - 幂等：同 `(version, ticker, date)` 已 success → 跳过不调用；`force=True` → 新行 `run_seq=1`。
  - 单标失败：fake 对第 2 只抛异常（或返回 error 事件）→ 第 1 只 success、第 2 只 failure + 原因、第 3 只仍执行。
  - 预算熔断：`max_tokens=1` → 首只完成后停止，其余记账 `skipped` + `failure_reason="budget"`。
  - RFC：`report_ready` 事件缺失（只有 error 事件）→ 该行 failure，`failure_reason` 含事件类型。

- [ ] **Step 2: 红**

- [ ] **Step 3: 实现**（骨架）

```python
def run_cohort_batch(
    *,
    universe_path: str | Path | None = None,
    db_path: str | Path | None = None,
    trade_date: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    enabled: bool | None = None,
    graph_runner: Callable[..., Iterator[str]] | None = None,
) -> dict[str, Any]:
    """遍历标的池内逐标的串行跑 deep 分析；幂等键 (version, ticker, trade_date)。"""
    if enabled is None:
        enabled = os.getenv("COHORT_ENABLED") == "1"
    if not enabled:
        logger.info("cohort 跑批未启用（COHORT_ENABLED != 1）")
        return {"enabled": False, "success": 0, "failure": 0, "skipped": 0}

    universe = load_universe(universe_path or DEFAULT_UNIVERSE_PATH)
    day = trade_date or datetime.now().strftime("%Y-%m-%d")
    budget = max_tokens if max_tokens is not None else int(os.getenv("COHORT_MAX_TOKENS_PER_RUN", "2000000"))
    existing = _successful_keys(universe.version, day, db_path)
    budget_stopped = False
    spent = 0
    result = {"enabled": True, "universe_version": universe.version, "trade_date": day,
              "success": 0, "failure": 0, "skipped": 0, "skipped_reasons": {}, "tokens_total": 0,
              "budget_stopped": False}
    for c in universe.constituents:                      # 串行
        if not force and (universe.version, c.ticker, day) in existing:
            _record(..., status="skipped", reason="duplicate", db_path=db_path); result["skipped"] += 1; continue
        if spent >= budget:
            budget_stopped = True
            _record(..., status="skipped", reason="budget", db_path=db_path); result["skipped"] += 1; continue
        run_seq = _next_seq(universe.version, c.ticker, day, db_path) if force else 0
        outcome = _run_one(c, day, graph_runner=graph_runner, db_path=db_path)   # 见下
        _record(universe.version, c.ticker, day, run_seq=run_seq, **outcome, db_path=db_path)
        spent += outcome.get("tokens_total") or 0
        result["tokens_total"] = spent
        result["success" if outcome["status"] == "success" else "failure"] += 1
    result["budget_stopped"] = budget_stopped
    if budget_stopped:
        logger.warning("cohort 单轮预算熔断（spent=%s >= budget=%s）", spent, budget)
    return result


def _run_one(constituent, day, *, graph_runner, db_path):
    """单标的：建 session → 迭代 fast path 生成器（usage 收集）→ 判 report_ready。"""
    from finance_agent.api import AnalyzeRequest, _run_graph_streaming
    session_id = create_session(stock_code=constituent.ticker, stock_name=constituent.name,
                                status="running", session_type="analysis", db_path=db_path)
    req = AnalyzeRequest(query=f"深度分析{constituent.name}", stock_code=constituent.ticker,
                         stock_name=constituent.name)
    runner = graph_runner or _run_graph_streaming
    trigger = datetime.now().isoformat()
    report_ready = False
    trace_id = None
    try:
        with usage_collector() as acc:
            for chunk in runner(constituent.ticker, constituent.name, req, uuid4().hex, time.time(),
                                session_id=session_id):
                if '"type": "report_ready"' in chunk or '"type":"report_ready"' in chunk:
                    report_ready = True
                    trace_id = _extract_trace_id(chunk) or trace_id
        if not report_ready:
            return {"status": "failure", "failure_reason": "no_report_ready", ...}
        return {"status": "success", "session_id": session_id, "langfuse_trace_id": trace_id,
                "llm_calls": acc.calls, "tokens_prompt": acc.prompt_tokens,
                "tokens_completion": acc.completion_tokens,
                "tokens_total": acc.total_tokens or None, "trigger_time": trigger}
    except Exception as e:  # noqa: BLE001 - 单标失败隔离
        logger.warning("cohort 单标失败 %s: %s", constituent.ticker, e)
        return {"status": "failure", "failure_reason": str(e)[:200], "session_id": session_id,
                "trigger_time": trigger}
```
（`report_ready` 判定与 trace_id 提取按 `api.py:889-918` 的真实事件 JSON 字段对齐——实现者先读该段再用 `json.loads` 严格解析，**不要**用子串匹配作为最终实现。）

- [ ] **Step 4: 绿 + Commit**

```bash
git commit -m "feat(cohort): 跑批执行器——串行 fast path + 幂等 + 预算熔断 + 真值记账（delta add-forward-paper-trading-cohort）"
```

---

### Task 5: 调度接线 + 运维开关 + 环境文档

**Files:**
- Modify: `src/finance_agent/outcome/scheduler.py`、`.env.example`
- Test: `tests/outcome/test_scheduler.py`（追加；既有 4 job 断言不动）

**Interfaces:**
- Produces: scheduler job `id="cohort_batch"`（`CronTrigger(day_of_week="mon-fri", hour=COHORT_HOUR, minute=COHORT_MINUTE, timezone="Asia/Shanghai")`，默认 18:00）；`_cohort_job()` 用 `_with_retry("cohort", run_cohort_batch)`

- [ ] **Step 1: 失败测试**
  - `start_scheduler()` 注册 5 条 job，新增 job 的 `id=="cohort_batch"` 且 cron 串含 `day_of_week='mon-fri'` 与小时 18（默认）。
  - `COHORT_HOUR=7` → cron 小时 7。
  - 全局 early-return（`TESTING=1`）仍禁用全部 job（含 cohort）。

- [ ] **Step 2: 红**

- [ ] **Step 3: 实现**

```python
COHORT_HOUR = int(os.getenv("COHORT_HOUR", "18"))
COHORT_MINUTE = int(os.getenv("COHORT_MINUTE", "0"))

def _cohort_job() -> None:
    _with_retry("cohort batch", run_cohort_batch)
```
`start_scheduler()` 内既有 4 个 `add_job` 之后追加：
```python
    scheduler.add_job(
        _cohort_job,
        CronTrigger(day_of_week="mon-fri", hour=COHORT_HOUR, minute=COHORT_MINUTE,
                    timezone="Asia/Shanghai"),
        id="cohort_batch",
        replace_existing=True,
    )
```
启动日志串同步（`scheduler.py:96-98`）。`.env.example` 增：`COHORT_ENABLED`（默认注 0/未设=关闭）、`COHORT_HOUR`、`COHORT_MINUTE`、`COHORT_MAX_TOKENS_PER_RUN`、`COHORT_UNIVERSE_PATH`（可选）。

- [ ] **Step 4: 绿 + Commit**

```bash
git commit -m "feat(cohort): 盘后调度接线（per-job 门控）+ 运维开关文档（delta add-forward-paper-trading-cohort）"
```

---

### Task 6: 评估侧 cohort 读数导出

**Files:**
- Create: `evals/outcome/cohort_readings.py`
- Test: `tests/evals/outcome/test_cohort_readings.py`

**Interfaces:**
- Produces: `collect_cohort_readings(db_path=None, *, universe_version=None, since=None) -> dict`（`{"batch": {trade_date, universe_version, planned, success, failure, skipped, run_success_rate, opinions_persisted, settlement_status_counts, failure_reasons}, "rows": [逐观点: ticker, created_at, direction, status, avoidance_status, raw_return, excess_return, entry_price, settle_entry_price, confidence]}`，join `cohort_runs` × `predictions`（经 session_id 或 langfuse_trace_id））；CLI `python -m evals.outcome.cohort_readings --db ... --universe-version v1 --since ...`（JSON 输出）
- Consumes: Δ1 健康检查（`evals/outcome/health.py` 的调用方在收口流程中消费本导出）

- [ ] **Step 1: 失败测试**
  - tmp DB 造 2 只 success 记账 + 对应 predictions 行（1 long closed / 1 neutral avoidance）→ `rows` 2 行、`batch.run_success_rate == 1.0`、`settlement_status_counts` 正确。
  - 记账 success 但 predictions 无对应行 → `opinions_persisted` < success 且计入 `failure_reasons["unlinked"]`（子桶 `unlinked_no_opinion`）。
  - `universe_version`/`since` 过滤生效；空库返回零值 + `run_success_rate=None`（不报 0%）。

- [ ] **Step 2: 红 → Step 3: 实现 → Step 4: 绿**

（实现按 `health.py` 的只读连接范式；join 键优先级 `langfuse_trace_id` → `session_id`；不写任何表。）

```bash
git commit -m "feat(evals): cohort 读数导出（join 记账×观点）+ CLI（delta add-forward-paper-trading-cohort）"
```

---

### Task 7: 验证收口

**Files:**
- Create: `tests/validation/2026-09-23-add-forward-paper-trading-cohort-validation.md`
- Modify: `openspec/changes/add-forward-paper-trading-cohort/tasks.md`（勾选）、`docs/evals/metrics.md`（§2 时间线：cohort 启用/标的池版本登记行）

- [ ] **Step 1: 全量相关测试 + lint**

```bash
uv run pytest tests/outcome tests/evals/outcome tests/nodes -v
uv run ruff check src/ tests/ evals/ scripts/ && uv run ruff format --check src/ tests/
uv run mypy src/finance_agent/outcome/cohort evals/outcome
```
Expected: 全绿 / 零违例 / 触碰文件零新增类型错误

- [ ] **Step 2: 离线端到端（零 LLM）**

fake graph（`patch("finance_agent.api.graph", fake)`）+ tmp DB + `universe_path` 指向测试用小登记文件 → `run_cohort_batch(enabled=True)` 断言：记账行齐、幂等复跑跳过、预算熔断路径、`collect_cohort_readings` 汇总正确；开关关闭时零调用。

- [ ] **Step 3: 文档与台账**
  - `metrics.md` §2 追加：「cohort 启用切点（2026-09-23，delta add-forward-paper-trading-cohort，未跑批）：标的池 universe-v1（10 只，seed 42）登记；开关默认关，开启前须 owner 批预算（≈1.7M tokens/日）」。
  - `tasks.md` 勾选 1–4 各条（含「开关关闭时零调用」「幂等/熔断/失败隔离」验收项）。

- [ ] **Step 4: 人工验证报告 + Commit**

报告含：验收项对照表、离线端到端输出摘要、**owner 待办**（真实开启一轮 2–3 标的实跑观测成本与耗时；战绩页 cohort 观点渲染核对）、异常记录。

```bash
openspec validate add-forward-paper-trading-cohort --strict
git commit -m "test(cohort): add-forward-paper-trading-cohort 验证收口——离线端到端 + 台账登记（tasks 勾选）"
```

---

## Self-Review（计划自审）

- **Spec 覆盖**：标的池登记与版本化（T2）/ 定时跑批与记账（T1+T4+T5）/ 成本预算与运维开关（T3+T4+T5）/ cohort 读数导出（T6）——四条 requirement 均有落点；「观点走真实链路零特判」由 T4 的 `graph_runner` 默认值 `api._run_graph_streaming` 保证。
- **占位符扫描**：T2/T4 的接口与骨架含「以实跑为准」的两处（akshare 成分列名、report_ready 事件字段）——均给出**实测指令 + 对齐的源码行号**，非 TBD；其余步骤含完整代码或明确实现范式。
- **类型一致性**：`run_cohort_batch` 返回键在 T4 测试与 T7 端到端中一致；`Universe/Constituent` 字段在 T2/T4 一致；`UsageAccumulator` 字段在 T3/T4 一致。
- **已知边界**：真实 LLM 实跑（成本）与前端展示属 owner 门控项（T7 待办登记）；沪深300 成分接口列名需实跑确认（T2 步骤内已要求写进 docstring）。
