# update-ci-pipeline 实施验证报告

**日期**: 2026-10-14
**范围**: `update-ci-pipeline` delta 五条需求——R1 同分支并发取消 / R2 门禁 fail-fast 排序 / R3 Playwright 浏览器缓存 / R4 抖动统计落盘与汇总 / R5 @live nightly 失败不静默
**交付物**: `.github/workflows/ci.yml`、`.github/workflows/e2e-playwright.yml`、`scripts/ci/collect_flaky.py`、`scripts/ci/aggregate_flaky.py`、`scripts/ci/report_live_failure.cjs`（+ 桩件测试）、`tests/test_collect_flaky.py`、`tests/test_aggregate_flaky.py`、`tests/fixtures/playwright-json/probe-flaky-report.json`、`.gitignore`

## 一、机制实证（先实证机制，再写实现）

1. **Playwright JSON reporter 落盘机制**（playwright 1.62.0，一次性探针实跑，探针文件已删）：
   - `PLAYWRIGHT_JSON_OUTPUT_NAME` 相对 **configDir**（config 文件所在目录）解析——源码 `playwright/lib/runner/index.js::resolveOutputFile`（无 OUTPUT_DIR 时 outputDir 回落 configDir）+ 实跑落点双重验证（探针 config 位于 `tmp-probe/` 时，报告落在 `tmp-probe/flaky-reports/probe.json`）；
   - reporter 自动创建父目录，workflow 无需 mkdir；
   - test 级 `status` ∈ {expected, flaky, unexpected, skipped}，与 list reporter 输出（1 passed / 1 flaky / 1 skipped / 1 failed）逐项一致 → 解析器主判据（attempts 反推为防御性回退）；
   - 探针报告脱敏（去堆栈/绝对路径/附件，结构保真）固化为夹具 `tests/fixtures/playwright-json/probe-flaky-report.json`；**夹具保真自证** = 逐 test 分类与报告内 stats 块逐项相等（测试首例钉死此断言）。
2. **gh CLI 下载循环**（flaky-report 作业下载段）本地实证（gh 2.92.0，与 runner 同 CLI）：`gh run list --workflow --limit --json databaseId --jq` 正常；无 `e2e-flaky` artifact 的历史 run 下载失败被 if 守卫静默跳过（candidates=5 → downloaded=0，符合预期，非错误路径）。
3. **CI runner 版本前提**：`node --test` 需 ≥18（runner 为 20/22）；`python3`（aggregate 用）为 stdlib-only，ubuntu-latest 3.12 满足（`datetime.UTC` 需 ≥3.11）。

## 二、本地验证

| 项 | 命令 | 结果 |
|---|---|---|
| collect 解析器/收数 | `uv run pytest tests/test_collect_flaky.py -q` | 10 passed（先红：`ModuleNotFoundError: scripts.ci` → 实现后绿） |
| aggregate 汇总 | `uv run pytest tests/test_aggregate_flaky.py -q` | 7 passed（同先红后绿） |
| R5 上报逻辑桩件 | `node --test scripts/ci/report_live_failure.test.cjs` | 4 passed（建单含标签补建 / 追加评论 / 同 run 幂等 / 同名 PR 过滤） |
| 本地 CLI 联调 | `collect_flaky.py`（local 与 GITHUB_* env 双路径）→ `aggregate_flaky.py` | 记录落盘 + 汇总报告（窗口 2026-09-30→2026-10-14、有效 n=2、抖动率 100.0% 披露）逐项符合夹具预期 |
| YAML 解析 | pyyaml 解析两 workflow + 作业/步骤结构清单 | 通过；套件顺序 eval-ops→track-record→default→timeline 与前端步骤先于 pytest 均经结构打印核对 |
| 后端全量回归 | `uv run pytest tests/ --ignore=tests/scripts -m "not live" -q`（CI 同命令，无 -x） | **5519 passed / 6 skipped / 1 failed**（19:51）——失败项 `tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default`，**已归因为宿主环境泄漏，非本 delta 引入**（见下「失败项归因」） |
| 前端全量回归 | `npm test -- --retry=1 --exclude 'src/test/followup-resume-after-switch.test.tsx'`（CI 同命令） | **90 文件 / 760 例全绿**（EXIT=0；与 CI 门禁同口径：总 761 例扣除 #331 隔离文件） |

### 失败项归因（后端 1 failed，非本 delta 引入）

- **失败项**：`test_empty_follows_registry_default`（断言 registry 静态默认 max_context==128000）。
- **机制实证（三连）**：宿主 `D:\WorkSpace\finance_analysis_agent\.env:50` 设 `LLM_MAX_CONTEXT=256000`（2026-10-11 起启用，见记忆档案）；`LLM_MAX_CONTEXT=256000 uv run pytest <单测>` → 复现失败（max_context=256000）；不带该变量 → 通过；模块级复跑 `tests/llm/test_resolver.py` → 29/29 全绿 → 全量套件中某前置测试把 .env 加载进 `os.environ` 后本测试被污染（顺序依赖）。
- **非本 delta**：diff 不触碰 `src/` 与 `tests/llm/`；CI 环境无宿主 .env → 不受影响（CI 侧该测试为绿）。
- **处置**：并发会话已在主检出以 `resolve_profile(..., _env={})` 显式隔离修复（未提交 WIP，本 worktree 未触碰该文件，避免互相覆盖）。
- **旁证**：main 上 838c04ae 的 push run（38507058743）为红，但失败项是另一族——`test_pipeline_watchdog_graph_done.py::test_scenario_e` 时序 flake（5001 passed 后 `-x` 中止；属 #312/incident 042 已立案的 CI 时序族，重跑即绿），与本 delta 无交集。

## 三、CI 实跑证据

**待 CI 实跑后填**（PR 链接、stub 作业套件顺序与缓存命中日志、e2e-flaky artifact、dispatch 探针的 issue 创建/rerun 幂等、flaky-report 报告、concurrency 组字段）。

## 四、实施口径说明（与 tasks 字面差异及理由）

1. **concurrency 组前缀按 workflow 区分**（`ci-${{ github.ref }}` / `e2e-${{ github.ref }}`）：tasks 1.1 字面写同一组名，但两个 workflow 共用组名会互相取消（同一 push 同时触发两 workflow → 后启动者取消先启动者），显然非意图；spec 只要求「按 github.ref 分组 + 取消在跑的上一次」。
2. **`e2e-playwright.yml` 的 `cancel-in-progress` 对 schedule 豁免**（`${{ github.event_name != 'schedule' }}`）：nightly 是唯一 @live 真链路信号，main 上的普通 push 若取消 nightly 即失去该信号（design 风险节已声明「按事件类型豁免」方向）。
3. **浏览器缓存同时加进 live 作业**（键绑 `uv.lock`）：tasks 1.3 只点名 e2e 主作业；live 作业的 Python 侧 playwright 版本由 `uv.lock` 锁定，同键同原理，省 nightly 下载。
4. **新增 `node --test` 桩件测试步骤进 ci.yml**：R5 上报逻辑属本 delta 新增代码，按「先写失败测试」纪律补桩件测试并纳入门禁（零依赖、秒级；亦属 fail-fast 排序的受益项）。
5. **`if: ${{ !cancelled() }}`（而非 `always()`）用于收数与 artifact 上传**：被并发取消的 run 不收半截数据（半截记录无归因价值，且 `always()` 会在取消时照跑）。

## 五、结论

**待 CI 实跑后填**。
