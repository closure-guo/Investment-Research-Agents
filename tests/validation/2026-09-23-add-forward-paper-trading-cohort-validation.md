# 人工验证报告: add-forward-paper-trading-cohort

**日期**: 2026-09-23
**验证人**: agent（机器项 + 离线端到端）+ owner（真实 LLM 实跑 / 预算 / 前端核对待办）
**关联 delta**: openspec/changes/add-forward-paper-trading-cohort/
**E2E 门禁**: 不适用（非交互类变更：后端调度 + 记账 + 评估侧导出，零前端 / SSE / 状态流转改动）

## 范围说明（读表前必读）

本 delta 实现「cohort 跑批定向积累可结算观点样本」：标的池登记与版本化 → 定时跑批与记账 → 成本预算与运维开关 → 评估侧读数导出。四条 requirement 全部有落点。

**验证分层**（诚实声明）：
- **机器项**：范围跑批（`tests/outcome` + `tests/evals/outcome` + `tests/data` + `tests/nodes`）全绿 + ruff / mypy 零违例。
- **离线端到端（零 LLM）**：脚本 `tests/scripts/d3_task7_cohort_offline_e2e.py` 以 fake `graph_runner`（同线程 contextvar usage 真值通道）+ tmp DB + 测试用小登记文件，走完「跑批 → 记账 → 读数」闭环。
- **真实 LLM 实跑（成本真实发生 ≈166k tokens/次 deep）**：属 **owner 预算门控项**，本 delta 不自行开启（`COHORT_ENABLED` 默认关）。故本报告**不声称**已完成真实链路实跑；`tasks.md` 4.2/4.3 的真实实跑与并发观测部分明确转 owner 待办。

## Scenario 对照表

### 需求①「Cohort 标的池登记与版本化」（2 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 1 | 登记文件字段完备（含种子复现） | `universe.py::REQUIRED_KEYS/CONSTITUENT_KEYS`（L50-51）、`load_universe`（L100，缺键抛 `ValueError` 含字段名）、`build_universe`（L282） | `test_cohort_universe.py::TestLoadUniverse::test_missing_required_key_raises_with_field_name`、`::test_missing_constituent_field_raises_with_field_name`；复现性：`TestBuildUniverse::test_same_seed_reproduces_same_constituents`、`TestPoolSnapshot::test_rebuild_from_pool_snapshot_is_identical`、`::test_rebuild_reproduces_excluded_and_pool_size`；**离线精确重建实测**（见「池快照披露」）：`constituents / cut_points / pool_size / excluded / strata / pool_hash` 逐键相等 | ✅ |
| 2 | 版本期内换池被拒 | `write_universe`（L143）：文件名版本 = 内容 version = 既有 version（`force` 亦然）；**默认拒绝覆盖**；`force=True` **仅限同内容重生成**——逐字段比对 `constituents`（含顺序）/`cut_points`/`pool_size`/`excluded`/`strata`（`FORCE_COMPARE_KEYS`），任一不同 → `ValueError`（提示须新 version） | `TestWriteUniverse::test_refuses_overwrite_within_version_lifecycle`（默认拒绝）、`::test_force_rejects_different_content_same_version`（seed 42→7 成分变 → 拒绝且原文件未改）、`::test_force_allows_same_content_regeneration`（同内容 + force → 允许，离线重建场景）、`::test_rejects_filename_version_mismatch_even_with_force`、`::test_rejects_version_mismatch_with_existing_file` | ✅ |

### 需求②「定时跑批与记账」（4 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 3 | 正常跑批记账完备 | `runner.py::run_cohort_batch`（L79）、`model.py::COHORT_RUNS_DDL` + `insert_cohort_run` | `test_cohort_runner.py::test_two_constituents_success_accounting`；离线端到端 **[1]**（2 只 → 2 行，session_id/trace_id/usage 齐；join 到 predictions） | ✅ |
| 4 | 同日重复触发幂等 | runner 幂等键 `(version, ticker, trade_date)` + `force` → `run_seq` 递增（L131-141、`_next_seq` L213） | `test_duplicate_success_skipped_without_new_row`、`test_force_reruns_with_next_seq`、`test_retry_failed_ticker_uses_next_seq`；离线端到端 **[2]**（复跑零调用无新行；force → `run_seq=[0,0,1,1]`） | ✅ |
| 5 | 单标失败不阻塞整批 | runner 每标的同 `try` 全链路隔离（`_run_one` L223、`_record_*_safely`）；稳定码 `FAILURE_CODES`（L76） | `test_failure_isolation_continues_batch`、`test_session_creation_failure_is_isolated`、`test_record_failure_does_not_abort_batch`、`test_all_failure_reasons_are_stable_codes`、`test_no_report_ready_is_failure` | ✅ |
| 6 | 观点走真实链路零特判 | runner 默认 `graph_runner = api._run_graph_streaming`（L249）；观点经既有挂点落 `predictions`，**结算侧无 cohort 分支** | `test_real_graph_runner_path_writes_row`（patch `api.graph`，走真实 `_run_graph_streaming` 事件形态）；`grep -rin cohort src/finance_agent/outcome/track_record/` → **零命中**；`grep -rn "source_type ==" src/finance_agent/outcome/track_record/` → **零命中**（无按 source_type 的特判分支） | ✅ |

### 需求③「成本预算与运维开关」（3 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 7 | 默认关闭 | runner 内部门控（L101-105，`COHORT_ENABLED != "1"` → 立即返回零调用）；调度 job 只负责「何时跑」（`scheduler.py::_cohort_job` L88、job 注册 L136） | `test_disabled_flag_returns_zero_calls`（零调用且连库文件都不建）、`test_disabled_by_default_when_env_unset`；`test_scheduler.py::TestCohortJobRegistration`（5 job 含 `cohort_batch`）、`::TestCohortGlobalGating`（TESTING / settle 关闭连带关）；离线端到端 **[4]**（零调用 + 零行 + 预期日志） | ✅ |
| 8 | 预算超限熔断 | runner `spent >= budget` → `stop_reason="budget"`（L152-164），剩余 `skipped` + WARN；`_UNKNOWN_USAGE_LIMIT=3` 兜底（L74） | `test_budget_circuit_breaker_skips_rest`、`test_budget_env_default_used_when_param_absent`、`test_unknown_usage_trips_budget_unknown`、`test_non_numeric_budget_env_falls_back`；离线端到端 **[3]**（首只成功，剩余 2 只 `skipped`/`budget`） | ✅ |
| 9 | 成本随轮落账（usage 真值） | `runner.py::_usage_fields`（L317，真值缺失 → NULL + WARN，不记 0）；usage 收集器 `nodes/_llm_utils.py::usage_collector`（contextvar，同步 gateway 路径已挂） | `test_two_constituents_success_accounting`（140 tokens/只）、`test_usage_no_calls_records_null_and_warns`、`test_usage_calls_but_zero_tokens_records_null_and_warns`、`test_partial_usage_then_exception_records_real_tokens`；离线端到端 **[1]**（A 真值 140 / B NULL+WARN） | ✅ |

### 需求④「Cohort 读数导出」（2 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 10 | 按版本与时间窗导出 | `evals/outcome/cohort_readings.py::collect_cohort_readings`（L130，join 经 `langfuse_trace_id`；`since` 复用 `list_cohort_runs` 语义）+ CLI | `test_cohort_readings.py::test_two_linked_success_runs_full_summary`、`::test_filters_by_universe_version_and_since`、`::TestCli::test_cli_json_output`；离线端到端 **[1]**（planned/success/opinions_persisted/结算状态分布/成本汇总正确） | ✅ |
| 11 | 导出只读 | `cohort_readings.py` 仅 SELECT；表缺失按空处理，不建表；DB 缺失抛 `FileNotFoundError` | `test_export_is_read_only`（调用前后 `cohort_runs`/`predictions` 全行快照相等）、`::test_missing_tables_treated_as_empty`、`::test_missing_db_raises` | ✅（措辞见下「口径披露」） |

**合计 11 Scenario：11 行落点齐备。** 需求②③的真实 LLM 实跑/并发观测属 owner 门控（本 delta 开关默认关，不自行烧钱），已转 owner 待办。

## 机器验证结果

命令与真实输出摘要（2026-09-23，分支 `feat/outcome-profitability-eval`，工作树 `.worktrees/outcome-eval`）：

| 命令 | 输出摘要 | 判定 |
|---|---|---|
| `uv run pytest tests/outcome tests/evals/outcome tests/data tests/nodes -v` | `831 passed, 1 warning in 349.02s`（全改动后最终跑） | ✅ 范围全绿 |
| `uv run ruff check src/ evals/ tests/ scripts/` | `All checks passed!` | ✅ 零违例 |
| `uv run ruff format --check src/ tests/ evals/` | `557 files already formatted` | ✅ |
| `uv run mypy src/finance_agent/outcome/cohort evals/outcome` | `Success: no issues found in 10 source files` | ✅ 零错误（含本任务修掉 1 处既有类型错误，见「异常记录」） |
| `openspec validate add-forward-paper-trading-cohort --strict` | `Change 'add-forward-paper-trading-cohort' is valid`（EXIT=0） | ✅ |

**全量 `uv run pytest` 未跑 / 不声称全绿**：全量跑含 `tests/evals/test_hallucination_live.py`（`@live`，凭据泄漏环境下不再 skip 并撞本地 `data/sessions.db` 前置）的 1 例**既有失败**，**非本 delta 引入**，**已立 issue #158**（`gh issue view 158` → OPEN，`[test-infra]` + `bug`）。本 delta 的机器证据为**范围跑批 831 passed**（与 CI 门禁 `-m "not live"` 同口径）。据此 `tasks.md` 4.1 勾选已按此处置标注，**不写「全量绿」**。

## 离线端到端结果（零 LLM）

脚本：`tests/scripts/d3_task7_cohort_offline_e2e.py`（`uv run python tests/scripts/d3_task7_cohort_offline_e2e.py` → `结论: PASS（全部核对项通过）`，EXIT=0）。四段摘要：

| 段 | 场景 | 关键断言（实测） |
|---|---|---|
| **[1]** | 开启跑批 2 只 → 记账 + predictions + 读数 | `cohort_runs` 2 行；A `(prompt,completion,total,llm_calls)=(100,40,140,1)` 真值、trace 回写落库；B `tokens_total=None`（非 0）+ `llm_calls=0` + WARN；读数 `planned=2 / success=2 / run_success_rate=1.0 / opinions_persisted=2 / unlinked=0（两子桶 0）/ settlement_status_counts={resolved_win:1,avoidance:1} / tokens_total_sum=140 / tokens_null_runs=1 / failure_reasons={} / skipped_reasons={}` |
| **[2]** | 幂等复跑 + force | 复跑零调用、`skipped_reasons={duplicate:2}`、无新行（仍 2 行）；`force` 重跑两只、4 行、`run_seq=[0,0,1,1]` |
| **[3]** | 预算熔断（`max_tokens=1`） | 只跑 1 只、`budget_stopped=True`、`success=1 / skipped=2`、`skipped_reasons={budget:2}`、熔断 WARN；读数 `skipped_reasons={budget:2}`、`failure_reasons` **不含** budget、`run_success_rate=1.0`（skipped 不计分母） |
| **[4]** | 开关关闭（`enabled=False` 与 env 缺省） | `enabled=False`、**零 LLM 调用**、**零 `cohort_runs` 行（连库文件都未建）**；预期证据日志 `cohort 跑批未启用（COHORT_ENABLED != 1）` 实测命中 |

## 口径披露

**① `run_success_rate` 分母**：`success / (success + failure)`，`skipped`（幂等重复 / 预算熔断）**不计分母**（未执行尝试，不是失败）。分母 0 → `None`（**不报 0%**）。`planned` / `attempted` 随报告披露。此口径已登记 `docs/evals/metrics.md` §1.8⑧，并已在本任务同步到 §1.8 表与模块 docstring。

**② `unlinked` 拆两桶（可归因）**：`unlinked` = success 行未关联到任何观点行，显式拆为
- `unlinked_trace_missing`：trace 未持久化（NULL/空）→ 无法 join；
- `unlinked_no_opinion = unlinked - unlinked_trace_missing`：分析成功且 trace 有值，但 `predictions` 无对应观点行。
两桶在 `batch` 顶层直接可读（`unlinked_trace_missing` / `unlinked_no_opinion`）。

**③ `failure_reasons` 与 `skipped_reasons` 分桶**：真失败码（`no_report_ready` / `exception` / `unlinked`）入 `failure_reasons`；未执行尝试码（`budget` / `budget_unknown` / `duplicate`）入 `skipped_reasons`——**不得混读为失败率**（Task 6 审查 Important 收口）。

**④ 导出「只读」的准确表述**：`collect_cohort_readings` **不写业务表**（无 INSERT/UPDATE/DDL，不调用 `init_cohort_runs` / `init_track_record_tables`），以全行快照相等守卫（`test_export_is_read_only`）。**但**其复用的 `list_cohort_runs` 内部 `_connect` 会执行 `PRAGMA journal_mode=WAL`——这是**连接级 pragma、非表写入**：生产库已是 WAL，实际为 **no-op**；仅在删除模式下会持久化该设置。故严格说是「不写业务表」，非「绝对零副作用」。（同步更正了 `d3-task-6-report.md` 的旧「只读」表述。）

## 池快照披露（`universe-v1`）

- **`universe-v1` 的池快照为事后重建**：登记文件 `data/cohort/universe-v1.json`（10 只，seed 42，`effective_date=2026-09-23`，`pool_ref=universe-v1.pool.json`，`pool_hash=9ec46b765a87`）与旁车整池快照 `data/cohort/universe-v1.pool.json`（`pool_size=296`，含 4 只缺市值行）。
- **经精确重建验证**（`build_universe("v1", n=10, seed=42, pool=<pool.json>)` 与登记文件逐键比对）：`constituents` / `cut_points` / `pool_size` / `excluded` / `strata` / `pool_hash` / `pool_ref` **全部相等** → 「同 seed 重跑复现同一成分清单」成立（离线、不触网）。
- **环境相关披露字段**：`unknown_industry`（登记 9 / 重建 10）与 `sources`（登记为真实信源指纹 `csindex`/`cninfo`/`baidu`，重建为 `snapshot`）**为环境相关**——上游信源瞬时失败指纹跨轮不同、不可由快照反演，**不保证逐字一致**（已在 `universe.py` docstring 与 `test_rebuild_tolerates_env_dependent_disclosure_drift` 显式隔离，不牵连核心不变式）。

## Owner 待办（门控项）

1. **真实开启一轮 2–3 标的实跑**：观测真实成本与耗时（proposal 参照 ≈166k tokens / ≈9 分钟/次 deep；默认 10 标的/日 ≈1.7M tokens/日），核对 `cohort_runs` 成本落账与 predictions `source_type=live` 可 join；开启前须 owner 批预算（`COHORT_ENABLED=1`）。
2. **战绩页 / 读数人工核对**：cohort 观点在战绩页渲染与 `collect_cohort_readings` CLI 输出的人工核对（前端展示属本 delta 之外）。
3. **`_UNKNOWN_USAGE_LIMIT=3` 阈值与 token 下界对账**：`cohort_runs` 的 token 数为「LLM 网关上报 usage 的**下界**」——**截断重试**时 `finished` 事件只承载末段 usage、二次截断走 error 分支可能低估甚至漏计（`runner.py` docstring 已记「已知低估场景」）。须与 Langfuse 逐 trace 的 `usage_details` 对账后，再定 `_UNKNOWN_USAGE_LIMIT` 与预算阈值的实际取值。

## 异常记录

- **issue #158**（非本 delta 引入）：全量 `uv run pytest` 的 1 例失败为 `tests/evals/test_hallucination_live.py`（`@live`，凭据泄漏环境下不再 skip 并撞本地数据前置），已立 issue（`gh issue view 158` → OPEN，`[test-infra]`/`bug`）。本 delta 不声称「全量绿」；范围跑批 831 passed 与 CI 门禁同口径。
- **本任务修掉 1 处既有 mypy 类型错误**（验证命令暴露）：`src/finance_agent/outcome/cohort/universe.py::_canonical_pool` 的 `rows.sort(key=lambda r: r["ticker"])` 返回 `str | float | None`，与 `list.sort` key 约束不符（HEAD 即报 2 处 `arg-type`/`return-value`）。修为 `key=lambda r: str(r["ticker"])`（ticker 由 L370 `str(...)` 保证恒为 str，**行为等价**）。修后 `mypy` 零错误、`test_cohort_universe.py` 44 passed。
- 说明（非异常）：需求②③的真实 LLM 实跑与并发观测属 owner 门控（开关默认关，不自行烧钱）——按计划设计转 owner 待办，不是缺口。

## 结论

[x] 全部通过（可进 sync/archive 流程）

勾选语义：本 delta 的**验证项**全过——范围跑批 831 passed + ruff / mypy / `openspec validate --strict` 零失败 + 11 Scenario 均有落点与证据 + 离线端到端四段全 PASS + 口径/池快照已披露。**非「立即 archive」**：按 `tasks.md` 4.5 裁定，sync/archive 待 Δ4（`add-backtest-leakage-controls`）落地后统一执行；真实 LLM 实跑与前端核对转 owner 待办。
