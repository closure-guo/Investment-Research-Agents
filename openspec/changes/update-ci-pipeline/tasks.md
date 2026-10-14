# Tasks: update-ci-pipeline

## 1. P0 门禁编排（workflow YAML）
- [x] 1.1 `ci.yml` + `e2e-playwright.yml` 加 concurrency 组（`ci-${{ github.ref }}`，`cancel-in-progress: true`）
      —— 组前缀按 workflow 区分（共用组名会互取消，见验证报告 §四.1）；e2e 的 schedule 豁免取消（§四.2）
- [x] 1.2 `ci.yml`：前端 vitest 步骤提前到后端 pytest 之前；`e2e-playwright.yml`：套件顺序改为 eval-ops → 战绩页 → 默认 → 时间轴（耗时升序）
- [x] 1.3 Playwright 浏览器缓存（`~/.cache/ms-playwright`，键绑 `package-lock.json` + 版本）加进 e2e 作业
      —— live 作业同原理加缓存（键绑 `uv.lock`，见验证报告 §四.3）

## 2. P1 抖动统计与 nightly
- [x] 2.1 `scripts/ci/collect_flaky.py`：解析 Playwright JSON 报告 → 各套件 passed/flaky/failed/skipped 落 `reports/e2e-flaky/<run>.json`（workflow 末尾调用）
      —— 机制先实证（OUTPUT_NAME 相对 configDir；探针报告固化为夹具）；10 例测试先红后绿
- [x] 2.2 `scripts/ci/aggregate_flaky.py`：读全部记录 → 各 spec 抖动率汇总报告（markdown + 有效 n/统计窗口披露）
      —— 7 例测试先红后绿；flaky-report 作业（nightly + dispatch）经 `gh run download` 拉 artifact 后调用
- [x] 2.3 @live nightly 失败时 `actions/github-script` 建/更新 issue（同 run 链接去重）
      —— 逻辑独立成 `scripts/ci/report_live_failure.cjs` + node 桩件测试 4/4；CI 探针实证见验证报告 §三

## 3. 验证
- [ ] 3.1 全量测试通过（后端 + 前端）
- [ ] 3.2 在 PR 上跑通全部 workflow（证据：CI 链接）；`git fetch` 核对 main 分支保护的 required checks（人工）
- [x] 3.3 既有测试零修改（红线）—— diff 不含任何既有测试文件；新增测试：`tests/test_collect_flaky.py`、`tests/test_aggregate_flaky.py`、`scripts/ci/report_live_failure.test.cjs`
- [ ] 3.4 验证报告落 `tests/validation/2026-10-14-update-ci-pipeline-validation.md`
