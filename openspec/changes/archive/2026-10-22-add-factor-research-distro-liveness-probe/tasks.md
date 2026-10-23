# Tasks: add-factor-research-distro-liveness-probe

- [x] 启动器每节拍先探活 `wsl -d <distro> -e /bin/true`（≤20s 超时），失败不阻断既有 ensure 流程
- [x] 探活结果原子写入 `data/factor_research/distro_probe.json`（含 `ok`/`exit_code`/`detail`/
      `consecutive_failures`/`terminated_at`/`terminate_count`）；写失败不影响代理存活保证
- [x] `consecutive_failures` 跨节拍正确累加与归零（成功即归零）
- [x] 自愈门控为三条件合取（连续失败 ≥15 ∧ 快照停摆 ≥900s ∧ 距上次自愈 ≥1800s），缺一不动作
      （抽为纯函数 `Program.ShouldHeal`，真值表程序 7/7 全过）
- [x] 探活/计数/文件写入实测（沙箱四例：计数 1→15→16→41、detail 捕获 WSL 错误、文件始终合法 JSON）；
      自愈的**执行**分支未对真实失败 distro 演练（沙箱内子进程 wsl.exe 不可用）——
      门控逻辑已由真值表确定性覆盖，如实记账于验证报告「异常记录 1」
- [x] 探活只读：不碰控制文件、不删研发产物（workspace/候选/会话日志）
- [x] 端点增 `distro` 派生块（透传 + `age_seconds` / `fresh`；缺失/损坏 → `available=false` + 原因码）
- [x] `distro.ok=false` 不改变 `actual_state` / `control.honor`，端点零处置动作
- [x] 监控页探活提示（`data-testid="factor-research-distro-probe-banner"`）含连续失败次数与探活时刻
- [x] 快照陈旧 + 探活失败时，陈旧横幅不再声称「采集器可能已停止」
- [x] 探活不可用/不新鲜/快照新鲜时均不展示探活提示（不冒充结论）
- [x] E2E spec 已覆盖探活披露场景（交互类变更；#18/#19）
- [x] 计划任务已重注册，探活文件在 ≤2 分钟内出现且 `ok=true`（生产实测）
- [x] 人工验证报告已落 `tests/validation/`
- [x] `openspec validate add-factor-research-distro-liveness-probe --strict` 通过
