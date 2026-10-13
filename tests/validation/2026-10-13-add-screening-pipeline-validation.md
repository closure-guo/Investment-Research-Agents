# 人工验证报告: add-screening-pipeline

**日期**: 2026-10-13
**验证人**: ZCode 实施 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/add-screening-pipeline/（specs: stock-screening / frontend / pipeline-events）
**E2E 门禁**: `tests/e2e/playwright/playwright-report/index.html`（最后一次全量复跑 = 25 passed / 13 failed / 1 skipped，失败集合全部为既有环境性类别，见「E2E 证据」与异常记录 2）
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`（Windows 本机，默认 config，8000/5173；端口起跑前经 `netstat -ano | findstr ":8000 :5173"` 预检为空闲）

> **硬约束声明（只记录实际发生）**：本报告区分「自动化证据」与「人工主观项」——主观项（页面可读性/浮层内容是否可用）由 E2E 快照 + 截图支撑，但**人工终审签字未完成**。依赖 live 行情的「连续 5 交易日完整日批核对」受东财 IP 封禁（issue #325）阻断，**未获得任何真实日批数据，未计为通过**（见异常记录 1）。

## 验证结果

| # | Scenario / 能力 | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|---|
| 1 | 日批编排（快照确认→L1→L2→L3→精选池落库） | 否（合成库单测） | 批次 ok、池行 = keep ∩ 连续在榜门槛、L2 全量 Top-N 落库、同日重跑幂等覆盖 | `tests/screening/test_pipeline.py::test_batch_ok_writes_batch_l2_full_list_and_eligible_pool`、`test_batch_drops_excluded_from_pool`、`test_rerun_same_day_overwrites_without_duplication`、`test_latest_successful_pool_returns_latest_ok_batch_with_date` 全绿；合成夹具三库隔离（快照/因子库/screening），零网络 | ✅ |
| 2 | 失败不产残缺池（spec 场景「中途失败不产残缺池」） | 是（横幅为 E2E 证据） | L3 网关整体失败/快照不新鲜/缺日历 → 批次 `failed` + error 落库 **且当日池零行** | `test_batch_l3_systemic_failure_records_failed_and_zero_pool`、`test_batch_fails_closed_when_snapshot_not_fresh`、`test_batch_fails_closed_when_calendar_missing` 全绿（池行数与批次行单事务同生共死）；前端横幅见 #5 与 E2E 用例 3 | ✅ |
| 3 | 信号时效性校验（spec 场景「盘前停牌移除」） | 否（单测 + 端点单测） | 停牌 → `SUSPENDED`、一字涨跌停 → `LIMIT_LOCKED`、新事件 → `NEW_EVENT`；多因并存；干净标的不入列表；**校验接入 analyze 端点：被移除者不建会话、不占并发位，其余照常入队** | 纯函数：`test_freshness_flags_suspended_and_leaves_others_untouched_spec_scenario`（其余标的不受影响）、`test_freshness_flags_limit_locked`、`test_freshness_flags_new_event_on_previous_trading_day`、`test_freshness_returns_all_reasons_in_s2_order`、`test_freshness_without_calendar_only_counts_same_day_events`、`test_freshness_treats_missing_snapshot_row_as_suspended`、`test_freshness_clean_symbols_return_empty`；接线：`tests/test_api_screening.py::test_analyze_endpoint_removes_suspended_symbol_and_starts_others`（removed=[600001/SUSPENDED]，其余 4 只照常 2 running + 2 queued，被移除者无会话，审计行落 `screening_removals`）、`test_analyze_endpoint_flags_limit_locked_and_new_event`、`test_analyze_endpoint_skips_freshness_without_snapshot_state`、`test_analyze_endpoint_reads_snapshot_through_env_path` | ✅ |
| 4 | API 端点（pool / status / analyze + seed 扩展） | 部分（E2E 经真实端点取数，见 #5/#6） | pool：最近成功批次池 + `stale` 标注；status：失败文案「选股批次失败（最近成功: YYYY-MM-DD）」且**永不 500**；analyze：池空 409、响应 `{started, queued, removed}`；seed：`screening` 造数 + `screening_reset` | `tests/test_api_screening.py` 20 例全绿，含 `test_status_endpoint_never_500_on_read_error`（读库异常降级 200）、`test_pool_endpoint_stale_after_failed_batch_keeps_last_success`、`test_analyze_endpoint_409_when_pool_empty`、`test_seed_screening_batch_and_pool_roundtrip`、`test_seed_screening_queue_and_reset`；store 新增 `screening_removals` 表 3 例（幂等 upsert / fail-loud 校验 / reset 覆盖） | ✅ |
| 5 | 前端选股页（`/screening`：表格/贡献分解 hover/在榜天数/L3 理由/横幅/空态） | 是 | 列齐 + 批次日期；hover 综合分 → 浮层（factor_id + contribution，`\|contribution\|` 降序）；stale → 横幅标注最近成功日期；空态；分析按钮 | Vitest `frontend/src/test/screeningPage.test.tsx` 7 例全绿；E2E `tests/screening-page.spec.ts` 用例 1/2/3/4 通过（表格含 L3 理由与在榜天数、浮层顺序 3→11→7 断言、失败横幅含最近成功日期 2026-10-13、空态 + 按钮禁用） | ✅ |
| 6 | 批量分析并发（S3：上限 2 + 排队 + 前端徽标） | 是（入队徽标部分） | 同时 running ≤ 2、其余 queued；终态补位；前端 queued/running 徽标可见 | 后端：`test_analyze_endpoint_caps_concurrency_at_two_and_queues_rest`（2 running + 3 queued、仅补位者真启动）、`test_analyze_endpoint_promotes_next_when_session_completes`（completed → 补位第三只，全程 ≤2）、`test_analyze_endpoint_queue_visible_in_pool_payload`、队列 FIFO/终态/启动失败用例全绿；E2E 用例 5（点击 → 池内标的全量入队、`screening-queue-badge-{symbol}` 可见）通过 | ✅ |
| 7 | E2E 门禁（本 change 新增 spec，默认 config） | — | `npx playwright test tests/screening-page.spec.ts` 全绿；默认全量套件记录 + 既有红归因 | 单跑 **5 passed (14.4s)**（log 存 `tmp/e2e-screening-page-standalone.log`）；全量套件复跑 **25 passed / 13 failed / 1 skipped**，其中本 spec 5/5 全绿（见「E2E 证据」），13 条失败全部落入既有环境性类别 | ✅ |

汇总命令证据（逐字）：

```
$ uv run pytest tests/ -m "not live" -q
1 failed, 5195 passed, 6 skipped, 12 deselected, 30 warnings in 907.29s (0:15:07)
# 唯一失败 = tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
# 归因：父目录 .env 的 LLM_MAX_CONTEXT=256000 经 api.py 导入期 load_dotenv() 泄漏（既有机器级，见异常记录 4）

$ uv run pytest tests/screening tests/test_api_screening.py -q
153 passed, 2 warnings in 23.59s

$ uv run ruff check src/finance_agent/api.py src/finance_agent/screening/ tests/test_api_screening.py tests/screening/
All checks passed!

$ uv run mypy src/finance_agent/api.py src/finance_agent/screening/store.py src/finance_agent/screening/pipeline.py
Found 10 errors in 3 files (checked 3 source files)
# 与改动前基线逐字同集（git stash 对照复跑同为 10 errors：api.py:436/1280/1469/2213 + evals/extract.py ×4 + evals/judges.py ×1）→ 本 change 零新增 mypy 债

$ cd frontend && npx vitest run
 Test Files  81 passed (81)
      Tests  685 passed (685)

$ cd frontend && npm run build
✓ built in 6.87s   （tsc -b 通过）
```

## E2E 证据（门禁）

**本 change 新增 spec**：`tests/e2e/playwright/tests/screening-page.spec.ts`（5 用例，serial + 每例前后 `screening_reset`）。
**红线遵守**：无任何 `route.fulfill` / MSW / 业务接口 mock——池与批次经 TESTING=1 的 `/api/test/seed` 真实写入独立库，页面经真实 `GET /api/screening/pool` 取数。`playwright.config.ts` 后端 env 新增 `SCREENING_DB_PATH=data/test-e2e-screening.db`（与 `SESSIONS_DB_PATH`/`SNAPSHOT_DB_PATH` 同款隔离；**否则 seed/reset 会清掉开发库 `data/screening.db`**）。

单跑（写入 `tmp/e2e-screening-page-standalone.log`）：

```
$ cd tests/e2e/playwright && npx playwright test tests/screening-page.spec.ts
  ok 1 种子池 → 表格可见（含 L3 理由、连续在榜天数、批次日期） (2.4s)
  ok 2 hover 综合分 → 浮层展示因子贡献（|contribution| 降序） (1.8s)
  ok 3 批次失败 → 横幅可见且标注最近成功批次日期（池仍展示） (2.1s)
  ok 4 无成功批次 → 空态 + 分析按钮禁用 (1.9s)
  ok 5 点击批量分析 → 池内标的全量入队，显示排队/运行徽标 (1.8s)
  5 passed (14.4s)
```

默认全量套件复跑（log 存 `tmp/e2e-full-screening-branch-2.log`；HTML 报告 `tests/e2e/playwright/playwright-report/index.html`）：

```
$ cd tests/e2e/playwright && npx playwright test
  13 failed
  1 skipped
  25 passed (3.1m)
```

本 spec 在全量套件内的逐条结果（8 并发 worker 下仍全绿）：

```
ok 16 tests\screening-page.spec.ts:92:1 › 种子池 → 表格可见（含 L3 理由、连续在榜天数、批次日期） (12.3s)
ok 21 tests\screening-page.spec.ts:110:1 › hover 综合分 → 浮层展示因子贡献（|contribution| 降序） (15.3s)
ok 22 tests\screening-page.spec.ts:133:1 › 批次失败 → 横幅可见且标注最近成功批次日期（池仍展示） (10.2s)
ok 28 tests\screening-page.spec.ts:160:1 › 无成功批次 → 空态 + 分析按钮禁用 (1.9s)
ok 30 tests\screening-page.spec.ts:168:1 › 点击批量分析 → 池内标的全量入队，显示排队/运行徽标 (9.8s)
```

**13 条失败的归因（既有基线，非本 change 引入）**：`concurrent-streaming-integrity` ×2、`contract`、`interaction`、`sidebar-content-overflow`、`search-banner @live` ×3、`deep-thinking-toolcall @live`、`thinking-banner @live` ×3、`agui-session-switch-isolation`。全部为流式/并发/@live 横幅类，与 `tests/validation/2026-08-29-refactor-ui-design-system-validation.md` §E2E 失败归因同族：**基线提交 `cc00bc0`（未含任何分支改动）同一套件即 18 failed / 7 passed**，失败模式为 `waitForLoadState` 超时、后端会话列表缺会话——Windows 本机 8 并发 worker + vite polling 的环境性既有问题；且**单次运行间成员有增减**（本轮 13 条 vs 上一轮 17 条，集合 != 恒等，其中 `agui-chat` 2 例本轮已自愈）。本 change 相关失败条目：0。

**首轮全量套件暴露并已修正的本 spec 自身问题（如实记录）**：首轮全量运行（17 failed，含本 spec 用例 1）——8 并发下页面首屏 `/api/screening/pool` 取数 >5s（默认 expect 超时），页面停在「加载中…」。处置：首屏断言显式放宽至 15s（与 `snapshot-banner.spec.ts` 同款实例化超时，非全局放宽、非 mock）；复跑两轮全量 + 单跑三次均全绿。另修正造数 API 调用显式走 `http://127.0.0.1:5173`（Windows 上 `localhost` 可能先解析 `::1`，vite 绑 0.0.0.0（IPv4）→ 偶发 ECONNREFUSED，首轮实测发生 1 次）。

## 异常记录

### 1. 连续 5 交易日完整日批人工核对 —— 受阻（东财 IP 封禁，issue #325）

- **要求**（tasks.md 4.2）：真实行情下连续 5 个交易日跑完整日批并人工核对。
- **实际**：东财行情域对本机 IP 的封禁仍未解除（incident 033 / issue #325，`gh issue view 325` → OPEN）。本次**实测复核**：

```
$ timeout 180 uv run python -c "from finance_agent.data.akshare_client import AKShareClient; spot = AKShareClient().fetch_market_spot(); print('fetch_market_spot:', 'FAILED (None)' if spot is None else f'OK rows={len(spot)}')"
100%|██████████| 59/59 [01:20<00:00,  1.08s/it]
fetch_market_spot: FAILED (None)
```

  即 spot 拉取在重试与降级源穷尽后返回 None → 快照日批无输入 → 不存在可核对的真实日批数据。**未伪造任何日批数据，未把本项计为通过**。
- **合成替代说明（证据边界，不等于通过）**：以合成夹具覆盖日批全部确定性语义——`tests/screening/test_pipeline.py` 三库隔离（快照库含 80 交易日日历与 25 日面板、因子库含 active 因子与 60 日 IC、screening 库），断言批次 ok/failed、池 = keep ∩ 连续在榜、L2 全量落库、同日重跑幂等、快照不新鲜/缺历 fail-closed、`latest_successful_pool` 取最近成功批次；E2E 以 `/api/test/seed` 造批次/池验证页面链路。**「真实行情连续 5 交易日」的时效性/数据质量核对仍缺，须随 issue #325 补验**。
- **风险留痕**：`run_screening_batch` 依赖快照新鲜度与 `trade_calendar`（缺历 fail loud），封禁期间日批恒 failed（当日池零行 + 横幅）——即「失败不产残缺池」行为已由单测钉死，但生产可用性恢复时点取决于 #325。

### 2. 默认全量 E2E 套件本机既有红（环境性，非本 change）

见「E2E 证据」归因段：两轮复跑分别为 17 failed / 25 passed（首轮含本 spec 自身超时 1 条，已修正）与 13 failed / 25 passed；失败集合 ⊆ 基线 `cc00bc0` 归因族，成员在单次运行间漂移。**本报告不声称全量绿灯**；本 change 新增 spec 单跑与套件内均全绿为其门禁证据。

### 3. 测试对默认快照库的副作用（评审修正 1 已关闭，措辞更正）

`analyze` 端点接线后，未显式注入 `SNAPSHOT_DB_PATH` 的既有后端用例会经 `latest_snapshot_date()` 触碰默认路径 `data/market_snapshot.db`（该文件已入 `.gitignore`）。**原报告「空库 → 校验按『无最新可得状态』跳过 → 用例语义不变」的前提仅在开发库为空时成立**：该库一旦有行且日期 ≥ 池批次日，未显式覆盖的 analyze 用例会真正进入事件装配（WebSearch/litellm 网络调用），语义随开发库内容漂移、并可能因网络失败而红（评审复现：向 `data/market_snapshot.db` 写 5 行 2026-10-13 数据后，修正前 `-k analyze` 3 例全部触达真实事件装配）。

**修正**：隔离已做进 `tests/test_api_screening.py` 的 autouse fixture——`SCREENING_DB_PATH` 与 `SNAPSHOT_DB_PATH` 逐用例双双钉 tmp（快照用例显式 `monkeypatch.setenv` 后设者胜，不受影响）；新增回归用例 `test_snapshot_db_path_pinned_to_tmp_not_cwd_default`。**证明**：同一份「有行的开发库」下，修正后 `tests/test_api_screening.py` 25 例全绿（网络守卫 autouse 桩零触发 = 零事件装配/零网络）。全局 conftest 纪律（覆盖本文件以外的测试）仍如原记录所述待单独立项。新增用例一律 monkeypatch `SNAPSHOT_DB_PATH` 到 tmp。

### 4. 全量后端套件唯一失败 —— 父目录 .env 泄漏（既有，机器级；非本 change）

- **现象**：`uv run pytest tests/ -m "not live" -q` → **5195 passed / 1 failed / 6 skipped**；唯一失败 `tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default`（断言 `max_context == 128000`，实得 256000）。
- **归因实验（不依赖本 change 任何文件）**：单跑该用例 → 1 passed（`tests/llm/test_resolver.py` 不导入 `api.py`，不触发 dotenv）；改用**基线既有**文件对跑复现：
  `uv run pytest tests/test_api_snapshot_status.py tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default -q` → **1 failed, 8 passed**。
- **根因**：`api.py` 导入期 `load_dotenv()`（`find_dotenv` 自 cwd 上溯）命中**主仓根目录**本地未入库的 `D:\WorkSpace\finance_analysis_agent\.env` 中 `LLM_MAX_CONTEXT=256000` 并写入 `os.environ`；该用例不传 `_env` 时读真实环境 → 覆盖 registry 静态默认。**与 screening 改动零关系**（本 change 未触碰 dotenv/环境加载路径）。

## 人工终审待签项

1. 页面可读性主观评价：表格列密度、综合分浮层在真实数据下的可读性（当前仅有合成数据截图 `tests/e2e/playwright/test-results/` 与 E2E 断言文本作间接证据）。
2. 移除原因展示文案（`{symbol}（SUSPENDED）：停牌`）在产品语义上是否够醒目（当前为页内黄色边框区块，非全局横幅）。
3. 异常记录 1 的 5 交易日补验结论（随 issue #325）。
4. 本文档全部条目的终审判定与签字。

## 结论

自动化门禁全部达成：后端全量 `tests/ -m "not live"` **5195 passed / 1 failed**（唯一失败为父目录 `.env` 泄漏的既有机器级用例，已用基线文件对跑复现归因，见异常记录 4）+ 本 change 靶向套件 153 passed；前端 Vitest **685 passed**；本 change E2E spec 单跑 **5 passed**、默认全量套件内 **5 passed**（套件整体 25 passed / 13 failed / 1 skipped，13 条红按 `cc00bc0` 基线归因）。既有测试零修改。唯一未达成项为「连续 5 交易日真实日批核对」（东财 IP 封禁，issue #325，合成替代已如实说明并标注证据边界）。**结论：待人工终审签字**。
