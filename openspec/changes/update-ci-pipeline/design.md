# Design: update-ci-pipeline

## Context

仓库现有两个 workflow（均 PR/push 触发）：`ci.yml`（单 job 顺序：字体→依赖→密钥→ruff→format→mypy（汇报）→后端 pytest→评估三道门→前端 vitest）与 `e2e-playwright.yml`（单 job 顺序跑四套 E2E 配置，playwright-report 已上传 artifact）。无 concurrency 组；无 Playwright 浏览器缓存；无抖动统计；@live nightly 失败无人知。

## Goals / Non-Goals

**Goals:**
- 连推同一分支自动取消在跑 run（省分钟）。
- fail-fast 排序（前端/快套件先跑）。
- Playwright 浏览器缓存复用（省搭建时间）。
- 抖动统计落盘 + 每日汇总（为 #328 类时延族与抖动治理提供度量）。
- @live 失败不静默（建 issue）。

**Non-Goals:**
- 不拆 E2E 为并行 job（4× 搭建机时换墙钟，且 job 数膨胀；抖动归因用统计层拿到）。保留为可选后续项。
- 不做镜像构建/推送/部署自动化（CD）——本仓库当前为本地开发挂载部署。
- 不改动任何产品代码与既有测试断言。
- 不把视觉基线门禁放进 CI。

## Decisions

- **抖动统计放 CI artifact + 本地聚合脚本**，不引入数据库/外部服务：`collect_flaky.py` 在 workflow 末尾解析 Playwright 的 JSON 报告（`--reporter=json`）写出 `reports/e2e-flaky/<run>.json`，nightly 里 `aggregate_flaky.py` 读全部 artifact 产出汇总 markdown + 上传 artifact。取舍：零外部依赖、可离线复核。
- **fail-fast 排序只改执行顺序，不改断言**：步骤重排是安全的最小改动；**代价说明**：默认套件提前时其失败会挡住后面慢套件——这正是要的"先快先红"，而时间轴套件最后跑避免长耗时遮挡快失败。
- **@live 失败建 issue 用 `actions/github-script`**（GitHub 官方 action，仓内不引依赖），按 run 链接去重（若已有 open issue 含同 run 链接则更新评论而非新建）。
- **缓存键绑定版本**：`playwright` 浏览器缓存键 = `${{ hashFiles('tests/e2e/playwright/package-lock.json') }}` + Playwright 版本，防换版本后命中错库。

## Risks / Trade-offs

- **concurrency 取消**：误取消手动触发的 run（workflow_dispatch）是预期行为；如有需要后续可对该事件类型豁免。
- **缓存命中错误**：键未绑定版本时可能命中旧浏览器 → 键已绑定 lockfile + 版本号，风险收敛。
- **@live 自动建 issue 可能噪声**：若 nightly 长期红会重复评论——用"同 run 链接去重 + 已开 issue 不重复新建"缓解；属低风险。
