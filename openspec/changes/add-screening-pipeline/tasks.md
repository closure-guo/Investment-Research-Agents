# Tasks: add-screening-pipeline

> 收口记录（2026-10-13）：勾选依据 = `tests/validation/2026-10-13-add-screening-pipeline-validation.md`
> （E2E 门禁证据 + 异常记录）。4.2 受阻不勾（东财 IP 封禁 issue #325），见该报告异常记录 1。

## 1. 日批编排
- [x] 1.1 pipeline.py 编排（快照确认→L1→L2→L3→落库）+ 失败不产残缺池单测（`tests/screening/test_pipeline.py`；批次行与池行单事务同生共死）
- [x] 1.2 批次失败触发前端横幅（复用快照告警通道）+ 单测（后端 `GET /api/screening/status` 永不 500 + pool 载荷 `stale`；前端横幅 `screening-batch-stale-banner`，E2E 用例 3）

## 2. 时效性校验
- [x] 2.1 深度分析前状态重检（停牌/一字涨跌停/新事件）+ 移除并记原因单测（纯函数 T2 + **analyze 端点接线**：被移除者不建会话不入队，响应 `removed`，审计落 `screening_removals` 表）

## 3. 前端选股页
- [x] 3.1 `/screening` 页（表格/贡献分解 hover/在榜天数/L3 理由）+ Vitest（`frontend/src/test/screeningPage.test.tsx` 7 例；路由 + 侧边栏入口）
- [x] 3.2 一键批量深度分析（并发 2，复用 SSE）+ Playwright stub 用例（后端并发上限 2 单测 + E2E 用例 5 队列徽标）

## 4. 验证
- [x] 4.1 全量测试通过（后端 pytest：全量 `tests/ -m "not live"` 5195 passed / 1 failed（唯一失败为父目录 .env 泄漏的既有用例，归因实验见验证报告异常记录 4）+ 靶向套件 153 passed；前端 Vitest 685 passed；E2E stub 门禁：本 change spec 单跑 5 passed、默认全量套件内 5 passed；既有本机环境性红按 `cc00bc0` 基线归因，见验证报告）
- [ ] 4.2 连续 5 交易日完整日批人工核对，报告落 `tests/validation/`（**受阻，不勾**：东财 IP 封禁 issue #325，`fetch_market_spot` 返回 None，无真实日批数据；合成替代证据与边界说明见验证报告异常记录 1，须随 #325 补验）
- [x] 4.3 既有测试无修改通过（红线：`tests/screening`、`tests/test_api_screening.py`、`frontend/src/test` 既有用例零改动，全绿）
