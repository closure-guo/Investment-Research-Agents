# ci-pipeline Specification

## Purpose
定义 CI 门禁的编排契约：同分支并发运行取消（连推不叠加分钟）、门禁作业 fail-fast 排序（最快失败最快反馈）、依赖与 Playwright 浏览器缓存（键绑 lockfile，同版本不重下）、E2E 抖动统计落盘与 nightly 汇总（各 spec 抖动率，披露统计窗口与有效 n，供时延/抖动族归因）、@live nightly 失败不静默（自动建/更新 `ci-failure` issue，同源去重、rerun 幂等）。不含 E2E 并行 job 拆分、镜像构建/推送（CD）与视觉基线门禁（基线在 Windows 采集，跨平台字体渲染会假红）。
## Requirements
### Requirement: 同分支并发运行取消

CI workflow SHALL 声明 concurrency 组并按 `github.ref` 分组：同一分支（或同一 PR）新一次运行触发时，在跑的上一次运行 SHALL 被取消（`cancel-in-progress: true`），避免分钟叠加。

#### Scenario: 连推同一分支自动取消

- **GIVEN** 分支 feat/x 的 CI 正在运行
- **WHEN** 同一分支再次 push 触发 CI
- **THEN** 在跑的旧 run 被取消，新 run 接管

### Requirement: 门禁作业 fail-fast 排序

`ci.yml` 的 `lint-and-test` 作业 SHALL 将前端单测（vitest）置于后端单元测试（pytest）之前执行；`e2e-playwright.yml` 的 stub 作业 SHALL 按套件耗时升序执行（eval-ops → 战绩页 → 默认 → 时间轴），以便最快失败、最快反馈。

#### Scenario: 前端失败先反馈

- **GIVEN** 一次改动同时引入一个前端单测失败与一个后端单测失败
- **WHEN** `ci.yml` 运行
- **THEN** 前端失败先于后端失败出现在日志（前端步骤先执行）

### Requirement: 依赖与浏览器缓存

CI SHALL 缓存 `~/.cache/ms-playwright`（Playwright 浏览器）与既有 uv/npm 缓存；缓存键 SHALL 绑定 `package-lock.json` 与 Playwright 版本，重复运行 SHALL NOT 重新下载浏览器。

#### Scenario: 重复运行不重下浏览器

- **GIVEN** 上一次运行已缓存 Playwright 浏览器
- **WHEN** 同版本浏览器再次运行
- **THEN** `npx playwright install chromium` 不重新下载（命中缓存，安装耗时显著下降）

### Requirement: 抖动统计落盘与汇总

每个 E2E run SHALL 把各套件的 passed/flaky/failed/skipped 计数落成机器可读 JSON artifact；每日聚合脚本 SHALL 汇总出各 spec 的抖动率并产出一份报告（`reports/e2e-flaky/`），供抖动归因（含 #328 类时延族）使用。

#### Scenario: 抖动计数可回查

- **GIVEN** 某日 E2E 跑了 3 次、其中 1 次有 2 例 flaky
- **WHEN** 运行聚合脚本
- **THEN** 报告含每个 spec 的 flaky 计数与抖动率，且该日 2 例被计入
- **AND** 报告披露统计窗口与有效 n

### Requirement: nightly 失败不静默

`@live` nightly 失败时，workflow SHALL 建/更新一个带 `ci-failure` 标签的 issue（同一失败源更新不重复开新 issue）；不得仅以红色 run 收尾。

#### Scenario: nightly 失败建 issue

- **GIVEN** @live nightly 运行失败
- **WHEN** workflow 结束
- **THEN** 存在一个 issue 记录失败 run 链接与失败摘要

