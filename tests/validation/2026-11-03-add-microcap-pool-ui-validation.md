# 人工验证报告: add-microcap-pool-ui

**日期**: 2026-11-03
**验证人**: agent（ZCode，经用户指派「小市值选股精选池还没有ui，你做一下」）
**关联 delta**: openspec/changes/add-microcap-pool-ui/
**分支**: feature/microcap-pool-ui（HEAD c70abefe）
**E2E 门禁**: 见 §3（受影响 spec 集确定性全绿 ×2；本机并行全套件 flake 归因见 §4，权威全套件门禁=CI 默认 config）

## 1. 验证环境

- worktree `.worktrees/microcap-pool-ui`，隔离栈：uvicorn 8001（TESTING=1 + 独立测试库 env，与生产物理隔离）+ vite 5174（`VITE_API_TARGET=127.0.0.1:8001`）
- 造数：`POST /api/test/seed`（真实写 `data/test-e2e-screening.db`）：main 档（600519 贵州茅台 / 000001 平安银行）+ microcap 档（300001 特锐德 / 300750 宁德时代，`frontier_gate=observation_only:missing=delist_pool,regime_data`，`exec_constraints={suggested_single_cap:0.05, slippage_tier_bump:1}`）
- 走查方式：ZCode 内置浏览器（IAB 1440×900）真实点击 + `getByTestId` 定点断言 + 全页截图
- 首次走查曾误连生产栈（手动 vite 未带 `VITE_API_TARGET` 默认代理 8000，页面显示生产数据）——发现后重启 vite 修正，重走全部步骤；此坑已记入本报告附录与 memory

## 2. 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果（真实浏览器） | 通过 |
|---|---|---|---|---|
| 缺省主档视图 | 是（screening-page.spec 既有用例） | 主档池表 + 档位切换在 | `screening-row-600519/000001` 可见、`aria-selected=true` 在主档 chip、分析按钮在（截图 01） | ✅ |
| 切换到小市值视图 | 是（新增用例①） | 观察横幅列缺失要点 + 约束 + 声明 + 无分析按钮 | 横幅=「观察模式：以下前置未就绪 —— 退市股池未就绪、拥挤度数据未就绪（仍不产生订单）」；约束=「建议执行约束（记录字段，非生效约束）： 单票上限 5% · 滑点档位提升 1 档」；声明=「小市值通道 v1 仅观察…」；`screening-analyze-btn` count=0（截图 02） | ✅ |
| 门禁 ready 两态可区分 | 是（新增用例②） | 「可执行候选」绿横幅，观察横幅消失 | `gate-ready` textContent=「前置齐备，可执行候选（仍不自动产生订单）」，`gate-observation` count=0 | ✅ |
| 小市值空态 | 是（新增用例③） | 专用空态、非报错、主档数据不冒充 | `screening-microcap-empty`=「尚无小市值批次：等待小票通道日批落库」，`screening-error` count=0，主档行 count=0 | ✅ |
| 切回主档数据不受影响 | 是（新增用例④） | 主档行/分析按钮恢复、门禁横幅消失 | `mainRowBack=true`、`analyzeBtn=1`、横幅 count=0 | ✅ |
| 视觉/文案主观项（E2E 覆盖不到） | 否 | 布局可读、披露文案不误导、观察语义醒目 | 逐项人工读屏：观察横幅警示色醒目、约束「记录字段，非生效约束」标注清晰、「不产生订单」双处可见；主档因子信号卡在小市值视图正确隐藏（防主档口径误导）；小市值视图口径文案切换为「小票通道阈值（流动性下限 1000 万）· L3 keep」 | ✅ |

**截图证据**: `tests/validation/assets-2026-11-03-microcap-pool-ui/`（01 主档视图 / 02 小市值观察态 / 03 ready 态 / 04 空态；03/04 为 IAB clip 坐标伪影图——双影为截图工具坐标系数问题非页面缺陷，页面渲染以 01/02 全页截图与 DOM 断言为准，见附录）

## 3. E2E 门禁证据

| 轮次 | 运行器 | 结果 |
|---|---|---|
| Task 4 | 隔离 config，screening-page.spec 串行 | **11 passed**（既有 7 + 新增 4） |
| §2 走查同日 | 隔离 config，受影响 spec 集（screening/smoke/daily-ops/settings-screening-rules/market-regime/event-watchlist/snapshot-banner） | **33 passed** |
| 并行 v2 | 默认 config + 端口覆盖（8001/5174） | 73 passed，3 failed（daily-ops 补跑、factor-monitor 6b、本 delta 用例①——共享 screening 库跨文件竞态+vite WS 抖动） |
| 并行 v3 | 同上 | 65 passed，2 failed（snapshot-banner、streaming——**均非本 delta 文件**，本 delta 用例全过） |
| 并行 v4 | 同上 | 85 passed，1 failed（**既有** hover 浮层用例——flake 受害者轮换，本 delta 用例全过） |

**归因**：v2–v4 失败集随跑次轮换且不含稳定指向本 delta 的失败；受影响 spec 集在串行/低并发下两次确定性全绿。失败族 = 本机 8-worker 并行 + 生产栈同机资源压力下的时序 flake（issue #328 家族，既有已知）。**权威全套件门禁 = CI 默认 config（PR 触发，retries=1）**，本报告不替代它。

## 4. 过程异常记录（不影响本 delta，留档）

1. **隔离 config 双跑 timeline spec 族**：`playwright.isolated.config.ts` 只 ignore `thinking-timeline*`，重复运行依赖 `STUB_SCENARIO` 的 spec（agui-toolcall/pipeline 族等）→ 大面积假红 + 栈拖死。正确姿势=默认 config + `E2E_BACKEND_PORT/E2E_FRONTEND_PORT/E2E_API_BASE/E2E_FRONTEND` 端口覆盖（默认 config 的 testIgnore 清单才是对的）。已入 memory `e2e-isolated-config-runs-timeline-specs`。
2. **手动 vite 忘带 `VITE_API_TARGET`**：代理默认 8000，页面短暂显示生产数据；重启修正后重走全部验证。
3. **后端全量 pytest**：7200 passed / 4 failed + 25 errors 全在 `tests/test_deploy_script.py`（MSYS sh.exe NtCreateDirectoryObject 崩溃 + `git push` 远端 128）——main 检出同文件同模式红（1 failed/25 errors），环境性既有红，与本 delta 无关。
4. 前端全量 976 passed / tsc 0 error；`uv run ruff check` 过；mypy 98 errors = 既有基线 0 新增。

## 结论

- [x] 受影响 E2E 场景确定性全绿 + 真实浏览器走查全过，交互行为符合 delta spec
- [ ] 全套件门禁：**待 CI 默认 config（PR 触发）绿后收口**——本机并行全套件存在既有 flake 族，不作为门禁判据（见 §3/§4）
- [ ] archive 待 CI 绿后执行（openspec archive --yes 自带 sync 合并）
