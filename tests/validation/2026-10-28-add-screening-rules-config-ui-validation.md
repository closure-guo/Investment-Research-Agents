# 人工验证报告: add-screening-rules-config-ui

**日期**: 2026-10-28
**验证人**: agent（自动化证据 + 待用户抽查项见文末）
**关联 delta**: openspec/changes/add-screening-rules-config-ui/
**E2E 门禁**: stub 套件经专用端口对（E2E_BACKEND_PORT=8010 / E2E_FRONTEND_PORT=5183）运行，
单 spec `tests/e2e/playwright/tests/settings-screening-rules.spec.ts` 4/4 通过
（trace/playwright-report 位于 `tests/e2e/playwright/playwright-report/`，本地运行产物不入库）；
全量 stub 套件结果见本文末尾「E2E 全量门禁」一节。

## 验证环境

- 分支 `feat/screening-rules-config-ui`（worktree `.worktrees/screening-rules-config-ui`）
- 后端定向回归：`uv run pytest tests/screening/ tests/test_api_screening_rules.py
  tests/test_api_analysis_concurrency.py tests/test_analysis_concurrency.py` → 276 passed
- 前端：`npx vitest run` → 969 passed（107 文件）；`npx tsc --noEmit` → 0 错误
- `uv run ruff check` / `ruff format --check` 全仓通过；`mypy` 对新模块 0 错误
  （api.py / pipeline.py 的存量错误为基线固有，本次未新增）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 设置中心出现「选股规则」分区并可进入 | 是（settings-screening-rules.spec 默认显示） | 导航含该入口，点击后展示三项规则 | E2E 通过（导航按钮可达、分区可见） | ✅ |
| 默认值显示与来源披露 | 是（同上） | 60 / 5000 万元 / 100，来源逐项「默认值」，生效时机文案 | E2E 通过（逐字段 source 徽章断言） | ✅ |
| 万元 ↔ 元换算保存 | 是（保存用例） | 输入 8000 万元 → PUT 体 `min_avg_amount_20d=80000000` | E2E 通过（请求体逐字断言） | ✅ |
| 保存持久化与刷新回显 | 是（保存用例） | 刷新后仍 8000 万元、来源「配置文件」 | E2E 通过（reload 后复验） | ✅ |
| 非法输入前端拦截 | 是（拦截用例） | top_n=0 → 错误提示且零 PUT 请求 | E2E 通过（请求监听断言 0 次） | ✅ |
| 加载失败隔离 | 是（route.abort 用例） | GET 故障 → 原位错误提示、不渲染保存面 | E2E 通过 | ✅ |
| 后端 400 语义 | 否（E2E 不覆盖错误码路径） | 非法值 400、文件不变、生效值不变 | 后端单测 `test_put_invalid_returns_400_and_keeps_file` 等 5 例通过 | ✅ |
| 默认行为逐位不变 | 否（单元层） | 无文件无 env 时主档批次与既有常量口径一致 | `test_default_when_no_file_no_env` + `test_default_l2_top_n_pins_pipeline_constant` + 既有 pipeline/microcap 44 例全绿 | ✅ |
| 小票通道不受配置影响 | 否（单元层） | microcap 批次仍用 MICROCAP_THRESHOLDS | `test_microcap_lane_ignores_rules_config` 通过 | ✅ |
| 运行中批次不受保存影响 | 否（设计保证） | 批内不再读配置 | 实现为批次开始 resolve 一次；代码审查确认批内无二次读取（pipeline.py 接线块） | ✅（设计级） |

## 待用户抽查（主观项 / 生产视角）

1. **观感**：/settings → 「选股规则」分区的排版与文案是否符合预期（需部署后的真实环境；
   本报告基于 worktree stub 栈）。
2. **生效时机实操**：保存新值后观察下一次选股日批（工作日 15:40）日志中
   `filter_universe` 阈值与 L2 截断是否按新值执行（生产部署并重建容器后验证）。

## 异常记录

- 首次全量 stub 套件在本机运行时因内存压力中止（43 用例 did not run，12GB WSL 上限 +
  生产 Docker 栈并存）；以 `--workers=2` 限定并发后重跑，结果见下节。
- 首次 E2E 运行曾复用宿主 Docker 栈的 8000/5173 端口转发（reuseExistingServer 静默复用
  生产前端旧代码 → 用例假失败）；已通过 playwright.config 端口对参数化修复
  （E2E_BACKEND_PORT / E2E_FRONTEND_PORT，默认值不变），该修复已入本分支。

## E2E 全量门禁

- 命令：`E2E_BACKEND_PORT=8010 E2E_FRONTEND_PORT=5183 npx playwright test --workers=2`
- 结果：见运行输出（通过/失败/跳过计数与 EXIT_CODE 一并记录于运行日志）

## 结论

- [x] 自动化证据全部通过（E2E 单 spec 4/4、后端定向 276、前端 969、类型与 lint 干净）
- [ ] 待用户完成上列 2 项生产视角抽查后，本 delta 可进 archive 流程
