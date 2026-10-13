# add-market-snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落库全市场日频行情快照（双复权口径 + 状态 + 历史成分），每日 15:30 日批写入，失败在前端全局横幅告警，读取接口强制 `as_of` 防前视。

**Architecture:** 独立 SQLite 库 `data/market_snapshot.db`（WAL，`CREATE TABLE IF NOT EXISTS`，与 `outcome/store.py` 同款连接纪律，路径经 `SNAPSHOT_DB_PATH` env 可覆盖）。日批为「一次全市场 spot 拉取 + 与昨日快照合并」的纯批量路径（禁止逐票循环）；日批注册进既有 ops job 注册表（单飞锁 + 运行历史 + 手动补跑白拿）。读取接口是独立模块函数，`as_of` 必填、越界显式抛错。前端横幅为 App 级组件，轮询只读状态端点。

**Tech Stack:** Python 3.14 / sqlite3 / pandas / akshare / APScheduler（既有）；React 18 + TS（前端横幅）；Playwright（E2E）。

## Global Constraints

- 后端测试：`uv run pytest tests/... -v`；lint `uv run ruff check`；类型 `uv run mypy`。
- TDD：每个任务先写失败测试（红）再实现（绿）；既有测试不得修改（红线，唯一例外是本计划明确列出的 conftest/E2E 配置扩展）。
- `openspec/specs/` 是行为唯一真源；本 change 的契约在 `openspec/changes/add-market-snapshot/specs/`。
- 后端单 uvicorn worker；scheduler 为进程内 `BackgroundScheduler`（`src/finance_agent/outcome/scheduler.py`），`TESTING=1` 不启动。
- 所有测试禁止真实网络调用（akshare 一律 `@patch("finance_agent.data.akshare_client.ak")` 或 monkeypatch 方法）。
- 中文注释/文档风格与既有代码一致；新模块 docstring 用中文。
- 枚举/字段口径钉死（勿改名）：
  - 表 `market_snapshot(trade_date TEXT 'YYYY-MM-DD', code TEXT 6位, name TEXT, open REAL, high REAL, low REAL, close_raw REAL, close_qfq REAL, volume REAL, amount REAL, pct_chg REAL, prev_close REAL, is_st INTEGER, is_suspended INTEGER, limit_up_sealed INTEGER, limit_down_sealed INTEGER, listed_days INTEGER, list_date TEXT, PRIMARY KEY(trade_date, code))`
  - 表 `snapshot_meta(key TEXT PRIMARY KEY, value TEXT)`；键：`last_success_date` / `last_attempt_at` / `last_error` / `last_error_date` / `trade_calendar`(JSON 数组)
  - 表 `security_master(code TEXT PRIMARY KEY, name TEXT, list_date TEXT, updated_at TEXT)`
  - 异常：`SnapshotError` 基类；`SnapshotNotFreshError`（as_of 超出数据范围）；`SnapshotEmptyError`（该日期无记录）
  - env：`SNAPSHOT_DB_PATH`（默认 `data/market_snapshot.db`）
  - 横幅文案（逐字）：「行情快照未更新（数据日期: YYYY-MM-DD）」；testid `snapshot-stale-banner`
  - 端点：`GET /api/snapshot/status` → `{"latest_date": str|None, "stale": bool, "message": str|None, "last_error": str|None, "last_error_date": str|None}`
  - job id：`market_snapshot_daily`，cron mon-fri 15:30 Asia/Shanghai

## 关键设计决策（实施者必读，不得自行改口径）

**D1 — close_qfq 复权基准维护（rebase）**：日批写入当日行时 `close_qfq == close_raw`（前复权以最新日为基准）。除权除息检测：`k = 交易所昨收(spot prev_close) / 该票最近一条历史行的 close_qfq`；若 `abs(k-1) > 1e-4`，说明发生复权事件，把该票**所有历史行** `close_qfq = round(close_qfq * k, 4)`（历史行 close_raw 不动）。当日行 `close_qfq = close_raw`、`prev_close = spot 昨收`。无历史行（新股次日）不做 rebase。口径理由：只有统一基准的 qfq 才能让动量/反转因子的跨日收益率在除权日不跳变（k<1 时历史价格下移，收益率连续）；**基期取 close_qfq 而非 close_raw**——否则停牌票在恢复交易日会把同一复权事件二次施加（2026-10-13 评审实测场景：Day0 raw=qfq=10、Day1 停牌遇除权、Day2 复牌 → 以 raw 为基期会双重复权）。

**评审修正记录（2026-10-13，实施中发生）**：
- D1 基期 close_raw → close_qfq（上述）。
- `compute_snapshot_status` 的 now 必须取**北京时区**（`ZoneInfo("Asia/Shanghai")`，aware 输入先 astimezone）——仓库既有惯例（api `_track_as_of` 等）。
- 读取接口的日期入参用 `date.fromisoformat` 解析后按 date 对象比较，非规范日期 fail loud（防字典序反转导致前视泄漏）。
- `apply_rebase` 校验 factor 有限且 > 0，否则抛 `SnapshotError`。
- `read_bars` 返回前对数值列 `pd.to_numeric(errors="coerce")`（NULL 混入时保持数值 dtype）。
- 新增 `set_meta_many(mapping, db_path=None)` 单事务批量写，T6 成功路径用它（避免「成功已写、错误未清」的中间态闪现横幅）。
- T8 `/api/test/seed` 若收到 snapshot 键，须同时合成 `last_attempt_at`（否则 D5 的 fresh-DB 分支使 E2E 造不出 stale 态）。

**D2 — 停牌/缺行合成**：spot 表（`stock_zh_a_spot_em`）不含停牌标的。日批把「昨日有行、今日 spot 无、且非退市」的票合成为停牌行：`open=high=low=close_raw=昨行 close_raw`，`volume=amount=0`，`is_suspended=1`，`close_qfq=昨行 close_qfq`，`pct_chg=0`。spot 中存在但 `最新价` 为 NaN 的票同规则。退市票（长期无行）不做正向补行，只在 master 名单里不再出现时自然停更。

**D3 — 状态位判定**（只用 spot 单行信息）：`is_st = "ST" in name`（含 `*ST`）；`limit_up_sealed = (open==high==low==close) and pct_chg >= 4.8`；`limit_down_sealed = (open==high==low==close) and pct_chg <= -4.8`；`listed_days = (trade_date - list_date).days`（自然日；`list_date` 未知 → NULL，不编造）。

**D4 — 交易日守卫**：`ak.tool_trade_date_hist_sina` 单次拉取全历史交易日历（约 9000 行），存 `snapshot_meta.trade_calendar`。日批先判 `trade_date in calendar`，不在则返回 `{"skipped": "non_trading_day"}`（status 仍 ok）；日历拉取失败 → 抛错走既有 3 次重试 + 失败告警链（宁可告警不可静默写错日期）。测试可注入 `trade_date` 参数直接跑。

**D5 — stale 判定**（读取路径纯函数 `compute_snapshot_status(meta, now)`）：
- `expected = 日历中 <= now.date() 的最后一个交易日`；无日历 → 退化「今天是工作日取今天，否则取最近工作日」。
- `stale = (last_error_date == now.date())` 或 `(now.time() >= 18:00 and (last_success_date is None or last_success_date < expected))`。
- 从未成功且从未失败（fresh DB，`last_attempt_at` 不存在）→ `stale=False, message=None`（避免 dev/E2E 噪音；首次启用后失败即告警）。
- `message` 仅 stale 时非空，且 `latest_date` 为 None 时文案为「行情快照未更新」（无日期）；否则逐字「行情快照未更新（数据日期: {latest_date}）」。

**D6 — E2E 造态**：扩展现有 `TESTING=1` 的 `POST /api/test/seed` 请求体（可选字段 `snapshot: {"last_success_date": ..., "last_error_date": ..., "last_error": ...}` 与 `reset` 清空），避免新增端点；E2E 用例 seed 失败态 → 断言行横幅 → reset → 断言行消失。快照库路径在 e2e 配置注入 `SNAPSHOT_DB_PATH=data/test-e2e-snapshot.db`（独立于 SESSIONS_DB_PATH）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/data/snapshot.py`（新） | DDL/连接/写入/读取/状态纯函数/异常；不依赖 akshare |
| `src/finance_agent/data/snapshot_job.py`（新） | 日批编排：日历守卫 → spot 拉取 → 合并合成/复权 → 写库 → meta；失败标记 |
| `src/finance_agent/data/snapshot_backfill.py`（新） | 历史回填（逐票 `stock_zh_a_hist` 两次调用 raw+qfq，限速/可断点续跑）；仅脚本用 |
| `src/finance_agent/data/akshare_client.py`（改） | 新增 4 个包装：`fetch_market_spot` / `fetch_trade_calendar` / `fetch_listing_info` / `fetch_symbol_history_raw_qfq` |
| `src/finance_agent/outcome/ops/jobs.py`（改） | JOB_IDS/LABELS/FUNCS/SCHEDULES 注册 `market_snapshot_daily` |
| `src/finance_agent/outcome/scheduler.py`（改） | 注册 15:30 job（复用 `_with_retry`） |
| `src/finance_agent/api.py`（改） | `GET /api/snapshot/status`（永不 500）；TESTING seed 扩展 |
| `frontend/src/SnapshotBanner.tsx`（新） | 全局横幅组件（挂载轮询 60s） |
| `frontend/src/App.tsx`（改） | 挂载 `<SnapshotBanner />` |
| `tests/data/test_market_snapshot.py`（新） | snapshot.py 单测（schema/写入/读取/状态/复权/幂等/TTL豁免） |
| `tests/data/test_market_snapshot_job.py`（新） | 日批单测（stub ak；合成/守卫/失败标记/幂等/rebase 集成） |
| `tests/test_api_snapshot_status.py`（新） | 端点单测（含降级不 500） |
| `tests/outcome/test_scheduler_market_snapshot.py`（新） | 调度注册单测 |
| `tests/conftest.py`（改） | 隔离夹具扩 `SNAPSHOT_DB_PATH` → tmp_path |
| `tests/e2e/playwright/tests/snapshot-banner.spec.ts`（新） | 横幅 E2E（seed 失败态 → 可见；reset → 消失；入口仍可用） |
| `tests/e2e/playwright/playwright.config.ts`（改） | webServer env 加 `SNAPSHOT_DB_PATH` |
| `scripts/backfill_market_snapshot.py`（新） | 回填运维脚本（含限速；只跑给定区间/标的） |

---

### Task 1: snapshot.py 骨架 — schema/连接/异常/工具

**Files:**
- Create: `src/finance_agent/data/snapshot.py`
- Create: `tests/data/test_market_snapshot.py`

**Interfaces:**
- Produces:
  - `class SnapshotError(Exception)`；`class SnapshotNotFreshError(SnapshotError)`；`class SnapshotEmptyError(SnapshotError)`
  - `init_snapshot_db(db_path: str | Path | None = None) -> Path`（建 3 表 + 索引，幂等；返回实际路径）
  - `_connect(path)` 内部：`sqlite3.connect(path, check_same_thread=False, timeout=15.0)` + `row_factory=Row` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=15000`
  - `_default_db_path() -> Path`：`Path(os.getenv("SNAPSHOT_DB_PATH", "data/market_snapshot.db"))`
  - `set_meta(key, value, db_path=None)` / `get_meta(key, db_path=None) -> str | None` / `get_all_meta(db_path=None) -> dict[str,str]`

- [ ] **Step 1: 写失败测试**：`tests/data/test_market_snapshot.py::test_init_creates_tables_and_is_idempotent`（tmp_path 路径调用两次 init，`sqlite_master` 断言 3 表 + 列集合含 `close_qfq/close_raw/is_suspended/limit_up_sealed/listed_days/prev_close`；`PRAGMA journal_mode` 返回 `wal`）、`test_meta_roundtrip`。
- [ ] **Step 2: 跑测试确认失败**（`uv run pytest tests/data/test_market_snapshot.py -v` → ImportError）。
- [ ] **Step 3: 实现**（模块 docstring 写明 D1-D5 口径；DDL 常量集中定义）。
- [ ] **Step 4: 跑测试确认通过 + `uv run ruff check`。**
- [ ] **Step 5: Commit** `feat(snapshot): market snapshot schema + meta helpers (add-market-snapshot T1)`。

### Task 2: 行构造纯函数 — build_snapshot_rows

**Files:**
- Modify: `src/finance_agent/data/snapshot.py`
- Test: `tests/data/test_market_snapshot.py`

**Interfaces:**
- Consumes: Task 1 的模块。
- Produces:
  - `build_snapshot_rows(*, trade_date: str, spot: pd.DataFrame, prev_rows: dict[str, dict], master: dict[str, dict]) -> list[dict]`
    - `spot` 为 `stock_zh_a_spot_em` 原样 DataFrame（列：代码/名称/最新价/涨跌幅/成交量/成交额/今开/最高/最低/昨收）；`prev_rows` 为昨日快照 `{code: row_dict}`；`master` 为 `{code: {"name","list_date"}}`。
    - 返回行字典列表（键与 Global Constraints 表列名逐字一致；`trade_date`/`code` 字符串，`*_sealed`/`is_*` 为 0/1 int）。
  - `compute_rebase_factors(spot: pd.DataFrame, prev_rows: dict[str, dict]) -> dict[str, float]`：按 D1 返回需要 rebase 的 `{code: k}`（仅 k 与 1 差 > 1e-4 且有历史行）。
- 实现要点：D2 合成停牌；D3 状态位；`pct_chg` 取 spot 涨跌幅（合成行为 0）；`listed_days`/`list_date` 来自 master；`close_qfq = close_raw`；`prev_close = spot 昨收`（合成行取昨行 close_raw）。

- [ ] **Step 1: 写失败测试**（同文件，覆盖）：
  - `test_build_rows_normal_day`（2 票 spot → 2 行，字段逐一对断）
  - `test_build_rows_suspended_synthesized`（昨日 2 票、今日 spot 仅 1 票 → 缺票行 `is_suspended=1, volume=0, close_raw=昨行 close_raw, pct_chg=0`）
  - `test_build_rows_nan_price_treated_suspended`
  - `test_status_flags_st_and_limit_sealed`（ST 名称；开=高=低=收 + pct 9.98 → `limit_up_sealed=1`；pct -9.97/0/3.2 → 0）
  - `test_listed_days_and_unknown_list_date`（master 有 date → 差值；无 → NULL）
  - `test_rebase_factor_detects_dividend`（昨行 raw 10.0、spot 昨收 9.0 → k=0.9；昨收 10.0 → 不返回）
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现两个纯函数（不碰 IO）。**
- [ ] **Step 4: 跑测试确认通过。**
- [ ] **Step 5: Commit** `feat(snapshot): row builder + rebase factor detection (T2)`。

### Task 3: 写入与读取接口 — 幂等写 + as_of 防前视 + 空数据报错

**Files:**
- Modify: `src/finance_agent/data/snapshot.py`
- Test: `tests/data/test_market_snapshot.py`

**Interfaces:**
- Produces:
  - `write_snapshot_rows(trade_date: str, rows: list[dict], db_path=None) -> int`（`INSERT OR REPLACE`；重复执行行数不变；返回写入行数）
  - `apply_rebase(code: str, factor: float, before_date: str, db_path=None) -> int`（`UPDATE market_snapshot SET close_qfq = ROUND(close_qfq * ?, 4) WHERE code=? AND trade_date < ?`）
  - `latest_snapshot_date(db_path=None) -> str | None`
  - `read_universe(as_of: str, *, include_suspended: bool = True, db_path=None) -> list[dict]`：`as_of > latest` → `SnapshotNotFreshError(f"快照最新数据日期为 {latest}，不接受 as_of={as_of}")`；该日无行 → `SnapshotEmptyError(f"该日期无快照记录: {as_of}")`。
  - `read_bars(codes: list[str], start: str, end: str, *, as_of: str, db_path=None) -> pd.DataFrame`：先做 as_of 防护（end > as_of → `SnapshotNotFreshError`；as_of > latest → 同）；无行 → `SnapshotEmptyError`。
  - `upsert_security_master(rows: list[dict], db_path=None) -> int` / `get_security_master(db_path=None) -> dict[str, dict]`

- [ ] **Step 1: 写失败测试**：
  - `test_write_idempotent_same_day`（写两次 → `COUNT(*)` 不变）
  - `test_read_universe_rejects_future_as_of`（最新 2026-10-10，as_of 2026-10-20 → `SnapshotNotFreshError`，`str(exc)` 含 `2026-10-10`，且无返回值）
  - `test_read_universe_empty_day_raises`（数据库有别的日期 → `SnapshotEmptyError`）
  - `test_read_universe_delisted_symbol_history`（X 有 2025-03-01 行、无 2025-09-01 行 → 前者含 X，后者抛 `SnapshotEmptyError` 或返回不含 X——按「无该日整天数据才报错」口径：为让场景可测，测试数据加一个别的票在 2025-09-01 有行，断言结果不含 X）
  - `test_read_bars_window_and_guard`
  - `test_apply_rebase_multiplies_history_only`
- [ ] **Step 2-4: 红 → 实现 → 绿。**
- [ ] **Step 5: Commit** `feat(snapshot): idempotent write + as_of-guarded readers (T3)`。

### Task 4: 状态纯函数 + 缓存 TTL 豁免契约测试

**Files:**
- Modify: `src/finance_agent/data/snapshot.py`
- Test: `tests/data/test_market_snapshot.py`

**Interfaces:**
- Produces: `compute_snapshot_status(meta: dict[str, str], now: datetime | None = None) -> dict`（D5 规则，返回 `{"latest_date","stale","message","last_error","last_error_date"}`；`now` 为 None 取 `datetime.now()`）。
- 测试：`test_status_fresh_db_not_stale`、`test_status_failed_today_stale`、`test_status_after_18_without_success_stale`、`test_status_before_18_pending_not_stale`、`test_status_success_clears`、`test_status_no_calendar_weekday_fallback`、`test_cache_cleanup_does_not_touch_snapshot`（建 `DataCache(tmp)` 写入过期条目并 `clear_all()`，断言 `market_snapshot`/`security_master` 行数与 `snapshot_meta` 键不变——cache-management delta 场景）。

- [ ] Step 1-5 同上节奏；Commit `feat(snapshot): status computation + cache TTL exemption contract (T4)`。

### Task 5: akshare 包装（4 个）

**Files:**
- Modify: `src/finance_agent/data/akshare_client.py`
- Test: `tests/data/test_market_snapshot_job.py`（或 `tests/data/test_akshare_client.py` 追加）

**Interfaces:**
- Produces（`AKShareClient` 实例方法，风格对齐既有：经 `_call_ak` 重试，失败返回 None/空）：
  - `fetch_market_spot() -> pd.DataFrame | None`：`ak.stock_zh_a_spot_em()`；列缺失（无 `代码`）→ None。空表 → None。
  - `fetch_trade_calendar() -> list[str] | None`：`ak.tool_trade_date_hist_sina()`；取 `trade_date` 列，返回 `["YYYY-MM-DD", ...]` 升序；失败 None。
  - `fetch_listing_info() -> list[dict] | None`：`ak.stock_info_sh_name_code`（3 个 symbol 变体）+ `ak.stock_info_sz_name_code` + `ak.stock_info_bj_name_code` 逐个 try；合并 `[{"code","name","list_date"}]`；某交易所失败不拖垮整体；全失败 None。列名兼容（如 `A股代码`/`证券代码`/`上市日期`；值可能是 `date`/`str`/`NaN` → 统一 `YYYY-MM-DD` 或 None）。
  - `fetch_symbol_history_raw_qfq(code: str, start: str, end: str) -> pd.DataFrame | None`：两次 `ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust="")` 与 `adjust="qfq"`，按 `日期` 对齐合并出 `[日期, 开盘, 收盘_raw, 收盘_qfq, 成交量]`；任一失败 None。
- 测试全部 `@patch("finance_agent.data.akshare_client.ak")`，不真实联网；断言列名映射与降级 None。

- [ ] Step 1-5 同上；Commit `feat(akshare): market spot / calendar / listing / dual-caliber history wrappers (T5)`。

### Task 6: 日批编排 — snapshot_job.py

**Files:**
- Create: `src/finance_agent/data/snapshot_job.py`
- Test: `tests/data/test_market_snapshot_job.py`

**Interfaces:**
- Consumes: Task 2/3/4/5。
- Produces:
  - `run_market_snapshot_batch(trade_date: str | None = None, *, client: AKShareClient | None = None, db_path=None) -> dict`：返回 `{"trade_date","written","rebased","suspended","skipped"}` 摘要（跳日返回 `{"skipped":"non_trading_day",...}`）。
  - 编排顺序：日历守卫（D4；`trade_date` 显式传入时仍校验在日历内，日历不可得且显式传入日期 → 继续跑并在摘要里 `calendar="unavailable"`；日历不可得且未显式传入 → 抛错）→ spot 拉取（None → 抛错）→ 读昨日行/master → `build_snapshot_rows` → `compute_rebase_factors` + `apply_rebase` → `write_snapshot_rows` → 更新 meta：`last_success_date`/`last_attempt_at`，清 `last_error`/`last_error_date`，写 `trade_calendar`（若本次拉到）；master 为空或每周一 → `fetch_listing_info` + upsert。
  - 失败路径：任何异常 → 更新 meta `last_error`/`last_error_date`/`last_attempt_at` 后**重抛**（交 ops `run_job` 落 failed + scheduler 重试；重试成功则 meta 被成功路径覆盖）。
- 测试（stub `client` 用 monkeypatch 注入 4 个方法的假实现）：
  - `test_batch_writes_rows_and_meta`（日历含当天；spot 2 票 → 2 行 + `last_success_date`）
  - `test_batch_idempotent_second_run`（跑两次行数不变，`rebased` 第二次为 0）
  - `test_batch_dividend_rebase_end_to_end`（昨日 10.0/今 spot 昨收 9.0 → 历史行 close_qfq 变 9.0，当日 close_qfq == close_raw）
  - `test_batch_skips_non_trading_day`（返回 skipped，无行写入，meta 未标失败）
  - `test_batch_failure_marks_meta_and_reraises`（spot None → 抛错 + `last_error_date`==当日）
  - `test_batch_suspended_merge`（昨日 2 票今 spot 1 票 → 3 行？不：2 行，1 合成）
  - `test_master_refresh_on_empty`
- [ ] Step 1-5 同上；Commit `feat(snapshot): daily batch orchestration with rebase + alerting meta (T6)`。

### Task 7: 调度与 ops 注册

**Files:**
- Modify: `src/finance_agent/outcome/ops/jobs.py`、`src/finance_agent/outcome/scheduler.py`
- Test: `tests/outcome/test_scheduler_market_snapshot.py`、`tests/test_ops_api.py`（既有文件追加断言 job 列表含新 id）

**Interfaces:**
- `JOB_IDS` 追加 `"market_snapshot_daily"`（放 `decision_settle_daily` 之前或之后？展示顺序：追加在 `decision_settle_daily` 前——15:30 早于 16:00，保持时间序）；`JOB_LABELS["market_snapshot_daily"]="行情快照"`；`JOB_FUNCS[...]=run_market_snapshot_batch`；`SCHEDULES[...]={mon-fri,15,30,Asia/Shanghai}`。
- `scheduler.py`：新增 `_snapshot_job()`（`_with_retry("market_snapshot_daily", "market snapshot", run_market_snapshot_batch)`）+ `add_job` 注册；日志行补 15:30。
- 测试：`test_schedule_spec_1530_monfri`、`test_job_registry_contains_snapshot`、`test_start_scheduler_registers_six_jobs`（用 `TESTING` 关掉 + 直接调 start_scheduler 的既有测法：monkeypatch `DECISION_SETTLE_ENABLED` 等，参照同目录既有 scheduler 测试的桩法）、`test_snapshot_job_uses_retry_wrapper`（monkeypatch run_job 断言 job_id）。
- [ ] Step 1-5 同上；Commit `feat(snapshot): register 15:30 daily job in ops registry + scheduler (T7)`。

### Task 8: API 端点 + TESTING seed 扩展

**Files:**
- Modify: `src/finance_agent/api.py`
- Test: `tests/test_api_snapshot_status.py`

**Interfaces:**
- `GET /api/snapshot/status`：`{"latest_date","stale","message","last_error","last_error_date"}`；实现 = `await asyncio.to_thread(snapshot_status_payload)`；任何异常 → 返回 `{"latest_date":None,"stale":False,"message":None,"last_error":"status_unavailable","last_error_date":None}`（**永不 500**，对齐 ops 纪律）。
- `POST /api/test/seed`：请求模型加可选 `snapshot: dict | None`（写入 meta 键）+ `snapshot_reset: bool = False`（清空 snapshot 三表）；仅 TESTING 分支内生效。
- 测试：`test_snapshot_status_ok`、`test_snapshot_status_degraded_on_error`（monkeypatch 读库函数抛错 → 200 且 stale False）、`test_seed_snapshot_meta`（TESTING=1 monkeypatch + tmp SNAPSHOT_DB_PATH）。
- [ ] Step 1-5 同上；Commit `feat(api): snapshot status endpoint + TESTING seed hooks (T8)`。

### Task 9: 前端全局横幅

**Files:**
- Create: `frontend/src/SnapshotBanner.tsx`
- Modify: `frontend/src/App.tsx`
- Test: E2E（Task 10）+（可选）`frontend/src/__tests__` 无既有组件测法 → 不新增 jest 测试，交互由 E2E 钉。

**Interfaces:**
- 组件：mount 时 fetch `/api/snapshot/status` + `setInterval(60_000)`（cleanup 清定时器）；`stale && message` 才渲染；`data-testid="snapshot-stale-banner"`；样式复用 App 内既有顶部警告条（`fixed top-16 left-1/2 z-[60]` 风格）；文案直接渲染 `message`（后端逐字给定）。
- `App.tsx`：在全局通知区（`App.tsx:1042-1051` warningMessage div 附近）挂载 `<SnapshotBanner />`。
- [ ] Steps：实现 → `cd frontend && npm run build`（tsc 通过）→ Commit `feat(frontend): global market-snapshot staleness banner (T9)`。

### Task 10: E2E 横幅用例 + 配置

**Files:**
- Create: `tests/e2e/playwright/tests/snapshot-banner.spec.ts`
- Modify: `tests/e2e/playwright/playwright.config.ts`（webServer env + `SNAPSHOT_DB_PATH`）

**Interfaces / 场景（stub 套件，默认 config）**:
1. `快照失败态 → 全局横幅可见且文案含数据日期`：`request.post('/api/test/seed', {data: {snapshot: {last_success_date: '2026-10-09', last_error_date: '<今天>', last_error: 'boom'}, snapshot_reset: true 先跑}})` → `page.goto('/')` → `expect(getByTestId('snapshot-stale-banner')).toBeVisible()` + `toContainText('行情快照未更新（数据日期: 2026-10-09）')`。
2. `成功态 → 无横幅`：seed `{last_success_date: <今天>}`（无 error）→ reload → `expect(banner).toHaveCount(0)`。
3. `告警不阻断`：seed 失败态 → 输入框可用（`toBeEnabled`）。
- 每个用例 before/after 走 reset 保证隔离；默认端口 8000（既有默认 config，含 TESTING=1）。
- [ ] Steps：写 spec → `cd tests/e2e/playwright && npx playwright test tests/snapshot-banner.spec.ts`（先红：端点/横幅未实现时）→ 实现转绿 → 再跑默认全量套件确认无回归 → Commit `test(e2e): snapshot staleness banner scenarios (T10)`。

### Task 11: 回填脚本 + 对账验证材料

**Files:**
- Create: `scripts/backfill_market_snapshot.py`
- Create: `tests/scripts/test_backfill_market_snapshot.py`（限速/区间参数纯函数单测，不联网）
- Produce（运行期产物）: `tests/validation/2026-10-13-add-market-snapshot-validation.md`

**Interfaces:**
- 脚本：`uv run python scripts/backfill_market_snapshot.py --start 2024-01-01 --end 2025-12-31 --symbols 600519,000001,... --sleep 0.5`；逐票 `fetch_symbol_history_raw_qfq`；写 snapshot 行（`close_raw/close_qfq` 双口径，状态位留 NULL/0 并在摘要注明「回填行状态字段不完整」）；断点续跑（已有该票该区间的行则跳过）；限速默认 0.5s/票。
- 对账：`scripts/` 内不新增对账脚本；验证报告里用一次性 `uv run python - <<'PY'`（或临时脚本）在 20 个随机抽样交易日 × 抽样票上，逐字段比对快照行 vs 当日重新拉取的 `stock_zh_a_hist`（含 qfq）——抽样与结果全部落报告。
- ⚠️ 若本机 AKShare/东财不可达（IP 封禁史），报告如实记录「验证受网络限制未执行/部分执行」并给出替代证据（单测 + stub 集成），**不得伪造对账数据**。
- [ ] Steps：脚本 + 单测 → Commit `feat(scripts): snapshot backfill with rate limit and resume (T11)`；然后运行验证（结果落 validation 报告）。

### Task 12: 验收收口

- [ ] 全量后端测试：`uv run pytest -x -q`（若既有套件需 Langfuse/网络，按项目既有说明处理，记录实际执行范围）
- [ ] `uv run ruff check` + `uv run mypy`（对改动文件）
- [ ] `cd frontend && npm run build`
- [ ] E2E 默认套件全绿（`tests/e2e/playwright && npx playwright test`）
- [ ] 回填 + 对账执行（或如实记录受限）→ `tests/validation/2026-10-13-add-market-snapshot-validation.md`（含 E2E 门禁路径、对账表、异常记录、结论勾选）
- [ ] `openspec/changes/add-market-snapshot/tasks.md` 全勾
- [ ] `openspec validate add-market-snapshot --strict` → `openspec archive add-market-snapshot`（sync：market-snapshot 新建 + cache-management MODIFIED）
- [ ] Commit：`chore(openspec): archive add-market-snapshot（validation 随档）`

## Self-Review 备注

- 覆盖对照：R1 全市场日频落库 → T2/T3/T6；R2 历史成分与状态 → T2/T3（退市场景）/T6；R3 时点截断防前视 → T3；R4 日批失败告警 → T4/T6/T8/T9/T10；cache-management R1 TTL 豁免 → T4。
- `read_bars` 的 `end > as_of` 保护与 `as_of > latest` 报错共同构成「仅返回 date <= as_of、越界显式报错」双保险。
- 类型一致性：`compute_rebase_factors` 返回 factor 字典被 T6 消费；`build_snapshot_rows` 的 prev_rows 形状与 T3 `read_universe` 行 dict 一致（sqlite Row → dict）。
