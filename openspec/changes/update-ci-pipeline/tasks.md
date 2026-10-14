# Tasks: update-ci-pipeline

## 1. P0 门禁编排（workflow YAML）
- [ ] 1.1 `ci.yml` + `e2e-playwright.yml` 加 concurrency 组（`ci-${{ github.ref }}`，`cancel-in-progress: true`）
- [ ] 1.2 `ci.yml`：前端 vitest 步骤提前到后端 pytest 之前；`e2e-playwright.yml`：套件顺序改为 eval-ops → 战绩页 → 默认 → 时间轴（耗时升序）
- [ ] 1.3 Playwright 浏览器缓存（`~/.cache/ms-playwright`，键绑 `package-lock.json` + 版本）加进 e2e 作业

## 2. P1 抖动统计与 nightly
- [ ] 2.1 `scripts/ci/collect_flaky.py`：解析 Playwright JSON 报告 → 各套件 passed/flaky/failed/skipped 落 `reports/e2e-flaky/<run>.json`（workflow 末尾调用）
- [ ] 2.2 `scripts/ci/aggregate_flaky.py`：读全部记录 → 各 spec 抖动率汇总报告（markdown + 有效 n/统计窗口披露）
- [ ] 2.3 @live nightly 失败时 `actions/github-script` 建/更新 issue（同 run 链接去重）

## 3. 验证
- [ ] 3.1 全量测试通过（后端 + 前端）
- [ ] 3.2 在 PR 上跑通全部 workflow（证据：CI 链接）；`git fetch` 核对 main 分支保护的 required checks（人工）
- [ ] 3.3 既有测试零修改（红线）
- [ ] 3.4 验证报告落 `tests/validation/2026-10-14-update-ci-pipeline-validation.md`
