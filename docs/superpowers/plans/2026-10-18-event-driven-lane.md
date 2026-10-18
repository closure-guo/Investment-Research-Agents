# Event-Driven Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付「业绩预告事件线」：预告抓取落库（公告日=可得时点）→ LLM 分诊（outperform/in_line/risk）→ 观察清单（可用既有队列发起深度分析）→ 独立记账（T+1 开盘入场、T+5 结算）并注册为成绩单第三族。

**Architecture:** 新包 `outcome/events/`（model/triage/watchlist/journal/job 小文件）；akshare_client 新增预告 fetch（失败 `[]`+error 日志的既有约定）；LLM 走 `llm.gateway.complete_text` + `llm.contracts.extract_json`（照 `screening/candidate_llm.py` 的 Protocol+Gateway 先例）；三个新调度 job（16:05 抓取 / 16:15 分诊 / 16:25 结算）；前端选股页新增事件观察区块；E2E 默认套件。

**Tech Stack:** Python 3.12 / SQLite / APScheduler / FastAPI / pytest；React + TS + vitest；Playwright（默认 stub 套件）。

## Global Constraints

- 工作目录 = `D:\WorkSpace\finance_analysis_agent\.worktrees\personal-edge-program`（分支 `feat/personal-edge-program`）。不得触碰主检出与其他变更的未完成文件；**前端选股页另有变更（regime 温度计）在被实施**——你的前端改动以此为前置顺序（见 Task 8 注）。
- 规范：`openspec/changes/add-event-driven-lane/`（spec.md 合同 + design.md 口径，binding）。
- 口径先登记 `docs/evals/metrics.md` §1（§1.15），再动代码。
- 时点纪律：判断与回填只用公告日及之前可得信息（`available_at ≤ 判定日`），MUST NOT 前视。
- 分诊合同：`verdict ∈ {outperform, in_line, risk}`、`reason ≤ 50 字`、`evidence`（字段级引用）；单条失败 → 降级 `in_line` + trace warning，不阻断批次；prompt 权威源 `src/finance_agent/prompts/event_triage.md`，发布 `uv run python scripts/deploy_prompts.py`（内容一致跳过；若 Langfuse 不可达则以 tests 绿为准并把「待发布」写入报告）。
- 记账口径：入场 = 次一交易日**开盘价**（kline `adjust=SETTLEMENT_ADJUST`，与执行链同口径）；停牌/一字板不可成交 → 顺延至下一可成交日并留痕；horizon 默认 5 个交易日；参考价缺失 → 保持未结算 + 留痕，MUST NOT 折算 0。
- 事件线 v1 **不接**执行链、**不进** regime 系数；读数注册为成绩单 `family_id=event`（`data_source="events"`、`cost_bearing=True`、`min_samples=10`、`extreme_threshold=-0.15`）。
- 调度计数断言同步：现有 15 个 job（含 regime_daily），新增 3 个 → `JOB_IDS` 15→18（`tests/outcome/test_scheduler.py` 两处、`tests/outcome/test_ops_jobs.py` 四处）；新 job 时刻取 16:05 / 16:15 / 16:25（可用空档：16:05/16:15/16:25/16:45）。
- E2E：种子经 `/api/test/seed` 新块；**新块键必须加进 `api.py` L849-864 的守卫 `not ... and` 链**（否则被早退吞掉）；`EVENTS_DB_PATH` 注入默认与 isolated 两 config；`.gitignore` 补 `data/events.db*`、`data/test-e2e-events.db*`；红线：不 mock 业务接口。
- TDD：先红后绿；提交 `feat(events): ...` 中文风格。

---

### Task 1: 口径登记（metrics.md §1.15）

- [ ] `docs/evals/metrics.md` §1 追加「业绩预告事件线」小节：预告字段口径（含 `available_at`=公告日）、分诊合同与降级、入场/horizon/结算口径（T+1 开盘、T+5、顺延规则）、家族隔离声明（不混入 T+20 观点统计；family=event 的口径）；提交 `docs(events): metrics.md §1.15 登记事件线口径`

### Task 2: 存储层（`outcome/events/model.py`，库 `data/events.db`，env `EVENTS_DB_PATH`）

**Interfaces:**
- `init_events_db(db_path=None) -> Path`；`_default_db_path()` 调用期读 env（默认 `data/events.db`；WAL+busy_timeout 短连接照 `track_record/model.py`）
- 四表：`earnings_forecasts(code, report_period, source_record_id, name, forecast_type, change_low, change_high, announce_date, available_at, source, PRIMARY KEY(code, report_period, source_record_id))`；`event_triage(forecast_key, verdict, reason, evidence_json, model, prompt_fingerprint, triaged_at)`；`event_watchlist(id INTEGER PK, forecast_key, source, status, session_id, created_at)`；`event_journal(id INTEGER PK, forecast_key, source, symbol, entry_date, entry_price, horizon_days, status, settle_date, settle_price, raw_return, note, updated_at)`
- `upsert_forecasts(rows, *, db_path=None) -> int`（幂等）；`list_untriaged(db_path=None)`；`record_triage(...)`；`insert_watchlist(...)`（`outperform` 去重：同 forecast_key 已有条目不重复插入）；`list_watchlist(*, source=None, db_path=None)`；`mark_watchlist_analyzing(id, session_id)`；`insert_journal(...)` / `list_open_journal(before_date=None)` / `settle_journal(id, settle_date, settle_price, raw_return, note=None)`；`list_settled_journal(limit, db_path=None)`（供成绩单 loader）

- [ ] 先红后绿：幂等 upsert / untriaged 查询含时点过滤（`available_at <= as_of`）/ watchlist 去重 / journal 生命周期；跑绿 + 提交 `feat(events): events 库四表存储层（幂等）`

### Task 3: 数据源（`data/akshare_client.py` 新增 fetch）

- [ ] 接口名以本机 akshare 实际可用为准（`ak.stock_yjyg_em` 或等价；先 `uv run python -c` 探一下确认名字与列名，探不到则按文档映射 + fake 测试实现，报告写明）。新增 `fetch_earnings_forecasts(self, report_period: str | None = None) -> list[dict]`：`_call_ak` 包装、失败/空 → `[]` + `logger.error`（逐股明细类约定）、代码 `_norm_six_digit_code`、日期 `_norm_date_iso`、来源 `self.sources_seen` 留痕；字段映射表：代码/名称/报告期/预告类型/变动幅度下限/上限/公告日期 → 上述列名（`source_record_id` 用源行号或金额组合稳定键）。
- [ ] 先红后绿（fake 行 fixture：映射/归一/失败空态）；提交 `feat(events): 业绩预告抓取（akshare，失败空态约定）`

### Task 4: 分诊（`outcome/events/triage.py` + prompt）

**Interfaces:**
- `TriageClient` Protocol `complete_json(prompt: str, *, purpose="quick", metadata) -> dict`；`GatewayTriageClient`（照 `screening/candidate_llm.py` 先例，`complete_text` + `extract_json`）；测试用 Stub
- `build_triage_prompt(forecast: dict, *, snapshot_ctx: dict | None) -> str`（`load_prompt("event_triage")` + `{{key}}` 替换；输入预算 ≤12 字段）
- `triage_forecasts(rows, *, client, db_path=None, as_of=None) -> dict`（并发 ≤8；合同校验：非法 verdict/超长 reason/缺证据 → 拒收）；单条 `LLMError` → 降级 `in_line` + warning，不阻断批次；记录模型与 prompt 版本指纹（`load_prompt_with_meta`）

- [ ] `src/finance_agent/prompts/event_triage.md`（合同定义 + 3 个判例；≤60 行）
- [ ] 先红后绿：合法解析 / 三种拒收 / 单条失败降级不阻断 / 指纹落库；提交 `feat(events): LLM 分诊（合同+降级，先红后绿）`
- [ ] `uv run python scripts/deploy_prompts.py --dry-run` 确认收录，正式发布（不可达则记入报告待办）；跑 `tests/test_prompt_contracts.py` 相关门禁

### Task 5: 观察清单与深度分析接线（`outcome/events/watchlist.py` + api.py）

- [ ] `build_watchlist(*, db_path=None) -> int`：`verdict=outperform` 且无条目者入清单（`source="earnings"`）
- [ ] 端点：`GET /api/events/watchlist?source=`（只读，含预告要素/分诊理由/证据/公告日/状态/session_id）；`POST /api/events/watchlist/{id}/analyze`：逐字复用 `api.py` L3461-3488 的建会话 + `queue.enqueue` 段（`_get_analyze_queue()` 单例）；重复点击（已有 session_id）→ 409/幂等返回既有状态，MUST NOT 重复入队；成功后 `mark_watchlist_analyzing`
- [ ] 先红后绿：清单构建（in_line/risk 不入、去重）/ 端点只读 / analyze 幂等与队列复用（`queue.seed` 或 fake starter）；提交 `feat(events): 观察清单端点与既有队列复用（不加新并发通道）`

### Task 6: 记账与结算 + 调度（`outcome/events/journal.py`、`job.py`、scheduler）

**Interfaces:**
- `journal.entry_candidates(*, as_of, kline_client, snapshot_db_path=None, db_path=None) -> dict`：对 `outperform` 且未入场者在**次一交易日开盘**入场；不可成交（停牌/一字板，读 `read_universe(entry_date)`）顺延并留痕 `note=deferred:xxx`
- `journal.settle_due(*, as_of, kline_client, calendar, db_path=None) -> dict`：entry_date + horizon 个交易日到期 → 收盘价结算；参考价缺 → 保持 open + 留痕
- `job.run_earnings_fetch_daily()` / `run_earnings_triage_daily()` / `run_event_settle_daily()`（薄封装，供调度与手动补跑）
- 调度：`JOB_IDS` 插 `earnings_fetch_daily`(16:05)、`earnings_triage_daily`(16:15)、`event_settle_daily`(16:25)（顺序=时刻序）；`SCHEDULES`/`LABELS`/`FUNCS` 同步；scheduler 三个 `_xxx_job` wrapper（照 `_regime_daily_job`）；计数断言 15→18
- 成绩单注册：`outcome/scorecards.py` 注册 `FamilySpec("event", "事件线", data_source="events", cost_bearing=True, min_samples=10, extreme_threshold=-0.15, caliber_note=...)` + `_LOADERS["events"]`（读 `list_settled_journal`，样本 = `raw_return`，日期 = `settle_date`，ticker = `symbol`）

- [ ] 先红后绿：入场口径（T+1 开盘）/ 顺延留痕 / 到期结算 / 参考价缺失不结算 / 家族隔离（event 不入 decision 统计）/ 调度计数；跑 `tests/outcome/` 回归全绿；提交 `feat(events): 记账结算 job + 调度三件 + 成绩单 event 族（先红后绿）`

### Task 7: 前端「事件观察」区块（选股页）

**Files:** Create `frontend/src/pages/screening/EventWatchlistCard.tsx`；Modify `ScreeningPage.tsx`（滚动区横幅序列内，**在 regime 温度计区块之后**）、`types.ts`
**Interfaces:** testid：`event-watchlist-card`、`event-watchlist-row-{id}`、`event-verdict-badge-{id}`、`event-analyze-btn-{id}`、`event-watchlist-empty`、`event-watchlist-error`、`event-source-filter`。三态语义色；结论附口径注释（研究标记，非投资建议）；发起按钮点击后展示排队/运行状态（复用队列快照）；空态/失败态区分。
- [ ] vitest 先红后绿（渲染/三态/发起后状态/空态/失败态/source 过滤）；`npx tsc --noEmit` 绿；提交 `feat(events): 选股页事件观察区块（先红后绿）`
- ⚠️ 顺序注：本任务与 regime 温度计区块同文件挂载——若 regime 前端尚未合并，先 rebase/等待其实施提交，避免同文件冲突。

### Task 8: E2E（默认套件）

- [ ] seed 扩展：`events` 块 + `events_reset`（写入 `EVENTS_DB_PATH` 库；**加进 L849-864 守卫链**）；`EVENTS_DB_PATH` 注入 `playwright.config.ts` 与 `playwright.isolated.config.ts`；`.gitignore` 补两条
- [ ] 新 spec `event-watchlist.spec.ts`（默认套件）：造数（outperform 条目）→ `/screening` → 断言区块渲染/三态徽章/口径注释；空态一条；发起分析 → 队列徽标出现（复用 screening-page 先例的断言方式）；证伪自检（改错期望 → 红 → 改回）；提交 `test(events): 默认套件 E2E 覆盖事件观察区块`

### Task 9: 收口验证

- [ ] `uv run pytest tests/outcome/ tests/test_api_events.py tests/test_prompt_contracts.py -q` 全绿；`uv run ruff check` 改动文件干净；新文件 mypy 零新增
- [ ] 前端 `npx tsc --noEmit` + `npm test` 全绿；`openspec validate add-event-driven-lane --strict` 通过
- [ ] 报告状态（DONE / DONE_WITH_CONCERNS）；人工验证报告落 `tests/validation/`（LLM 分诊质量抽检 = 人工项）
