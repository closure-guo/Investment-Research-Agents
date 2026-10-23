## ADDED Requirements

### Requirement: 监控页 distro 探活披露

`/factor-research` 页面 SHALL 在探活新鲜且 `ok=false` 时展示独立提示
（`data-testid="factor-research-distro-probe-banner"`）：文案 SHALL 含连续失败次数与最近一次探活
时刻，并 SHALL 明示**采集器并未停止、疑似 WSL 子系统（distro）不可用**，以及恢复动作由人发起
（`wsl --terminate rdagent` 后下一次节拍自动拉起）。该提示 MUST NOT 提供任何可点击的处置入口
（零命令面不变）。

快照陈旧（`stale=true`）且探活新鲜 `ok=false` 时，既有「数据已陈旧」横幅的文案 SHALL 改为指向
distro 不可用——MUST NOT 继续声称「采集器可能已停止」（2026-10-22 incident 049 实测该表述失实：
采集器没停，是 distro 不可用）。探活不可用（`available=false`）、不新鲜（`fresh=false`）或
`ok=true` 时，陈旧横幅 SHALL 保持既有表述，且 MUST NOT 展示探活提示。

#### Scenario: 探活失败且快照陈旧 → 提示可见且不误指采集器

- **GIVEN** 端点返回 `stale=true`，且 `distro.available=true`、`fresh=true`、`ok=false`、
  `consecutive_failures=8`
- **WHEN** 打开监控页
- **THEN** 展示 `factor-research-distro-probe-banner`，文案含「连续 8 次」与「distro」
- **AND** 陈旧横幅 MUST NOT 出现「采集器可能已停止」字样
- **AND** 探活提示内无可点击的处置入口

#### Scenario: 探活正常时保持既有表述

- **GIVEN** 端点返回 `stale=true`，且 `distro.ok=true`
- **WHEN** 打开监控页
- **THEN** 陈旧横幅保持既有「采集器可能已停止」表述
- **AND** MUST NOT 展示 `factor-research-distro-probe-banner`

#### Scenario: 探活不可用或不新鲜不冒充结论

- **GIVEN** 端点返回 `distro.available=false`（或 `fresh=false`）
- **WHEN** 打开监控页
- **THEN** MUST NOT 展示 `factor-research-distro-probe-banner`
- **AND** 陈旧横幅保持既有表述（未探活 ≠ distro 不可用）

#### Scenario: 采集链路正常时不展示探活提示

- **GIVEN** 端点返回 `stale=false`（快照新鲜）且 `distro.ok=false`
- **WHEN** 打开监控页
- **THEN** MUST NOT 展示 `factor-research-distro-probe-banner`
- **AND** 页面照常展示研发进度（探活失败不足以判定 distro 不可用——见 monitor 判据依据）
