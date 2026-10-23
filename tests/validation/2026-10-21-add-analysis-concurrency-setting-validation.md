# 人工验证报告: add-analysis-concurrency-setting

**日期**: 2026-10-21
**验证人**: ZCode（自动化验证；主观项待用户抽查）
**关联 delta**: `openspec/changes/add-analysis-concurrency-setting/`
**E2E 门禁**: `tests/e2e/playwright/tests/settings-concurrency.spec.ts`（isolated config，1 passed）；`screening-page.spec.ts` 回归 6 passed

## 验证范围

设置中心「会话管理」分区新增「批量分析最大并发数」：默认 10、范围 1–20；保存后立即作用于
`AnalyzeQueue` 并持久化到 `data/settings/analysis_concurrency.json`（`ANALYSIS_CONCURRENCY_PATH`
可覆盖）。来源优先级：设置文件 > 环境变量 `SCREENING_ANALYZE_CONCURRENCY` > 默认值。

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 默认值为 10 | 是（settings-concurrency.spec） | 首次进入显示 10、来源「默认值」 | 输入框读数 10；GET `source=default` | ✅ |
| 保存后立即生效并持久化 | 是（settings-concurrency.spec） | 改为 7 保存 → 已保存 → 刷新仍 7 | 刷新后输入框仍为 7；PUT 后队列 `max_concurrency=7`（单测） | ✅ |
| 非法值被拒绝 | 单测（前端 Vitest + 后端 pytest） | 0/21/非整数 → 错误，不落盘、不改队列 | 前端显示「取值范围 1–20」；API 返回 400 且文件不存在 | ✅ |
| 提高上限立即补位 | 单测（`test_queue_raise_limit_promotes_queued`） | 1→3 时排队两项立即启动 | starter 调用序列 `s1,s2,s3` | ✅ |
| 降低上限不终止运行中 | 单测（`test_queue_lower_limit_keeps_running`） | 2→1 时两个运行中保持、第三项继续排队 | 状态 `running,running,queued` | ✅ |
| 环境变量回退与覆盖披露 | 单测（`test_env_fallback` / `test_save_persists_and_file_wins_over_env`） | 无文件时取 env；保存后文件优先并披露 env 值 | 3/7 与 `env_value=3` 断言通过 | ✅ |
| 损坏文件如实降级 | 单测（`test_corrupt_file_falls_back` / `test_invalid_saved_value_falls_back`） | 回退且披露 `corrupt`/`invalid`，不 500 | 断言通过 | ✅ |
| 设置项保存反馈 | 是（settings-concurrency.spec） | 保存成功显示「已保存」并回显生效值 | 通过 | ✅ |

## 生产生效验证（部署后）

宿主 `src/` 以 dev override 挂载，但 WSL2 bind mount 的 inotify 不传播——宿主改码不会触发容器内
`--reload`。核对无运行中会话（`status=running` = 0）后重启后端容器加载新代码：

- 重启前 `GET /api/settings/analysis-concurrency` → 404（旧代码）
- 重启后 → `{"value":10,"source":"default","default":10,"min":1,"max":20,"env_value":null,"file_status":"missing"}`
- 生产页面只读抽查（Playwright，未保存）：`/settings → 会话管理` 显示
  「批量分析最大并发数」输入框 **10**、区间 **1–20**、来源「默认值」；
  截图 `tests/validation/2026-10-21-analysis-concurrency-prod.png`
- 前后端与 Langfuse 全部健康：8000 `/api/health` 200、5173 200、3000 已启动

> 重启过程中触发 WSL 内存耗尽（前端容器先因 Vite watcher `ENOMEM` 退出，后端首尝试启动失败）；
> 已按 `docs/incidents/047-wsl-memory-exhaustion-container-enomem-20261021.md` 完成恢复。生产最终状态健康。

## 证据

- 后端定向测试：`uv run pytest tests/test_analysis_concurrency.py tests/test_api_analysis_concurrency.py tests/test_api_screening.py -q` → **56 passed**
- 后端扩展定向（含事件/选股）：276 passed；1 failed = `tests/screening/test_report_trigger.py::test_per_report_cap`，
  失败日志为外部网络/鉴权（`Web search returned no results`、`DeepseekException - Authentication Fails`），
  与本改动无关；单跑同错可复现（环境依赖用例）。
- 前端：`npx vitest run` → **900 passed (101 files)**；`npx tsc -b` 无输出（通过）
- Lint：`uv run ruff check <改动文件>` → All checks passed
- E2E（隔离栈 8001/5174，不触碰生产实例）：
  - `npx playwright test -c playwright.isolated.config.ts tests/settings-concurrency.spec.ts` → **1 passed**
  - `npx playwright test -c playwright.isolated.config.ts tests/screening-page.spec.ts` → **6 passed**
    （默认并发由 2 提升到 10 后，批量分析徽标用例仍通过）

## 未覆盖 / 限制披露

1. **默认配置（8000/5173）全量 E2E 套件未运行**：本机 8000 由 Docker 生产栈占用，为不中断用户运行环境，
   使用 `playwright.isolated.config.ts`（8001/5174，独立测试库与设置文件）跑本次相关 spec。
   生产栈当时 `status=running` 会话数为 0（已核对）。
2. **人工主观项**（设置项文案/位置/视觉）已做生产页面只读截图抽查（见上），未由真人逐项复核；
   建议用户进入 `/settings → 会话管理` 再确认一次。
3. 并发默认值 2 → 10 会提高 LLM 调用峰值；如需回退可在设置项下调，或临时设置环境变量
   `SCREENING_ANALYZE_CONCURRENCY`（无保存文件时生效）。
4. 生产重启期间发生 WSL 内存耗尽（见 incident 047）；恢复后服务健康，未损失运行中分析
   （重启前 running = 0、队列为空）。

## 结论

- [x] 自动化验证全部通过（上述定向范围），可进入 sync + archive
- [ ] 存在失败项，需修复后重新验证（本改动无失败项；唯一失败为外部网络/鉴权环境用例）
