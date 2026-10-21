# Tasks: add-factor-research-settings

- [x] 状态端点返回 `actual_state`（running / stopped / unverified）、`config` 块与 control 重启字段（含单测）
- [x] `POST /api/factor-research/restart` 与 `PUT /api/factor-research/config` 端点落地（含 409 / 422 / 原子写单测）
- [x] 研发代理按 request_id 去重执行重启（持久化已处理标识、停止失败不启动）、每周期读取节奏配置并在快照回显 `agent.config` / `agent.restart`（含单测）
- [x] 监控页开关改为实际态语义（unverified 不再显示已开启），新增「重启管线」按钮与失败/超时披露（含单测）
- [x] 设置中心新增「因子研发配置」分区：实态、启停/重启、四个节奏字段保存、恢复说明（含单测）
- [x] E2E：开关实态语义、重启控制文件真写入、设置页配置保存（含 `FACTOR_RESEARCH_CONFIG_PATH` 隔离）
- [x] E2E 门禁通过（15/15，隔离端口 8001/5174；证据路径见验证报告）
- [x] 生产栈重载 + 研究侧新版代理部署（旧文件备份；正在运行的看护会话未中断）
- [x] 人工验证报告已落 `tests/validation/2026-10-21-add-factor-research-settings-validation.md`
