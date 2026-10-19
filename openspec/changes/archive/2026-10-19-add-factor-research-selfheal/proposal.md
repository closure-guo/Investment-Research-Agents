# Proposal: 研发看护自愈常驻与离线可开（add-factor-research-selfheal）

## Why

`/factor-research` 的研发看护开关（add-factor-research-control，2026-10-18）只在「快照新鲜」时
受理控制请求：研究侧代理随 WSL 虚拟机停机后，快照陈旧 → 开关禁用；而能拉起代理的只有登录 distro
手动跑脚本——控制面在这个最需要它的场景里恰好失能。2026-10-18 23:59 虚拟机停机后 19 小时无人
恢复（`status.json` 定格在 23:59:28，`agent.log` 尾巴为 `tick failed: OSError(5, 'Input/output
error')`），用户面对灰掉的开关没有任何自助手段。这正是上一版 design 中「distro 开机自启」Non-Goal
留下的实账。

## What Changes

- **研究侧自愈启动器**：宿主级 Windows 计划任务（用户登录时 + 每 2 分钟）幂等调用 distro 入口
  `ensure-factor-research.sh`（→ 代理 `--launcher-tick`）：确保 distro 在跑、研发代理守护在跑，
  并原子写 `launcher.json` 心跳。启动器 MUST NOT 触碰控制文件——意图的唯一写入面不变。
- **代理快照增强**：快照新增 `agent` 块（`last_action` / `last_action_at` / `last_error`），把
  「点了开但起不来」从无限「切换中」变成可读失败（如 key 过期、启动脚本缺失）。
- **控制受理门改造**：`PUT /api/factor-research/control` 在快照不新鲜、但启动器心跳新鲜且代理就绪
  时仍受理（`honor="launcher"`，`honor_eta_seconds=180`）；仅「快照与启动器双双无证据」时维持
  409。`GET /status` 增加 `launcher` 块与 `control.honor`，判定单源在后端。
- **前端离线可开**：`honor="launcher"` 时开关可操作并明示「≤3 分钟内自动拉起」；快照陈旧时开关
  位置改取已持久化的 `control.desired_running`；pending 期间暴露 `start-failed` / `stop-failed`
  原因；新增「连续短命会话」告警（复用既有 `sessions[].duration_s`，零新增采集面）。
- **重启语义**：控制文件已持久化期望态——开着就自动继续、关着就一直停。无新逻辑，是既有期望态
  调和的自然结果。

## Capabilities

### New Capabilities

（无——全部落在既有 capability）

### Modified Capabilities

- `factor-research-monitor`：快照契约（新增 `agent` 块）、只读查询端点（新增 `launcher` 块与
  `control.honor` / `honor_eta_seconds`）、控制请求端点（受理门：快照 OR 启动器；409 仅双离线）、
  新增「研究侧自愈启动」Requirement（心跳格式、≤2 分钟节拍、≤300 秒新鲜阈值、MUST NOT 触碰意图）。
- `frontend`：因子研发监控页（离线可开 + ETA 披露、陈旧时位置取意图、失败可读、短命会话告警）。

## Impact

- 代码：`tests/scripts/spike/factor_research_agent.py`（`--launcher-tick` + agent 元数据）、
  `tests/scripts/spike/collect_factor_status.py`（可选 agent 元数据入快照，独立调用写 `agent: null`）、
  `tests/scripts/spike/ensure-factor-research.sh`（新）、`src/finance_agent/api.py`、
  `frontend/src/types.ts`、`frontend/src/pages/factors/FactorResearchPage.tsx`、
  `scripts/register-factor-research-task.ps1`（新：幂等注册/更新/卸载计划任务）。
- 测试：代理 pytest（`--launcher-tick` 幂等/不动意图/心跳原子写）、后端 pytest（受理门四象限 /
  launcher 派生 / honor）、前端 vitest（离线可开 / 位置取意图 / 失败文案 / 短命告警）、
  E2E `factor-research-monitor.spec.ts` 增补（stale+launcher 可点、双离线禁用、start-failed 文案、
  fresh 回归）。交互类变更 → E2E 门禁 + 人工验证报告落 `tests/validation/`。
- 运维：本机注册计划任务（登录触发 + 2 分钟间隔）；distro 侧安装 ensure 脚本；README 部署与排障
  步骤更新。回滚 = 卸载计划任务（行为退回「离线仅禁用」，控制文件与快照零迁移）。
- 明确不做：研发容器化、收割/日批纳管、鉴权/审计、任意命令面、卡死代理的自动重启升级
  （见 design.md Non-Goals）。
