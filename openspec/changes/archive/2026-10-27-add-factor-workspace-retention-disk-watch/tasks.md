# Tasks: add-factor-workspace-retention-disk-watch

- [x] 看护主循环实现会话结束后保留窗清理（`FACTOR_WORKSPACE_RETENTION_H`，默认 48h），含失败跳过与日志
- [x] 采集器新增 `disk` 读数（guest ext4 空闲 / 宿主 D: 空闲 / workspace 总占用），失败字段置空不编造
- [x] 只读端点派生 `disk` 块（含 `low_watermark` / `low_watermark_reason` / `paused_reason`）
- [x] 看护实现低水位停发保护（阈值 `FACTOR_DISK_MIN_FREE_GB_GUEST=30` / `FACTOR_DISK_MIN_FREE_GB_HOST=20`），披露 `paused_reason`
- [x] distro 探活升级：exec + 临时文件读写双探针，失败原因区分 `fs_rw_failed`
- [x] `/factor-research` 页新增水位展示与低水位横幅（低水位时不再把停发误报为故障）
- [x] 单元测试：清理窗口判定（边界含等于）、低水位真值表、探活双探针组合、读数失败降级（合并跑 190 passed, 1 skipped）
- [x] E2E spec 覆盖 /factor-research 水位展示与低水位横幅场景（专属端口对 playwright.monitor-disk.config.ts，3/3 绿）
- [x] 人工验证报告落 `tests/validation/`（含读数截图/断言证据）：tests/validation/2026-10-27-add-factor-workspace-retention-disk-watch-validation.md
- [x] incident 051 待办项勾销回链（docs/incidents/051 待办 #1）
