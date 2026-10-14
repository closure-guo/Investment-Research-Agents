# Proposal: update-ci-pipeline

## Why

- CI 现状是两个 workflow 各一个顺序 job：任一环节失败时后续全停、连推同一分支会叠加排队（无 concurrency 组）、四个 E2E 套件顺序执行（13 分钟）、@live nightly 失败无人感知。
- 实测暴露的缺口：前端 761 例单测此前从未进 CI（PR #330 已补步骤，但本次补进暴露了一个 CI 确定性失败的用例——`followup-resume-after-switch`，issue #331）；Playwright 浏览器每 run 重新下载；抖动只在本地被观察，无统计，无法回答「哪些 spec 在抖」（#328 排查缺度量）。
- 项目纪律要求「指标进入处置前先分桶归因」——当前 CI 对 flaky/抖动**零记录**，归因无据可依。

## What Changes

- `ci.yml` 与 `e2e-playwright.yml` 增加 concurrency 组（同分支连推自动取消上一次运行）。
- 门禁作业按 fail-fast 排序：`ci.yml` 里前端单测提前到后端测试之前；E2E job 里快套件（eval-ops/战绩页）排到慢套件（默认/时间轴）之前。
- Playwright 浏览器目录（`~/.cache/ms-playwright`）纳入缓存，与既有 uv/npm 缓存同列。
- 每个 E2E run 把各套件的 passed/flaky/failed/skipped 计数落成 JSON artifact（`reports/e2e-flaky/`），nightly 聚合成一页报告，供 #328 类时延族与抖动治理归因。
- @live nightly 失败时自动建/更新一个 issue（不得静默红）。
- 核对 main 分支保护的 required checks 与上述作业一致（人工核对步骤）。

非目标（Out of scope）：

- 不把四套 E2E 拆成并行 job（job 数 ×4 环境搭建机时换墙钟，归因收益可用报告层拿到；作为可选项记录在设计中）。
- 不做镜像构建/推送/部署自动化（CD）——本仓库当前为本地开发挂载部署，CD 属于后续基础设施决策。
- 不把视觉基线门禁放进 CI（基线在 Windows 采集，跨平台字体渲染会假红；决策已写进 `playwright.ui-baseline.config.ts`）。

## Capabilities

### New Capabilities
- `ci-pipeline`: CI 门禁编排、抖动统计与 nightly 报告。

## Impact

- **仓库**：`.github/workflows/ci.yml`、`.github/workflows/e2e-playwright.yml`、新增 `scripts/ci/collect_flaky.py`（flaky 计数落盘）与 `scripts/ci/aggregate_flaky.py`（nightly 汇总）、（可选）`reports/e2e-flaky/` 输出目录。
- **既有测试**：零修改。
- **依赖**：无新增第三方依赖（纯 workflow + stdlib Python 脚本）。
