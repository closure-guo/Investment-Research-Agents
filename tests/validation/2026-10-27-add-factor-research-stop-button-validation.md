# 人工验证报告: add-factor-research-stop-button

**日期**: 2026-10-27
**验证人**: agent（Closure 授权代行）
**关联 delta**: openspec/changes/add-factor-research-stop-button/
**E2E 门禁**: tests/e2e/playwright/playwright.factor-stop.config.ts（专属端口对 8009/5182，`reuseExistingServer: false`），1/1 通过（2026-10-27 14:36 本机实跑）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 运行中点「停止」→ 二次确认 → 控制文件真写入 | 是（6b）+ 单测 | 未确认零请求；确认后 `PUT /control` 体 `{desired_running:false}` | E2E 轮询隔离控制文件 `desired_running=false` 真值；单测请求体断言一致 | ✅ |
| 开关位置语义不变 | 是（6b）+ 单测 | `pending` 期间开关仍显示实态「开」，位置不乐观翻转 | E2E `aria-checked=true` + 文案「切换中」；单测同断言 | ✅ |
| 与开关同禁用条件（pending / restart_pending / 通道不可用） | 是（6b）+ 单测 | `pending`、`restart_pending`、`honor=unavailable` 时停止按钮禁用 | E2E pending 禁用；单测覆盖 restart_pending 与 honor=unavailable 两例 | ✅ |
| 无可停止对象 → 禁用 | 是（6b）+ 单测 | 实态 stopped 且无运行意图 → 停止禁用、开关仍可开启 | E2E 推进快照到 running=false 后按钮禁用、文案「已关闭」；单测同断言 | ✅ |
| 离线意图可撤销 | 否（单测覆盖） | 快照陈旧 + `desired_running=true` + launcher 可用 → 停止可点并写 false | 单测：按钮 enabled，确认后请求体 `{desired_running:false}` | ✅ |
| 视觉与布局（主观项） | 否 | 控制条顺序「研发看护 开关 · 停止 · 重启管线 · 状态文案」，无换行/遮挡 | 隔离栈截图（1280×800）：按钮在开关与重启之间、图标+「停止」文案清晰、控制条单行 | ✅ |

证据截图：

- `tests/validation/2026-10-27-add-factor-research-stop-button-control-bar.png`（控制条特写）
- `tests/validation/2026-10-27-add-factor-research-stop-button-page.png`（整页）

## 门禁记录

- vitest 全量：`npx vitest run` → **106 files / 964 tests passed**（含新增 6 例停止按钮用例）
- `npx tsc -b`：0 错
- E2E 专属套件：`npx playwright test --config playwright.factor-stop.config.ts` → **1 passed**（22.1s）
- E2E 回归（同隔离栈跑整份 monitor 套件，含既有 22 例 + 新增 6b）：**23 passed**（2.2m）
- 后端端点回归（零后端改动，跑到即证未破坏）：`uv run pytest tests/test_api_factor_research.py -q` → **83 passed**
- 前端无 lint 工具链（仓库既有状态，CI 前端门禁为 vitest + build/tsc）

## 隔离与安全记录

- 用例会真实 `PUT` 控制文件，因此必须走独立端口对（8009/5182）+ 独立 fixture 路径；
  spec 内保留前置守卫（后端读到的快照 `collected_at` 必须等于本 spec 写入值），复用生产栈会被拒绝执行。
- 视觉检查用另一组临时隔离栈（8010/5183），未触碰生产控制文件；生产看护在整个验证过程中保持运行（实态 running，未发任何控制请求）。

## 异常记录

- 无阻塞异常。备忘：Playwright 的 `--grep` 不会覆盖 config 里的 `grep`，跑整份套件回归时需临时移除 config grep 再恢复（本次即如此操作）。

## 结论

- [x] 全部通过，可 archive
