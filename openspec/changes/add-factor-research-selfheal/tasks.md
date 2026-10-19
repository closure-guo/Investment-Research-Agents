# Tasks: add-factor-research-selfheal

- [ ] 研究侧自愈启动器可用：Windows 计划任务（登录时 + 每 2 分钟）幂等确保 distro/代理存活并写心跳
  （`launcher.json`），不触碰控制文件与看护
- [ ] `--launcher-tick` 幂等：代理已在跑不重复拉起（单例锁）；心跳原子写；心跳写失败不影响代理存活
- [ ] 快照新增 `agent` 块（`last_action` / `last_action_at` / `last_error`）；独立采集器写 `agent: null`
- [ ] `GET /api/factor-research/status` 增加 `launcher` 块（available/reason/age_seconds/fresh/agent_ok）
  与 `control.honor` / `honor_eta_seconds`
- [ ] `PUT /api/factor-research/control` 受理门：快照新鲜 → live；否则心跳新鲜且 agent_ok → launcher；
  双无证据 → 409 且不落文件
- [ ] 前端开关：`honor=launcher` 时可操作并明示 ≤3 分钟自动拉起；陈旧时位置取持久化意图；
  pending 期间展示 `start-failed` / `stop-failed` 原因；连续短命会话告警
- [ ] 单元测试：代理（launcher-tick / 单例 / 不动意图 / 心跳）、采集器（agent 块）、后端（受理门
  四象限 / launcher 派生 / honor）、前端（四种新状态）
- [ ] E2E `factor-research-monitor.spec.ts` 增补：stale+launcher 可点与文案、双离线禁用、
  start-failed 文案、fresh 回归；fixture 与 `FACTOR_RESEARCH_LAUNCHER_PATH` 环境注入齐备；
  E2E 门禁全绿
- [ ] 人工验证（真栈）：kill 代理自愈 / `wsl --terminate rdagent` 自愈 / 研究侧全死时点开自动拉起 /
  开着重启继续 / 关着重启保持停止；报告落 `tests/validation/`
- [ ] 运维文档：`tests/scripts/spike/README.md` 更新（计划任务注册/卸载、心跳排障、回滚步骤）
- [ ] sync + archive（tasks 全勾 + 验证通过 + 人工验证报告齐备）
