# Design: add-factor-research-settings

## Approach

### 1. 实态派生放在后端单源

`actual_state` 由后端从快照派生，而不是让前端各自组合 `available/stale/supervisor.running`：

- 快照 available 且非陈旧 → `running` / `stopped`（按 `supervisor.running`）；
- 快照缺失、损坏或陈旧 → `unverified`。

持久化意图（`control.desired_running`）只作为「重启后按同一意图恢复」的配置事实展示，
不再决定开关位置。这样「重启后先检查服务是否真的起来」有了单一契约：页面每轮轮询拿到的
`actual_state` 就是结论。

### 2. 重启走控制文件，不走两次 PUT

`POST /api/factor-research/restart` 在控制文件里写 `desired_running=true` 与
`restart_request_id/restart_requested_at_epoch`。代理在调和周期里：

1. 若 `desired_running` 非 true 或控制文件不可解析 → 不动作（fail-safe）；
2. 若 `restart_request_id` 与已处理 id 不同 → 先停止（失败则中止，不启动第二个看护），
   再启动；
3. 成功/失败结果持久化到工作区 `.agent_restart_state.json`，并写进快照
   `agent.restart`。

用 request_id 去重而不是时间戳：两次快速点击不会被 1 秒精度吞掉；代理重启后也不会重复执行。
`PUT /control` 写不带重启字段的文件，语义上清除未执行的重启请求（停止/启动是新的明确意图）。

不采用「前端先 PUT false 再 PUT true」：中间态会让看护经历一次真实停止，失败时留下半开状态，
且没有幂等去重。

### 3. 重启结果以快照 agent.restart 为证

`restart_pending` 只在「有请求 + 快照新鲜 + 没有同 request_id 的终态结果」时为真。
旧版代理不识别重启字段，因此不会写 `agent.restart`，页面会持续显示「重启未被执行」并在
超时后提示部署新版代理——这比「快照推进即当成功」诚实。

### 4. 节奏配置走宿主数据桥

配置文件 `data/factor_research/pipeline.json` 与既有 `control.json` / `status.json` /
`launcher.json` 同一条宿主桥，后端调用期读 `FACTOR_RESEARCH_CONFIG_PATH`。代理每周期读取：

- `snapshot_interval_s`：下一轮 sleep 用新值（5–120 秒）；
- `session_gap_s` / `short_session_backoff_s` / `short_session_threshold_s`：渲染看护脚本时
  使用，即下一次启动看护生效（页面明示）。

四个字段都做严格范围校验与 fail-safe 归一化：缺失/损坏/越界回默认值，代理不崩、不猜。
快照 `agent.config` 回显代理实际读到的值，页面可核对「文件值 vs 研究侧值」。

不采用「前端直接编辑 distro `.env` / RD-Agent 配置」：跨宿主/发行版边界需要命令通道，
且会把 LLM 凭据暴露到配置面；本变更坚持零命令面。

## Alternatives Considered

- **只改前端派生**：契约分散在页面里，后续再加入口会重复实现；后端单源可测试性更好。
  选后端派生。
- **两次 PUT 实现重启**：中途无幂等、失败留半开，且无法区分「重启」与普通启停。
  选控制文件 restart 标记 + request_id 去重。
- **重启时直接杀进程/执行脚本**：后端在容器内，无法可靠调用 distro；且会引入命令注入面。
  不做，保持文件桥。
- **暴露 LLM 模型配置**：需要写 distro `.env` 与密钥，收益低风险高。不做。

## Risks

- **旧代理兼容**：不识别 restart/config → 页面如实显示「未执行/未上报」，不会假成功；
  部署新版代理后自动启用。部署步骤写入 tasks/validation 报告。
- **配置生效时机**：会话节奏在下一次启动看护时生效，可能让用户误以为立即生效；
  页面文案与 spec 明示。采集间隔下一周期生效。
- **控制文件双写**：宿主（后端）与 distro（脚本）都会写控制文件；沿用既有唯一 tmp 名 +
  原子 replace，不引入固定 tmp 名。
- **重启失败不再自动重放**：失败结果（`restart-failed`）对本次请求是终态，避免每 15 秒
  重复执行破坏性停止；若失败发生在启动段，后续周期仍会按 `desired_running=true` 并非常规
  start 路径自愈。页面展示失败原因，用户可再次点击重启发起新请求。
  停止失败时绝不启动第二个看护，避免双进程烧 key。
