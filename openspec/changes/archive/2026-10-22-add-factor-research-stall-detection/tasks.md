# Tasks: add-factor-research-stall-detection

- [x] 快照含 `current_session.log_age_s`（日志 mtime 距采集时刻秒数；无会话/stat 失败 → null）
- [x] 采集器不自行判定僵死（只报原始读数）
- [x] 端点派生 `progress` 块（`log_age_seconds` / `stalled`），阈值单源
      `FACTOR_RESEARCH_STALL_AFTER_S = 900`，全仓无第二份阈值
- [x] `stalled` 四条件判定正确：快照可用且非陈旧 + 看护运行中 + 会话存活 + 超阈值
- [x] 快照不可用/陈旧、看护未运行、会话非存活、旧采集器未上报 → `stalled=false`（不误报）
- [x] 端点对 `stalled=true` 零副作用（不改控制文件、不触碰研发进程）
- [x] 监控页僵死横幅（`data-testid="factor-research-stall-banner"`）含无进展时长
- [x] 僵死时步骤进度标注为陈旧读数，MUST NOT 渲染「预计剩余」冒充当前进度
- [x] 未僵死/未上报时不展示横幅，且未上报时不声称「进度正常」
- [x] E2E spec 已覆盖僵死场景（交互类变更）
- [x] 采集器已部署到 distro 并验证真实快照 `log_age_s` 与日志 mtime 一致
- [x] 人工验证报告已落 `tests/validation/`
- [x] `openspec validate add-factor-research-stall-detection --strict` 通过
