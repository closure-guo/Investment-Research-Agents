# 人工验证报告: add-factor-research-settings

**日期**: 2026-10-21
**验证人**: Agent（自动化门禁 + 生产栈/真实 distro 状态核验；视觉截图留档）
**关联 delta**: `openspec/changes/add-factor-research-settings/`
**E2E 门禁**: `tests/e2e/playwright/tests/factor-research-monitor.spec.ts` 15/15 通过
（生产栈占用 8000/5173 → 用 `playwright.isolated.config.ts` 在 8001/5174 隔离端口 +
隔离 DB/文件桥运行；spec 断言未改动。用例 6 的「后端必须读隔离 fixture」守卫通过。）

## 环境

- 宿主：Windows + 生产 Docker 栈（backend/frontend 已 `docker compose restart` 加载新代码，
  重启前 `GET /api/sessions` 无 running 会话）。
- 研究侧：`rdagent` WSL2 distro；新代理 pid 5483（`/root/factor_research_agent.py` 已更新，
  旧文件备份 `factor_research_agent.py.bak-20261021`）；**正在运行的看护会话未被中断**
  （agent daemon 与 supervisor 生命周期独立）。
- 生产 API 实测：`actual_state=running`、`config.source=defaults`、`agent.config.values`
  四字段齐全、`agent.restart=null`、`control.restart_pending=false`。

## 验证结果

| # | Scenario | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 重启后实态语义 | 快照陈旧/缺失时开关不再显示「已开启」，显示「状态未知」并披露持久化意图 | 前端单测「离线可开」断言 `aria-checked=false` + 「状态未知/已记录保持运行」；E2E 用例 13 通过 | ✅ |
| 2 | 验证过的运行中 | 快照新鲜且 `supervisor.running=true` → 开关开、徽章「运行中」 | 生产页面实测开关 `aria-checked=true`、控制文案「已开启」；截图见下 | ✅ |
| 3 | 重启管线按钮 | 二次确认后 `POST /api/factor-research/restart`，控制文件含 restart 请求；`restart_pending=true` 期间显示「重启中」并禁用 | E2E 用例 14（真实后端 + 隔离控制文件）通过；生产未按（避免终止正在跑的研发会话） | ✅（隔离环境） |
| 4 | 代理重启去重 | 同一 request_id 只执行一次；停止失败不启动第二个看护；结果持久化 | 代理单测 4 例（执行一次/停止态只启动/desired=false 忽略/停止失败不启动）通过 | ✅ |
| 5 | 旧代理不假成功 | 不写 `agent.restart` 的旧代理 → `restart_pending` 保持真，60 秒后提示「未被执行」 | 后端单测 + 前端单测（超时分支）通过 | ✅ |
| 6 | 节奏配置写入/回读 | 四字段严格整数、越界/未知字段 422；原子写；`config.values` 回读 | 后端 10 例配置测试通过；E2E 用例 15 通过并真实写入隔离文件 | ✅ |
| 7 | 配置生效与回显 | 代理上报 `agent.config`；旧代理缺失时页面标注「研究侧未上报」 | 生产快照实测 `agent.config.source=defaults`（配置文件缺失）四字段齐全；设置页显示「研究侧实际读取」 | ✅ |
| 8 | 设置中心新分区 | 左侧导航「因子研发配置」直达；实态/控制/节奏配置/恢复说明四区块 | 生产页面点击导航后渲染，`settings-state=已开启`；截图见下 | ✅ |
| 9 | 控制通道离线 | 动作与保存禁用，展示宿主机恢复命令且无可点击执行入口 | 前端单测通过；E2E 用例 7/10（禁用 + 理由）通过 | ✅ |

## 视觉证据

| 页面 | 文件 |
|---|---|
| 设置中心 · 因子研发配置（生产，实态运行中） | `tests/validation/2026-10-21-factor-research-settings.png` |
| 因子研发监控（生产，开关开 + 重启按钮） | `tests/validation/2026-10-21-factor-research-monitor.png` |

## 自动化验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 + 代理单测 | `uv run pytest tests/test_api_factor_research.py tests/scripts/spike/test_factor_research_agent.py -q` | 102 passed |
| 前端全量单测 | `cd frontend && npx vitest run` | 898 passed / 101 files |
| 前端类型检查 | `cd frontend && npx tsc -b` | 0 错误 |
| Lint | `uv run ruff check <modified py>` | All checks passed |
| 后端类型检查 | `uv run mypy src/finance_agent/api.py` | 5 个存量错误（484/1682/1871/2618 行），新增代码零报错 |
| E2E 门禁 | `npx playwright test tests/factor-research-monitor.spec.ts --config playwright.isolated.config.ts` | 15/15 passed |
| OpenSpec | `openspec validate add-factor-research-settings --strict` | valid |

## 异常记录

1. **MSYS/WSL 变量展开坑（已处理）**：经 `wsl ... bash -c 'W=/root/...; ... "$W"'` 启动新代理时，
   外层 MSYS 把 `$W` 等变量展开为空，代理以 `--workdir . --control . --out .` 启动（pid 5206），
   在仓库根留下 `factor_research_agent.pid`。已 kill 该实例、删除杂散 pidfile，改用
   **stdin 投递脚本 + 字面路径**的方式重新部署（pid 5483，`/proc/<pid>/cmdline` 逐项核对正确）。
   后续 distro 部署一律走脚本文件，不在 `bash -c` 内用 shell 变量拼路径。
2. **E2E 串行共享控制文件**：用例 14 留下的 restart 请求会让用例 15 首屏显示「重启中」；用例 15
   开头显式 `removeControl()` 后通过。属测试夹具清洁问题，非产品缺陷。
3. **生产重启按钮未实按**：当前看护有正在运行的 fin_factor 会话（终止即作废当前实验）。按
   「破坏性动作需操作者确认」原则，生产验证只覆盖到按钮可见/可用与隔离环境全链路；真机重启
   留给操作者在合适时机点击确认。

## 结论

- [x] 自动化门禁全部通过（后端/代理/前端/E2E/类型/Lint/OpenSpec）
- [x] 生产后端与研究侧新版代理已部署，状态端点与页面实态一致
- [ ] 生产环境「重启管线」实按验证（待操作者在研发会话可中断时执行）
