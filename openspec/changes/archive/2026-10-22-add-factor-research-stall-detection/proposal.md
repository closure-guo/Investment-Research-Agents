## Why

2026-10-22 因子研发会话两次因 pandarallel 并行 IC 计算撑爆资源而死锁（incident 049）：第二次从
07:40 到 11:47 停摆 **4h07m**，而监控页全程显示「运行中 / 存活 / 已运行 603s · 预计剩余 905s」——
`current_session.alive` 只探「进程在不在」（死锁进程仍在），`step_elapsed_s` / `step_eta_s` 解析自
日志里**最后一行** tqdm 进度条（日志冻结后该读数永久定格）。于是**僵死会话与健康会话在监控面上
不可区分**，事故只能靠人肉发现，且看护只在会话进程退出后才重拉 ⇒ 死锁可无限期停摆。

## What Changes

- **快照增进度新鲜度原始读数**：`current_session.log_age_s`（当前会话日志文件 mtime 距采集时刻的
  秒数；无当前会话或无法 stat → `null`）。采集器只报原始读数，不判定。
- **状态端点增派生 `progress` 块**：`log_age_seconds`（int 或 null）与 `stalled`（布尔）。
  阈值单源 `FACTOR_RESEARCH_STALL_AFTER_S = 900` 秒（依据：历史 14 个会话日志实测最大静默
  **602s**（4 次，疑为 600s 超时），中位 88s ⇒ 取观测上界的 1.5 倍；既能覆盖正常长操作，
  又能把本次 4h07m 的停摆在 15 分钟内暴露）。
- **监控页如实披露僵死**：`stalled=true` → 醒目提示「当前会话无进展 N 分钟」；同时
  MUST NOT 把陈旧的步骤进度/ETA 当作当前进度展示（标注读数时刻）。
- 零命令面不变：不新增任何控制入口、不接受任何新参数。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `factor-research-monitor`: 状态快照采集契约增 `current_session.log_age_s`；只读查询端点增派生
  `progress` 块（`log_age_seconds` / `stalled`）与阈值单源 `FACTOR_RESEARCH_STALL_AFTER_S`。
- `frontend`: 因子研发监控页增僵死提示与陈旧进度读数的如实披露。

## Impact

- distro 侧采集器 `tests/scripts/spike/collect_factor_status.py`（需按 spike 手册部署到 rdagent distro）
- 后端 `src/finance_agent/api.py`（派生块 + 阈值常量）
- 前端 `frontend/src/pages/factors/FactorResearchPage.tsx`、`frontend/src/types.ts`、
  `frontend/src/lib/factorResearch.ts`
- 测试：`tests/scripts/spike/test_collect_factor_status.py`、`tests/test_api_factor_research.py`、
  `frontend/src/test/factorResearchPage.test.tsx`、
  `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`
- 不涉及：控制通道、重启通道、自愈启动器（exec 存活探针与 `wsl --terminate` 自愈属**另一个**
  delta——它改动启动器权限边界，需单独讨论）
