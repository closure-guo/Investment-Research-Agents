# 人工验证报告: add-paper-broker（Task 3 前端双曲线 / Task 4 E2E + 回放核对 + 免责声明）

**日期**: 2026-10-13
**验证人**: ZCode 实施 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/add-paper-broker/（specs: `broker-execution` / `track-record`）
**范围**: 本报告覆盖 Task 3（战绩页虚拟 vs 仿真双曲线 + 执行损耗标注 + Vitest）与 Task 4（E2E 门禁 / 合成面板回放核对 / README 免责声明 / 收口）。Task 1–2 的实现提交为 `1237999a`（Broker 抽象 + paper broker）与 `94ddadf4`（每日结算 + 双曲线接口 + seed 扩展），本报告对其测试做复跑确认（未修改任何既有测试语义）。
**E2E 证据**: `tests/e2e/playwright/playwright-report/index.html`（最后一次运行 = 隔离套件 16 passed）；原始 log：`tmp/e2e-paper-broker-standalone.log`、`tmp/e2e-track-record-suite-paper-broker.log`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`（Windows 本机）；E2E 跑在 `playwright.track-record.config.ts` 专属端口对 8004/5177（起跑前 `netstat -ano | findstr ":8004 :5177"` 预检为空闲）。

> **硬约束声明（只记录实际发生）**：Task 4 要求的 2026-09 真实行情回放核对受东财 IP 封禁（issue #325）阻断，**以合成面板替代**（13/13 逐笔手算一致，见「回放核对」），**「真实历史行情回放」未计为通过**；默认 config 全量 E2E 因 8000/5173 被本机 docker compose 栈占用（端口预检命中，见异常记录 2）按派单纪律跳过，**未声称默认套件绿灯**；人工终审签字未完成。

## 与计划/派单的偏差（如实记录）

| # | 偏差 | 原因与处置 |
|---|---|---|
| D1 | E2E 落位：计划/派单要求 `paper-broker.spec.ts` 走**默认 config（8000/5173）**；实际运行于 `playwright.track-record.config.ts`（8004/5177） | 本 spec 必须 seed `equity_curve`（双曲线需共同日期），而默认 config 共享库 `data/test-e2e-sessions.db` 同时承载 `decisions.spec.ts` 的「`track-record-curve` count 0」空态断言——种子跨 run 持久，落库后 decisions.spec 必红。仓库既有 review fix（该 config 头注释）已因同一冲突把 track-record 造数 spec 全部迁入独立 config；本 spec 沿同一隔离纪律：`playwright.track-record.config.ts` 的 `testMatch` 显式收录 `paper-broker.spec.ts`，默认 config `testIgnore` 显式排除。文件路径保持派单原样 `tests/e2e/playwright/tests/paper-broker.spec.ts`。运行命令：`npx playwright test -c playwright.track-record.config.ts tests/paper-broker.spec.ts` |
| D2 | 新增 seed 复位键 `track_record_reset`（派单要求「reset in beforeEach/afterEach」，此前无清理通道） | 清 `equity_curve` + `paper_equity` 两表（复位边界=双曲线造数面；predictions 不涉）。实现：`api.py` seed 分支 + `delete_all_equity_points` / `delete_all_paper_equity` 两个 store 助手；`tests/test_api_track_record.py` 追加 1 例（既有用例零修改，仅 import 列表增 1 名） |
| D3 | `playwright.track-record.config.ts` 后端 env 追加 `EXECUTION_DB_PATH=data/test-e2e-track-record-execution.db` | 否则 `paper_equity` 种子会落到开发库 `data/execution.db`（与 `SCREENING_DB_PATH` 隔离同款纪律；缺失该隔离时本 spec 不可交付） |

## 验证结果

| # | Scenario / 能力 | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|---|
| 1 | broker-execution「paper broker 下单闭环」（spec 场景：初始 100 万、买入 600519×100、快照可成交 → 持仓 +100、资金按成交价+fee 扣减；订单含 decision_id/signal_id） | 否（单测 + 回放核对） | 撮合经统一引擎；成交价 = `open×(1+slippage)`；买入成本 = 成交额+fee；订单证据链完整 | `tests/execution/test_paper_broker.py` 全绿（783 passed 内含）；合成面板回放 13/13 笔逐笔价格/费用与独立手算一致（含 3 档滑点全覆盖），资金台账 1,057,821.46 期望=实际、期末空仓（见「回放核对」） | ✅ |
| 2 | broker-execution「涨跌停订单挂单」（spec 场景：一字涨停买入 → `pending` + `LIMIT_UP_UNFILLABLE` 且零持仓变动） | 否（单测 + 回放核对 sealed 分支） | 状态 `pending`、原因码逐字、不产生持仓变动 | 单测覆盖；回放核对含 1 笔一字涨停买入：`status=pending`、`reason=LIMIT_UP_UNFILLABLE`、持仓前后一致 | ✅ |
| 3 | track-record「双曲线展示」（spec 场景：虚拟 1.043 / 仿真 1.031 → 两曲线 + 图例「虚拟结算」「仿真成交」+ 执行损耗 -1.2%） | **是** | 双曲线同渲染、图例区分、损耗标注 -1.2%（1 位小数）；`paper: null` → 单曲线不报错 | Vitest `frontend/src/test/paperCurve.test.tsx` 3/3（双序列值/图例 DOM/损耗文案 `执行损耗: -1.2%`/null 与缺省两态）；E2E `paper-broker.spec.ts` 2/2（真实 seed → 页面断言图例与损耗文案；无 paper → 单曲线、无损耗标注） | ✅ |
| 4 | 双曲线接口对齐（B3：共同日期、基期重合、`paper: null`、as_of 防前视） | 否（后端集成） | 仅共同日期对齐；无数据/无交集 → `paper: null` | `tests/test_api_track_record.py` 37 passed（含 T2 已交付的 4 例：对齐、双 null、as_of 前视排除；本 change 新增 reset 1 例） | ✅ |
| 5 | README 免责声明（逐字要素「仅供研究，不构成投资建议」） | —（文档） | 断言要素存在且人工可读 | `README.md` 顶部引言后新增 1 行引用块免责声明（含逐字要素；研究性口径披露） | ✅ |

汇总命令证据（逐字）：

```
$ uv run pytest tests/ -m "not live" -q
1 failed, 5343 passed, 6 skipped, 12 deselected, 67 warnings in 1216.13s (0:20:16)
# 唯一失败 = tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
# 归因（既有机器级 .env 泄漏，与上一份 2026-10-13 报告同记载）：父目录 .env 的
# LLM_MAX_CONTEXT=256000 经 api.py 导入期 load_dotenv() 泄漏。实验：单跑该用例 1 passed；
# 与任意 import api.py 的用例同跑即复现（tests/test_api_track_record.py + 该用例 → 1 failed, 37 passed）
# → 非本 change 引入（本 change 未触碰 llm/ 与 dotenv 路径）。

$ uv run pytest tests/execution tests/outcome -q
783 passed, 2 warnings in 85.21s (0:01:25)

$ uv run pytest tests/test_api_track_record.py -q
37 passed, 1 warning in 9.49s

$ uv run ruff check src/finance_agent/api.py src/finance_agent/execution/store.py \
      src/finance_agent/outcome/track_record/model.py tests/test_api_track_record.py \
      tests/validation/2026-10-13-add-paper-broker-replay-check.py
All checks passed!

$ uv run mypy src/finance_agent/execution/store.py src/finance_agent/outcome/track_record/model.py src/finance_agent/api.py
Found 10 errors in 3 files (checked 3 source files)
# 全为既有债且不在本 change 改动 hunk 内：api.py ×5（436×2/1307/1496/2240，行号=基线上浮 +27）
# + evals/extract.py ×4 + evals/judges.py ×1；store.py / model.py 零错误 → 本 change 零新增 mypy 债

$ cd frontend && npx vitest run
 Test Files  82 passed (82)
      Tests  688 passed (688)

$ cd frontend && npm run build
✓ built in 21.02s   （tsc -b 通过）

$ cd tests/e2e/playwright && npx playwright test -c playwright.track-record.config.ts tests/paper-broker.spec.ts
  ok 1 … paper 数据就位 → 双曲线图例「虚拟结算」「仿真成交」与执行损耗标注可见 (5.4s)
  ok 2 … 无 paper 数据 → 单曲线渲染不报错，无损耗标注 (3.2s)
  2 passed (17.8s)

$ cd tests/e2e/playwright && npx playwright test -c playwright.track-record.config.ts   # 隔离套件全量（paper-broker + 6 个 track-record-*.spec）
  16 passed (1.5m)
```

## E2E 证据（门禁）

**本 change 新增 spec**：`tests/e2e/playwright/tests/paper-broker.spec.ts`（2 用例，serial + 每例前后 `track_record_reset`）。
**红线遵守**：无任何 `route.fulfill` / MSW / 业务接口 mock——种子经 TESTING=1 的 `/api/test/seed` 真实写入独立库（SESSIONS/EXECUTION 双隔离），页面经真实 `/api/v1/track-record/overview` 取数；图例与损耗文案为 DOM 断言（ECharts 图例画在 canvas 内，故前端新增 DOM 图例区承载可断言文本）。

单跑（log：`tmp/e2e-paper-broker-standalone.log`）：

```
$ npx playwright test -c playwright.track-record.config.ts tests/paper-broker.spec.ts
  ok 1 tests\paper-broker.spec.ts:63:3 › 战绩页:虚拟 vs 仿真双曲线（add-paper-broker） › paper 数据就位 → 双曲线图例「虚拟结算」「仿真成交」与执行损耗标注可见 (5.4s)
  ok 2 tests\paper-broker.spec.ts:82:3 › 战绩页:虚拟 vs 仿真双曲线（add-paper-broker） › 无 paper 数据 → 单曲线渲染不报错，无损耗标注 (3.2s)
  2 passed (17.8s)
```

隔离套件全量（log：`tmp/e2e-track-record-suite-paper-broker.log`）——证明本 spec 与既有 6 个 track-record spec 共存（本 spec 文件序在前，`afterEach` 复位后 index-compare 首例空态前提不受影响）：

```
$ npx playwright test -c playwright.track-record.config.ts
  16 passed (1.5m)
```

跑后库态复核（复位生效、零残留）：`test-e2e-track-record.db → equity_curve 0 行`；`test-e2e-track-record-execution.db → paper_equity 0 行`。

**刻意未跑的默认套件全量（预检命中，见异常记录 2）**：`npx playwright test`（默认 config，8000/5173）——端口被本机 docker compose 栈占用（PID 11692 com.docker.backend / 41684 wslrelay），`reuseExistingServer: true` 会复用不可控后端且种数会写开发库，按派单「端口预检；占用即停」纪律不执行。预检原始输出存 `tmp/e2e-port-precheck-8000-5173.log`。

## 回放核对（Task 4 / tasks.md 3.2）

- **脚本**：`tests/validation/2026-10-13-add-paper-broker-replay-check.py`（`uv run python …` 可直接复跑，退出码即结论）
- **JSON**：`tests/validation/2026-10-13-add-paper-broker-replay-check.json`（逐笔期望/实跑/判定 + 资金台账 + sealed 用例 + verdict）
- **口径**：合成面板 2026-09 全月 22 个工作日、3 标的；13 笔成交全部经 `PaperBroker.place_order`（注入 bar；临时库，零污染）；期望值由**脚本内独立算术**给出（滑点档=脚本自实现分位选档；成交价 = `round(open×(1±rate),2)`；费用 = `round(round(max(5, 成交价×数量×佣金率),2) (+印花税),2)`），不调用 broker 侧计算。滑点三档 0.002/0.001/0.0005 全覆盖（成交额木样 100/500/1000）。
- **结果**：**13/13 笔逐笔一致**；一字涨停挂单用例 OK（pending/LIMIT_UP_UNFILLABLE/零持仓变动）；资金台账期望 1,057,821.46 = 实际；期末空仓；`verdict: PASS`。

| # | 日期 | 标的 | 方向 | 数量 | bar open | 滑点档 | 期望成交价 | 实跑成交价 | 期望费用 | 实跑费用 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-09-01 | 600519.SH | buy | 100 | 1600.0 | 0.0005 | 1600.8 | 1600.8 | 40.02 | 40.02 | ✅ |
| 2 | 2026-09-01 | 300308.SZ | buy | 200 | 150.0 | 0.001 | 150.15 | 150.15 | 7.51 | 7.51 | ✅ |
| 3 | 2026-09-01 | 000858.SZ | buy | 100 | 120.0 | 0.002 | 120.24 | 120.24 | 5.0 | 5.0 | ✅ |
| 4 | 2026-09-07 | 600519.SH | buy | 100 | 1632.0 | 0.0005 | 1632.82 | 1632.82 | 40.82 | 40.82 | ✅ |
| 5 | 2026-09-07 | 300308.SZ | buy | 100 | 156.0 | 0.001 | 156.16 | 156.16 | 5.0 | 5.0 | ✅ |
| 6 | 2026-09-14 | 600519.SH | buy | 200 | 1672.0 | 0.0005 | 1672.84 | 1672.84 | 83.64 | 83.64 | ✅ |
| 7 | 2026-09-14 | 000858.SZ | buy | 200 | 127.2 | 0.002 | 127.45 | 127.45 | 6.37 | 6.37 | ✅ |
| 8 | 2026-09-21 | 300308.SZ | buy | 100 | 171.0 | 0.001 | 171.17 | 171.17 | 5.0 | 5.0 | ✅ |
| 9 | 2026-09-28 | 600519.SH | sell | 200 | 1752.0 | 0.0005 | 1751.12 | 1751.12 | 262.67 | 262.67 | ✅ |
| 10 | 2026-09-28 | 300308.SZ | sell | 100 | 178.5 | 0.001 | 178.32 | 178.32 | 13.92 | 13.92 | ✅ |
| 11 | 2026-09-30 | 600519.SH | sell | 200 | 1768.0 | 0.0005 | 1767.12 | 1767.12 | 265.07 | 265.07 | ✅ |
| 12 | 2026-09-30 | 300308.SZ | sell | 300 | 181.5 | 0.001 | 181.32 | 181.32 | 40.8 | 40.8 | ✅ |
| 13 | 2026-09-30 | 000858.SZ | sell | 300 | 136.8 | 0.002 | 136.53 | 136.53 | 30.72 | 30.72 | ✅ |

**诚实边界（未计为通过的部分）**：2026-09 **真实历史行情**回放核对未执行——东财行情域对本机 IP 的封禁（incident 033 / issue #325）使真实 bar 与全市场成交额样本不可得，上述面板为构造数据；合成面板覆盖成交价/费用/资金/持仓的确定性算术，但**不覆盖**真实行情的价格跳变、涨跌停真实分布与成交额分位口径；真实回放挂 follow-up，须随 #325 解除补验。

## 异常记录

### 1. 真实回放受阻（东财 IP 封禁，issue #325）→ 合成替代（不等价）

见「回放核对」诚实边界。本报告不把合成回放读成「真实回放通过」；tasks.md 3.2 勾选附注说明。

### 2. 默认套件全量 E2E 未跑：8000/5173 被 docker compose 栈占用（端口预检命中）

```
$ netstat -ano | findstr ":8000 :5173"
  TCP    0.0.0.0:8000     … LISTENING  11692   （com.docker.backend.exe）
  TCP    0.0.0.0:5173     … LISTENING  11692
  TCP    [::1]:8000       … LISTENING  41684   （wslrelay.exe）
  TCP    [::1]:5173       … LISTENING  41684
```

默认 config `reuseExistingServer: true` 会静默复用该容器后端（无 TESTING=1、库指向生产/开发库），跑出的计数无意义且种子会写开发库——按派单「端口预检；占用即停」纪律**跳过**。作为替代证据：(a) 本 change 新增 spec 单跑 2/2 绿；(b) 其所属隔离套件全量 16/16 绿（含既有 14 例，零回归）。历史基线参考（上一份 2026-10-13 验证报告记录）：默认套件 25 passed / 13 failed / 1 skipped，失败集合全为该机环境性类别——本报告不据此声称今天的默认套件状态。

### 3. mypy 基线 10 errors（既有，非本 change）

见汇总命令证据：错误行全部不在本 change 的 diff hunk 内（本 change 改动 hunk 位于 api.py 739–775 区的 seed 分支；错误在 436/1307/1496/2240 与 evals/*）。改动文件 `execution/store.py`、`outcome/track_record/model.py` 零错误。

### 3b. 后端全量唯一失败 —— 父目录 .env 泄漏（既有，机器级；非本 change）

见汇总命令证据首段：`tests/llm/test_resolver.py::…::test_empty_follows_registry_default`（断言 128000 实得 256000）。归因实验与处置同上一份报告异常记录 4：单跑通过、与 import `api.py` 的用例同跑即复现；本 change 不触碰 `llm/` 与 dotenv 路径，计数（5343 passed）为本 change 分支实测。

### 4. 前端全量 Vitest 观测到 1 次负载抖动（复跑不复现）

首轮全量 `npx vitest run` 出现 1 failed（`src/test/trackRecord/calibrationPage.test.tsx`：`getByText('0.1200')` 未命中），单文件复跑 4/4 绿、全量复跑 82 files/688 tests 全绿——判定为并行负载下的既有抖动，与本 change 无交集（该文件不 import 本次改动模块）。

### 5. 已知展示限制（如实记录，非缺陷）

paper 双曲线**不随战绩页「时间跨度」偏好（3m/6m/1y）裁剪**——后端 `paper` 块为共同日期对齐结果、无窗口参数，时间跨度仅作用于既有 equity-curve 序列（单曲线模式）。口径影响：开启 paper 数据后曲线窗口 = paper 全期。如需对齐窗口须在 delta 层（接口加 span 参数）另行变更，本 change 不做前端私裁。

## 结论

- 自动化证据：Task 3/4 的 spec 场景（下单闭环 / 涨跌停挂单 / 双曲线展示 + 执行损耗 -1.2%）均由测试钉死；后端全量 5343 passed（唯一失败为既有 .env 泄漏用例，见异常记录 3b）、前端全量 82/688 绿、构建通过；E2E 新增 spec 2/2、所属隔离套件 16/16 绿；回放核对 13/13 逐笔一致。
- **人工终审签字 pending**（交互类变更的门禁要求：页面图例/损耗标注的可读性、DOM 图例区与 canvas 图表的并置观感、README 免责声明的措辞与位置需人工确认）。
- 未通过/未覆盖项：真实行情回放（#325 阻断）、默认套件全量计数（端口占用跳过）——均如实留痕，见异常记录。
