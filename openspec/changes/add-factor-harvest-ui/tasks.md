# Tasks: add-factor-harvest-ui

> 上线顺序 = 任务顺序。TDD：先写失败用例再实现；每层测试先行。

## T1 后端只读端点

- [x] 先红：`tests/test_api_factor_research.py::TestHarvestChainEndpoint` 覆盖缺失/损坏/漏斗/运行窗口/台账
- [x] `GET /api/factor-research/harvest`：job / progress / funnel / recent_admitted / recent_rejected
- [x] 运行窗口口径：`batches_done` = 账本中 `created_at >= started_at` 的不同 `batch_id`；ETA 按本轮处理速率（**仅运行中**，收尾后为 null，见 T7）
- [x] 缺失/损坏 → `available=false`（progress/funnel 为 null，不编造）
- [x] 零写入（请求前后状态文件字节不变）

## T2 前端区块

- [x] 先红：`frontend/src/test/factorResearchPage.test.tsx` 新增「收割链路」两用例（渲染 / 空态不拖垮整页）
- [x] `types.ts` 增 `HarvestChainResponse` 及子块类型
- [x] `lib/factorResearch.ts` 增 `fetchHarvestChain()`
- [x] 页面新增区块：进度条 + 漏斗四卡 + 拒收分布 + 最近入库/拒收；区块级错误与重试
- [x] 运行中批内活性：秒级时钟「本批进行中 · 已 X（进度按批落盘）」+ 心跳逐候选「本批 n/60 · 转译中」
- [x] 轮询复用 10s；取数排在主快照之后（首屏顺序稳定）

## T3 测试与静态检查

- [x] 后端 `uv run pytest tests/test_api_factor_research.py -q` → 67 passed
- [x] 前端 `npx vitest run src/test/factorResearchPage.test.tsx` → 40 passed
- [x] `npx tsc -b` 通过；`ruff check` 通过；新代码 `mypy` 0 错误

## T4 E2E 只读门禁

- [x] `tests/e2e/playwright/tests/factor-harvest-ui.spec.ts`：真实后端、**不 mock 业务接口**
- [x] 断言区块存在 + 「进度/空态」二居其一 + 漏斗四卡与分布结构

## T5 部署与激活

- [x] 重建 backend / frontend 镜像（两容器均为生产形态，无源码挂载）
- [x] 端点实际返回真实读数（候选/已处理/到闸/入库与状态文件一致）
- [x] 浏览器实测截图（`tests/e2e/playwright/tmp/harvest-ui.png`）

## T6 文档与验收

- [x] 设计文档 `docs/superpowers/specs/2026-10-22-factor-harvest-ui-design.md`
- [x] `openspec validate add-factor-harvest-ui --strict`
- [x] 人工验证报告 `tests/validation/2026-10-22-factor-harvest-ui-validation.md`（含截图与端点读数）
- [ ] archive（`tasks.md` 全勾 + 验证通过后执行）

## T7 收尾口径修正（2026-10-23 实测「已完成 + 预计还需 38 分」）

- [x] 先红：`test_finished_run_reports_no_eta[ok|failed]`（收尾后仍外推，实测 15445s）
- [x] 后端：`eta_s` 仅在 `job.status=running` 时计算；已收尾一律 `null`
- [x] 先红：前端「已收尾：不渲染预计还需，队列归属下次自动跑」
- [x] 前端：完成态改「本轮 N 批 · 入库 x · 拒收 y」+「队列剩余 M · 待下次 MM-DD HH:mm 自动跑」，
      去掉 `/batch_limit` 分母与外推 ETA
- [x] E2E spec 按运行/收尾两态分层断言（不再固定要求「批次」字样）
- [x] `uv run pytest tests/test_api_factor_research.py -q` → 70 passed；`npx vitest run src/test/factorResearchPage.test.tsx` → 41 passed；`npx tsc -b` / `ruff check` 通过
- [x] 真实端点复核：`status=ok`、`finished_at=21:12:23`、`pending=39`、`eta_s=null`、`next_fire_time=2026-10-23T15:55:00+08:00`
- [x] `tests/factor-harvest-ui.spec.ts` 对真实栈通过（1 passed）

## T8 完成态进度条修正（2026-10-23 实测「已完成 + 进度条卡 94%」）

> 现象：徽标「已完成」，紧邻的绿色进度条停在 94%（实测 `candidates=958` / `processed=896` = 93.5%）。
> 根因：进度条画的是 `funnel.processed / funnel.candidates`——**全局队列消化率**，不是单轮完成度；
> 上一轮 `drained=true` 已跑完（`run_id=227`，21:12 收尾），此后 RD-Agent 又追加了新候选，
> 该比值永远回不到 100%。与 T7 同类（「已完成」旁边的读数被读成「没跑完」）。

- [x] 先红（单测）：收尾用例增 `queryByTestId('factor-research-harvest-bar')` 为空 → 实测收到 `width: 96%` 的进度条
- [x] 先红（E2E·真实栈）：`factor-harvest-ui.spec.ts` 收尾分支增 `bar.toHaveCount(0)` → 实测 1 failed
      （旧镜像 bundle；失败截图留证 `tests/e2e/playwright/tmp/harvest-bar-before-fix.png`）
- [x] 前端：进度条只在 `job.status=running` 时渲染；收尾态未消化读数由「已处理 N」+「队列剩余 M ·
      待下次 …自动跑」承担（`FactorResearchPage.tsx`）
- [x] delta spec：进度条口径写入 Requirement（「队列消化进度条」，仅运行中）+ 新增 Scenario
      「运行中渲染队列消化进度条」「已收尾不渲染进度条」
- [x] `npx vitest run src/test/factorResearchPage.test.tsx` → 41 passed；全量 `npx vitest run` → 102 files / 916 passed；`npx tsc -b` 通过
- [x] 真实端点复核：`status=ok`、`finished_at=2026-10-22T21:12:23`、`candidates=958`、`processed=896`、
      `pending=62`、`eta_s=null`（比值 93.5%）
- [x] 重建前端镜像（生产形态）+ `tests/factor-harvest-ui.spec.ts` 对真实栈通过（1 passed）
      （bundle `index-B40QdUkn.js` → `index-Bxae_6oK.js`；重建前 `GET /api/sessions` running=0，backend 未重启）
- [x] 人工验证报告追加 §7（修复前/后真实栈截图 + 端点读数 + 红/绿对照）：`tests/validation/2026-10-22-factor-harvest-ui-validation.md`
- [x] E2E spec 清 P0：`if (await progress.isVisible())` 条件绕过（scan #5a）改为**端点读数驱动**
      （`request.get('/api/factor-research/harvest')` + `test.skip(理由)`），空态独立成例；
      `scan.sh` 由 1 P0 → **0 hit**；`npx playwright test tests/factor-harvest-ui.spec.ts` → 1 passed / 1 skipped
      （顺带修好默认 stub config 下 `factor-research-harvest-status` 不存在会挂死的隐患：状态不可得时按收尾口径断言）
- [x] `uv run pytest tests/test_api_factor_research.py -q` → 70 passed（后端未改动，回归确认）
- [x] 独立评审（`e2e-reviewer` 24 反模式，静态）：**Approved / 无 P0**；按建议硬化三处——
      运行中/已收尾拆成各自命名的用例（「已收尾：不渲染队列消化进度条」，回归断言不再可能被静默挪走）、
      `fetchHarvest` 移到 `page.goto` 之后（消除与页面 10s 轮询的分支翻转竞态）、
      RTL 等待句柄由区块外壳改为数据块（`factor-research-harvest-status`）
- [x] P1-1 已补（2026-10-23 同一变更内做完，未另开）：默认 config 增 `FACTOR_RESEARCH_HARVEST_STATE_PATH` /
      `_CANDIDATES_PATH` / `_HEARTBEAT_PATH` 测试隔离路径；`.gitignore` 收敛为
      `data/test-e2e-factor-research-*.json*`；spec 以**真实文件** seed（不 mock 业务接口）
- [x] 换 stub 栈实测才暴露的深层依赖：收割区块整块挂在主快照 `data.available` 门控下
      （`FactorResearchPage.tsx:503` 的块一直包到 `:896`，supervisor/收割/会话历史都在里面），
      所以收割 E2E 还需要 `FACTOR_RESEARCH_STATUS_PATH` 快照；而那份 fixture 在默认套件里归
      `factor-research-monitor.spec.ts` 所有（`:122` 每例写、`:199-205` afterAll 删）——
      两文件在 `fullyParallel` 下并行时，收割用例过不过取决于调度时序（实测：单独跑默认 config
      必红，随套件跑却可能靠 race 过）。**即自 `59e8b9da` 起该 spec 在 CI 是碰运气，不是稳定门禁**
- [x] 解法（选专属套件）：`tests/e2e/playwright/playwright.harvest.config.ts`——端口对 8007/5180、
      `workers: 1` / `fullyParallel: false`、status+收割 fixture 全隔离、`reuseExistingServer: false`
      （不误连本机 8000/5173 生产栈）；CI 增一步 `harvest suite`（`flaky-reports/harvest.json`）+
      `collect_flaky --expect` 加 `harvest`
- [x] 变异验证：临时摘掉前端 `harvestRunning &&` 守卫 → 专属套件 **1 failed**（正好命中
      `toHaveCount(0)` 回归断言）；`git checkout` 复原后 **2 passed / 1 skipped**
- [x] 不干扰生产栈：专属套件全程在 8007/5180，docker 8000/5173 未停（`GET /api/sessions` 未受影响）
