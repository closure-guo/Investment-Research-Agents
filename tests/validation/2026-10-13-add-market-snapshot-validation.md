# 人工验证报告: add-market-snapshot

**日期**: 2026-10-13
**验证人**: ZCode 验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/add-market-snapshot/
**E2E 门禁**: `tests/e2e/playwright/playwright-report/index.html`（本次仅复跑 `snapshot-banner.spec.ts` → 3 passed；默认全量套件未由本次复跑，见异常记录 4）
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`；执行时段北京时 2026-10-13 01:46–01:53（**早于 15:30，且早于开盘**——日批生产时刻为 15:30 收盘后，此刻 spot 表反映的是上一收盘，见异常记录 6 时效说明）

> **硬约束声明（只记录实际发生）**：受东财行情域对本机 IP 的长期封禁（incident 033）影响，本清单中依赖 live 数据的步骤（步骤 1 探测、步骤 2 日批真实数据烟测、步骤 3 两年对账）**未获得任何真实行数据**。全部按受阻如实记录，**未伪造任何对账数据、未把受影响项计为通过**。

## 验证结果

| # | Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|---|
| 1 | 日批写入与幂等（真实数据） | 否 | 对今日跑 `run_market_snapshot_batch()` 两次，行数不变、摘要含 written/rebased/suspended | **未执行（受阻）**：其前置 `AKShareClient().fetch_market_spot()` 返回 None，3 次重试全 `ConnectionError`（见异常记录 1）；按任务约定步骤 2 以步骤 1 成功为前置，未执行。替代证据 = stub 单测（`tests/data/test_market_snapshot_job.py::TestMarketSnapshotBatch::test_batch_idempotent_second_run` 等） | ❌ |
| 2 | 双口径字段与状态位（真实数据抽查：close_qfq==close_raw、`is_suspended` 合成行、5 行抽查） | 否 | 当日行 close_qfq == close_raw；合成停牌行有计数；抽样字段与口径正确 | **未执行（受阻）**：同 #1，真实 spot 不可得。替代证据 = 单测（rebase `test_batch_dividend_rebase_end_to_end` / 停牌合成 `test_batch_suspended_merge` / 状态位 `test_status_flags_st_and_limit_sealed` 等） | ❌ |
| 3 | as_of 防前视与空数据报错（单测证据） | 否 | as_of 超最新数据日期 → `SnapshotNotFreshError`；目标日无记录 → `SnapshotEmptyError`；`end > as_of` 拒绝 | `tests/data/test_market_snapshot.py` 47 项全绿，含 `test_read_universe_rejects_future_as_of`、`test_read_universe_empty_day_raises`、`test_read_bars_window_and_guard`、`test_read_bars_orders_by_date_and_code_and_never_past_as_of` | ✅ |
| 4 | 失败告警链路（单测+E2E 证据） | 是（snapshot-banner.spec.ts） | 日批失败写 `last_error`/`last_error_date` 并重抛；API 据此判 stale；前端横幅出现 | 单测：`test_batch_failure_marks_meta_and_reraise`、`test_api_snapshot_status.py::test_status_stale_when_failed_today`、`test_status_degraded_on_read_error`（降级不 500）全绿；E2E 用例 1 通过 | ✅ |
| 5 | 前端横幅（E2E 证据） | 是 | 失败态 → `snapshot-stale-banner` 可见且逐字文案；成功态 → 无横幅；stale 不阻断输入 | `cd tests/e2e/playwright && npx playwright test tests/snapshot-banner.spec.ts` → **3 passed (12.3s)** | ✅ |
| 6 | 两年对账（计划任务 4.2：20 交易日 × 20 票 × 4 字段） | 否 | 抽样日期与种子落报告；比对总单元数、不一致数（+≤10 例） | **受阻，无数据**：回填 `symbols_ok=0 symbols_failed=20 rows_written=0`（每票 `stock_zh_a_hist` 3 次重试全 `ConnectionError`）；对账脚本 `BLOCKED … 无任何回填行 … 无法对账`（exit=2）。库内 `market_snapshot` 0 行 → **种子/抽样日期/比对单元/不一致数均不存在，无法也不得编造** | ❌ |

单元/集成整体证据（非表格行，汇总）：后端 `uv run pytest tests/data/test_market_snapshot.py tests/data/test_market_snapshot_job.py tests/test_api_snapshot_status.py tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py tests/test_ops_api.py -q` → **193 passed, 1 warning in 9.42s**（各文件 47/28/8/18/33/59）；前端 `cd frontend && npx vitest run src/test/snapshotBanner.test.tsx` → **1 file / 4 tests passed**。

## 执行记录（原始命令与输出摘要）

### 1. 环境探测

```
$ uv run python -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('Asia/Shanghai')))"
2026-10-13 01:46:29.636716+08:00
```

```
$ uv run python -c "... AKShareClient().fetch_market_spot() ..."
AKShare 调用异常: stock_zh_a_spot_em: ConnectionError (attempt 1/3)
AKShare 调用异常: stock_zh_a_spot_em: ConnectionError (attempt 2/3)
AKShare 调用异常: stock_zh_a_spot_em: ConnectionError (attempt 3/3)
AKShare 调用全部失败 (3 次): stock_zh_a_spot_em
全市场 spot 拉取失败/空/缺「代码」列（返回 None）
fetch_market_spot: FAILED (returned None)
```

底层异常原文（直调 akshare）：

```
TYPE: ConnectionError
STR: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

### 2. 日批真实数据烟测（步骤 2）

**未执行**（步骤 1 失败，任务约定为前置条件）。未设置 `SNAPSHOT_DB_PATH` 造 scratch 库跑批，也未做「两次运行行数不变」「close_qfq==close_raw」「停牌合成计数」的真实数据抽查——真实 spot 不可得。另记录时效说明：即使拉取成功，01:46（开盘前）的 spot 是上一收盘快照，与 15:30 收盘后生产时刻语义不同，本应作为 caveat 记录。

### 3. 两年回填 + 对账（步骤 3，计划任务 4.2）

原样命令（后台执行完整跑完）：

```
$ uv run python scripts/backfill_market_snapshot.py --start 2024-01-01 --end 2025-12-31 \
    --symbols 600519,601398,600036,600030,601818,000001,000002,000651,000333,002415,002594,300750,300059,300124,688111,688981,600845,601066,600026,600515 \
    --sleep 0.5 --db tmp/val-snapshot.db
```

每票均：`AKShare 调用异常: stock_zh_a_hist: ConnectionError (attempt 1/3..3/3)` → `历史日线 raw 腿失败/空` → `[N/20] <code>: 失败（取数返回 None）`。摘要行（逐字）：

```
回填完成: symbols_ok=0 symbols_failed=20 rows_written=0 skipped_resumed=0
```

库状态（脚本 `init_snapshot_db` 已建表）：`market_snapshot count: 0`。

对账脚本已按任务要求落盘：`tests/validation/2026-10-13-add-market-snapshot-reconcile.py`（self-contained；固定种子 `--seed` 抽样、逐票全窗口取 raw+qfq 参考、按日期索引后比对 `close_raw/close_qfq/open/volume`）。运行输出（逐字）：

```
$ uv run python tests/validation/2026-10-13-add-market-snapshot-reconcile.py --db tmp/val-snapshot.db --seed 20261013
BLOCKED: 库 tmp\val-snapshot.db 在 [2024-01-01, 2025-12-31] × 20 只标的范围内无任何回填行——回填未产出数据（网络受阻时取数全失败），无法对账。
EXIT=2
```

**本项实际产出：种子 20261013、抽样日期 0 个、比对单元 0、不一致数无**（库内无任何交易日可抽）。20 只标的名单未做任何调整——无一票取到数据，不存在因退市/不可用而替换的判定依据。

对账口径说明（设计意图，本次**未被检验**）：该比对**能**证明回填路径（akshare 取数 → `_history_rows` 映射 → `store` 落库）对源 API 的解析/映射/存储保真度；**不能**证明：状态字段（回填显式 `is_suspended=0`、`prev_close`/涨跌幅为 NULL，源码 docstring 已声明）、以及日批 spot 路径 close 语义与 hist 的可比性（仅收盘后可比，见步骤 2 caveat）。因本项未获得数据，**对 live 数据不做任何保真度声称**。

### 4. E2E 复跑

端口预检：

```
$ netstat -ano | findstr ":8000 :5173"
PORTS FREE: 8000 and 5173
```

```
$ cd tests/e2e/playwright && npx playwright test tests/snapshot-banner.spec.ts
Running 3 tests using 1 worker
  ok 1 ... › 快照失败态 → 全局横幅可见且文案含数据日期 (2.7s)
  ok 2 ... › 快照成功态（当天已更新）→ 无横幅 (2.3s)
  ok 3 ... › 告警不阻断：stale 态下新分析输入框保持可用 (2.6s)
  3 passed (12.3s)
```

（后端以 `TESTING=1` + `SNAPSHOT_DB_PATH=data/test-e2e-snapshot.db` 真实起服，告警态经 `/api/test/seed` 真实落库，非 mock。）

### 5. 单元/集成

见上「验证结果」末段，两个命令均当场执行、exit 0。

## 异常记录

1. **live 数据不可达（东财行情域 IP 封禁，全流程阻断源）**：`stock_zh_a_spot_em`、`stock_zh_a_hist` 两域（push2/82.push2/push2his）连续探测均 `RemoteDisconnected('Remote end closed connection without response')`。旁证与定性：通用网络正常（`curl https://example.com` → 200；01:47 对 `push2.eastmoney.com` 单次 curl 曾 200，随后同 URL 立即复测即 exit 56/HTTP 000，属限频-封禁特征）；换浏览器 UA、走本机代理 `http://127.0.0.1:7897` 均失败；与 `docs/incidents/033-stale-financials-valuation-chain-break-20260929.md`（「东财行情域对本机 IP 封禁长期化（实测 IP 级、指纹伪装无效）」）一致。影响：步骤 2 未执行、步骤 3 无数据；生产上该主机 15:30 日批与回填补齐在此条件下必然失败告警（告警链本身工作正常）。
2. **样本调整**：20 只跨板块标的（600519/601398/600036/600030/601818/000001/000002/000651/000333/002415/002594/300750/300059/300124/688111/688981/600845/601066/600026/600515）**全部保留原清单**；无一票返回过数据，退市/不可用剔除无从谈起，亦未替换。
3. **未达成项清单**（与结论勾选对应）：① 日批真实数据烟测与幂等实测（步骤 2）——前置失败未执行；② 两年区间 20×20 对账（计划任务 4.2）——回填 0 行，对账脚本以 exit 2 显式受阻；③ 默认全量 E2E 本地复跑——本次未执行（见 4）。
4. **全量 E2E 上下文（转述，非本次复跑）**：实施者本地全量结果 14–16 failed，归因为既有 Windows 本机并发/环境争用（非本 change 引入）。仓库既有基线文档 `tests/validation/2026-08-29-refactor-ui-design-system-validation.md` 记录同一套件在**未含任何分支改动**的基线提交 `cc00bc0` 即 18 failed / 7 passed，失败模式同为 `waitForLoadState` 超时/会话列表缺失类。本次**不声称全量绿灯**；`snapshot-banner.spec.ts` 为本次实跑 3 passed（证据见执行记录 4）。
5. **既有测试修改披露（偏离「既有测试无修改通过」红线之字面）**：T7 提交 `65b89fc8` 因 job 注册表 5→6 修改了两处既有测试的**计数类断言**——`tests/outcome/test_ops_jobs.py:36`（`len(JOB_IDS) == 5` → `== 6`）、`tests/outcome/test_scheduler.py`（`test_registers_five_jobs_including_cohort_batch` 更名为 `test_registers_six_jobs_including_cohort_batch`、两处 `len(ids) == 5` → `== 6`、id 集合补 `market_snapshot_daily`、docstring 五个→六个）。仓库先例：`tests/outcome/test_scheduler.py:83` 注释（`Δ3T5+add-market-snapshot T7：job 数增至 6，末次 add_job 不再是 settle——按 id 取，断言不变`）表明注册表上一次扩容（Δ3T5，增至 5）时同类计数断言亦按同一惯例同步更新。断言方向均为**增强**（新 id 必须存在、逐 id 触发时刻断言不变），非放宽；但按红线字面仍属偏离，建议人工复核确认此定性。
6. **时效说明**：全部 live 探测发生在 2026-10-13 01:46–01:53（北京时），早于开盘也早于 15:30 生产时刻；即便当时拉取成功，spot 数据也非「当日收盘」，本报告未据此产出任何通过结论。

## 结论

- [ ] 全部通过，可 archive
- [x] 存在失败项 / 未执行项，需修复或补足验证后重新验证——**本次越过的项**：#3 as_of 防前视与空数据报错（47 项单测全绿）、#4 失败告警链路（单测 + E2E）、#5 前端横幅（E2E 3 passed）、单元/集成汇总 193 passed + vitest 4 passed、E2E 端口预检与复跑。
- **未执行/未达成项及原因**：#1/#2 日批真实数据烟测——`fetch_market_spot` 被东财 IP 封禁阻断（前置失败，按任务约定不做）；#6 两年对账——回填 `symbols_ok=0/rows_written=0`，对账脚本 exit 2 显式受阻（**无任何对账数据，不得视为通过**）；默认全量 E2E 复跑——未执行，仅有实施者转述结果与 `cc00bc0` 基线归档（异常记录 4）。
- **归档建议**：不满足 archive 前置（`tasks.md` 4.2 对账未完成；live 项受阻证据已留档）。待东财封禁解除或改用可用数据源后，重跑步骤 1–3 并将结果回填本报告（对账脚本已就绪、固定种子 20261013）再议归档。另请人工终审异常记录 5 的既有测试修改定性。

## 未修改任何源码声明

本次仅新增 `tests/validation/2026-10-13-add-market-snapshot-reconcile.py` 与本报告；其余文件改动为执行期临时产物（`tmp/val-snapshot.db`，gitignore 覆盖）。未发现需上报为 bug 的既有代码缺陷（唯一系统性问题——live 数据源 IP 封禁——已定性为环境/数据源问题并归档于异常记录 1）。
