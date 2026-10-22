# Tasks: add-factor-harvest-ui

> 上线顺序 = 任务顺序。TDD：先写失败用例再实现；每层测试先行。

## T1 后端只读端点

- [x] 先红：`tests/test_api_factor_research.py::TestHarvestChainEndpoint` 覆盖缺失/损坏/漏斗/运行窗口/台账
- [x] `GET /api/factor-research/harvest`：job / progress / funnel / recent_admitted / recent_rejected
- [x] 运行窗口口径：`batches_done` = 账本中 `created_at >= started_at` 的不同 `batch_id`；ETA 按本轮处理速率
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
