# Proposal: add-factor-research-settings

## Why

1. 因子研发监控页的开关在快照失联（机器/虚拟机重启后最常见）时回退到持久化意图
   `control.desired_running`，界面显示「已开启」但研发服务可能根本没起来——操作者无法从
   界面判断真实状态。重启后第一眼看到的「已开启」是不可信的。
2. 研发管线没有重启入口：看护/代理卡死或会话卡在异常状态时，只能去 distro 手动执行
   `stop-factor-research.sh` / `start-factor-research.sh`。
3. 研发节奏（采集间隔、会话间隔、短命会话退避与阈值）硬编码在代理与看护脚本里，缺少
   可持久化、可校验、可回读的配置面；设置中心也没有因子研发分区。

## What Changes

- `GET /api/factor-research/status` 新增 `actual_state`（`running` / `stopped` / `unverified`）、
  `config` 块与 control 的重启请求字段。实态不可证时 MUST NOT 用持久化意图冒充运行中。
- 监控页开关位置改为「验证过的实际状态」；不可证时开关为关并显示「状态未知」，
  同时披露持久化意图与启动器恢复路径。
- 新增 `POST /api/factor-research/restart`：写控制文件重启请求（`desired_running=true` +
  `restart_request_id` 等标记），研发代理按 request_id 去重执行一次（停止当前会话 → 重新拉起），
  并在快照 `agent.restart` 中回写处理结果。
- 新增 `PUT /api/factor-research/config` 与 `data/factor_research/pipeline.json`：
  四个节奏旋钮（采集间隔 / 会话间隔 / 短命退避 / 短命阈值）。采集间隔下一周期生效；
  其余三项在下次启动看护时生效。代理每个周期 fail-safe 读取并在快照 `agent.config` 回显。
- 设置中心新增「因子研发配置」分区：服务实态、启停/重启、节奏配置与恢复说明；
  监控页控制条增加「重启管线」按钮（二次确认）。

## Capabilities

- **Modified Capabilities**: `factor-research-monitor`（实态派生、重启控制、节奏配置通道）、
  `frontend`（监控页开关语义 + 配置页）
- **New Capabilities**: 无

## Impact

- 后端：`src/finance_agent/api.py`（状态端点扩展、restart/config 端点）
- 研究侧代理：`tests/scripts/spike/factor_research_agent.py`（重启请求去重执行、节奏配置读取、
  快照上报）；`collect_factor_status.py` 的 `agent_meta` 透传不变
- 前端：`FactorResearchPage.tsx`、`SettingsCenterPage.tsx`、新增 `FactorResearchPane.tsx`、
  `types.ts`、共享取数/派生工具
- E2E：`factor-research-monitor.spec.ts` 新增实态/重启/设置页用例；playwright env 增加
  `FACTOR_RESEARCH_CONFIG_PATH`
- 部署：distro 侧 `factor_research_agent.py` 需重新推送才能执行重启与新配置读取；
  旧代理下页面会显示「重启请求未被执行（可能为旧版）」，MUST NOT 假成功
