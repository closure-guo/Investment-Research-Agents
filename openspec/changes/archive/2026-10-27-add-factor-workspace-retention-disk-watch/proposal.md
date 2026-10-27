# Proposal: add-factor-workspace-retention-disk-watch

## Why

RD-Agent 每个会话在 distro 内物化全量因子面板快照（`combined_factors_df.parquet` 0.4–1.9GB + 回测 h5），
且全链路无保留策略：10-22 至 10-26 四天积累 ~820GB，把宿主 D: 打到剩 18.7GB，VHDX 无法扩张导致
distro 部分块不可读（incident 051），管线停摆 2.5h+。现有监控只覆盖内存（047）与 tmpfs（049），
磁盘水位是同族三块拼图中缺的一块；同时 distro 探活 `/bin/true` 在 fs 大面积不可读时假阴性（页缓存命中）。

## What Changes

- 看护（supervisor/agent daemon）在每次会话结束后按保留窗清理 `RD-Agent_workspace` 旧任务目录（默认 48h），失败降级记录、不致崩
- 采集器新增磁盘水位读数：guest ext4 空闲、宿主 D: 空闲、workspace 总占用；只读端点派生 `disk` 块；`/factor-research` 页新增水位展示与告警横幅
- 低水位停发保护：guest 或宿主空闲低于阈值（默认 30GB / 20GB）时，看护暂停拉起新会话并在状态中如实披露
- distro 探活从「exec /bin/true」升级为「exec + 读写真实临时文件」，覆盖页缓存假阴性形态

## Capabilities

- **Modified Capabilities**: `factor-research-monitor`（新增保留窗清理 / 磁盘水位 / 低水位停发 / 探活升级四组需求）

## Impact

- `tests/scripts/spike/factor_research_agent.py`（看护主循环：清理 + 停发门控）
- `tests/scripts/spike/collect_factor_status.py`（采集器：水位读数）
- `src/finance_agent/api/`（/factor-research 端点派生 `disk` 块 + 探活 `probe` 块）
- `frontend/src/pages/FactorResearch*`（水位展示 + 低水位横幅）
- E2E：/factor-research 页新增水位场景（交互类变更，走完整管线含 E2E 门禁）
