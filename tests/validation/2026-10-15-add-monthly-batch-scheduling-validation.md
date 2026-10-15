# add-monthly-batch-scheduling 实施验证报告

**日期**: 2026-10-15
**范围**: 月批排程（记忆提炼 + 因子提案两条休眠链路接线）+ 注册表排程粒度扩展（日/周/月）
**交付**: `memory/monthly.py`、`factors/monthly.py`（新增）；`outcome/scheduler.py`（cron 粒度分支 + 两任务注册）、`outcome/ops/jobs.py`（10→12）、`memory/__init__.py`、`memory/semantic.py`（**空素材防线修复**）、前端三处（`types.ts` / `EvalOpsPane.scheduleText` / `DailyOpsPage` 时间线）、E2E 两 spec、测试五处计数更新

## 一、实施摘要

- **两条休眠链路接线**（2026-10-15 审计发现，与前三条同类：实现完整、零生产调用方）：
  - `memory_extract_monthly`（每月 1 日 09:00）：已结算决策近 90 天窗口 → `extract_candidate_clauses`（LLM 优先/规则兜底）→ 候选态落库（`/memory` 待确认可见；未确认不注入契约不变）；
  - `factor_proposal_monthly`（每月 1 日 10:00）：`propose_broad_batch` → 同批一次 `admit_candidates` 五道闸 → 过闸入库/未过留原因（摘要 `rejections`）；网关不可用 fail loud。
- **排程粒度扩展**：`_cron_from_spec` 增 `day_of_month` 分支（月批 `CronTrigger(day=...)`；日批 `day_of_week` 逐字不变）；前端 `scheduleText` 与日批运维时间线支持「每月 N 日 HH:MM」；非排程日文案「未到本期排程日」（收紧诚实性）。
- 任务走统一通道（调度/运行历史/手动补跑/单飞锁）；`eval-ops-console` 规范 MODIFIED 一次修净（粒度通用化 + 当前 12 个，既有三场景保全）。

## 二、实跑暴露并修复的缺陷（本 delta 最有价值的发现）

**空素材走 LLM 编造**：本地真实栈手动补跑（空库 + **真实网关**）首次运行得
`{settled_count: 0, candidates: 1, path: "llm"}`——`extract_candidate_clauses` 的 LLM 分支只判 `client is not None`，**不判素材为空**：空输入下模型产出被当候选入库，违反本 delta 规范场景「无素材 MUST NOT 以编造条款冒充提炼产物」。

- 根因：该函数（add-decision-memory 交付）此前**零生产调用方**——空输入 × 真实网关的组合从未被触达；本 delta 首次接线即暴露（与三条休眠链路的教训同族）。
- 修复：`extract_candidate_clauses` 入口增空素材早退（不调 LLM，注释记实跑出处）；
- 回归：新增 `test_empty_source_never_calls_llm`（会编造的 stub client 钉死「不得触达」）；
- **修复后真实网关复跑**：`{window_days: 90, settled_count: 0, candidates: 0, path: "rule"}` ✓ 防线成立。

## 三、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 靶向（我的复跑） | `pytest tests/memory/test_monthly.py tests/factors/test_monthly.py tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q` | **85 passed** |
| 修复后 memory+factors | `pytest tests/memory tests/factors/test_monthly.py -q` | **64 passed** |
| 全量（代理） | `uv run pytest -q` | 5622 passed / 4 failed——全部存量/环境性：3 例 `@live`（本机凭据真跑）+ `test_resolver` 顺序依赖存量污染（单跑 29 passed，与本 delta 无涉，另见宿主 .env 泄漏归因） |
| 前端 | `npm test -- --run` + `npx tsc -b` | 95 files / **802 passed**；tsc 干净 |
| E2E（isolated config） | `eval-ops-console.spec.ts` + `daily-ops.spec.ts` | **5 passed + 5 passed**（含月粒度文案与 12 任务计数断言） |
| lint | ruff check/format 改动文件 | 全过 |

**真实 API（本地隔离实例，真调度器）**：任务数 **12**；两月批排程 `{day_of_month: 1, 09:00/10:00 Asia/Shanghai}`、下次触发 **2026-11-01**；手动补跑——记忆提炼 ok（见 §二 修复前后两态）、因子提案 **fail loud**（`ValueError: 快照库为空：无法构造因子准入面板（先跑 market_snapshot_daily）`，封禁期预期）。

## 四、既有测试最小更新清单（计数/形状类，逐条）

1. `tests/outcome/test_ops_jobs.py` **3 处**（len 10→12 ×3 + 集合 +2 + 新增 `TestMonthlyBatchRegistration`）
2. `tests/outcome/test_scheduler.py` 2 处（len 10→12 + 集合 +2 + 新增 `TestMonthlyCronGranularity`）
3. `tests/test_ops_api.py` 1 处（排程键集断言推广为两合法形态：`day_of_week` 或 `day_of_month`）
4. E2E `eval-ops-console.spec.ts`（JOB_IDS +2、标题 8→12、月粒度断言）+ `daily-ops.spec.ts`（计数 10→12、下标 +2、月批文案）

## 五、裁决记录（代理请示 5 项）

1. 记忆提炼缺省客户端用真实网关适配器（quick 档，失败自动规则兜底）——**接受**；内联 prompt 非 `prompts/*.md` 权威源属 add-decision-memory 既有状态，记后续项。
2. 未过闸原因落运行摘要 `rejections`（不动 `factor_events` 既有约束）——**接受**。
3. 分桶存量/算子 IC 取库内全部因子（含 retired）——**接受**（docstring 已记）。
4. 非排程日「未到本期排程日」文案——**接受**（诚实性收紧）。
5. §5.2 页面可见性（`/memory` 页出现候选）与 §5.4 报告——**本报告即 §5.4**；页面可见性挂合并部署后核验（当前运行栈为主检出，本 delta 未合并）。

## 六、遗留

1. 合并后部署核验：`/memory` 页在真实月批产出候选后可见（当前空库 → 空态如实）；运维页两新任务卡片「每月 1 日 HH:MM」。
2. 因子提案真实运行待东财解封（#325）；记忆提炼**不依赖行情**——已结算决策出现后即可真实产出候选。
3. 提炼 prompt 未走 `prompts/*.md` 权威源（既有状态，后续 delta 可收编）。

## 七、结论

两条休眠链路接线完成（统一通道三态在真实 API 验证）；排程粒度扩展使月批可注册/可读；**实跑暴露并修复「空素材走 LLM 编造」缺陷**（防线 + 回归 + 真实网关复跑三证）；既有测试仅计数/形状类最小更新。
