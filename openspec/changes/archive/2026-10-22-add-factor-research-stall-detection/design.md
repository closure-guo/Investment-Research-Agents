## Context

监控面现有分层：distro 侧采集器周期写 JSON 快照（只读观测，MUST NOT 干预研发进程）→ 后端
`GET /api/factor-research/status` 只读透传原始字段并派生 `available` / `age_seconds` / `stale`
→ 前端轮询渲染。既有派生只覆盖「采集链路是否还活着」（快照新鲜度）。

incident 049 暴露的缺口在另一维度：**采集链路完全正常，但会话进程存活而日志停更**（pandarallel
死锁）。此时 `current_session.alive` 为真（只探进程存在），`step_elapsed_s` / `step_eta_s` 取自
日志末行 tqdm（日志冻结即定格），页面因此显示「运行中 · 已运行 603s · 预计剩余 905s」长达
4h07m。看护只在会话进程退出后重拉 ⇒ 死锁可无限期停摆。

约束：采集路径只读（MUST NOT 改进程状态）；零命令面；阈值单源（项目既有纪律）；快照原始字段
逐字透传（派生字段不得混入原始块）。

## Goals / Non-Goals

**Goals:**

- 让「采集正常但会话无进展」成为可观测、可判定、可如实呈现的状态，且阈值有实测依据。
- 保持零新增控制面：本能力只披露，不自动处置。

**Non-Goals:**

- 自动处置（不自动重启、不自动杀会话、不写控制文件）。
- 覆盖「采集器/代理本身停摆」——已由既有 `stale` 覆盖。
- exec 存活探针与允许 `wsl --terminate rdagent` 的自愈（动启动器权限边界，另立 delta）。

## Decisions

**D1：原始读数在采集器，判定在后端。** 采集器只报 `current_session.log_age_s`（日志 mtime 距
采集时刻秒数）；后端派生 `progress.stalled`。
*备选*：采集器直接判 `stalled` → 否。阈值会落到 distro 侧脚本，与后端常量构成第二份阈值；且每次
调阈值都要重新部署 distro。*备选*：前端按 `log_age_seconds` 自行判定 → 否。同一阈值出现在前端，
违反单源。此分层与既有 `collected_at_epoch` → 后端派生 `stale` 完全同构。

**D2：派生块放顶层 `progress`，不塞进 `current_session`。** spec 要求快照原始字段逐字透传；
在 `current_session` 内加派生字段会同时破坏「透传」语义与「原始 / 派生」边界。

**D3：阈值 900 秒。** 实测依据：历史 14 个会话日志的最大行间静默为
`48/49/65/85/86/87/87/88/88/247/601/601/602/602` 秒（中位 88，上界 602，上界出现 4 次，形态
指向上游 600 秒超时）。取上界的 1.5 倍：既不会把 602 秒级正常长操作误报，又能把本次 4h07m 的
停摆在 15 分钟内暴露。*备选* 600 秒 → 否（正好等于观测上界，会误报 4 次历史正常会话）；
*备选* 1800 秒 → 否（暴露太慢，本次事故要半小时才发现）。

**D4：`stalled` 需同时满足三个条件。** 快照 `available=true` 且非陈旧、`supervisor.running=true`、
`current_session.alive=true`，且 `log_age_seconds > 900`。缺一为 `false`：看护停止时日志本就不
更新（那是「没在跑」不是「僵死」）；会话非存活时属正常会话间隔。

**D5：前端只披露、不改语义。** `stalled=true` 时展示横幅并把步骤进度读数标注为「来自 N 分钟前的
日志」，MUST NOT 继续渲染「预计剩余 X」——日志末行读数是历史值，当当前进度展示即失实。

## Risks / Trade-offs

- [误报：某步骤合法静默超过 900 秒（如大回填）] → 阈值取观测上界 1.5 倍；提示文案表述为「无进展」
  而非「故障」；不触发任何自动处置，误报代价仅是一条提示。
- [漏报：日志停更不足 900 秒的短僵死] → 接受：900 秒内的静默与正常长操作在观测上不可区分。
- [旧版采集器未上报 `log_age_s`] → `progress.log_age_seconds=null`、`stalled=false`；前端不提示
  亦不声称「进度正常」（未上报 = 不可判定）。部署新采集器后自然生效，无迁移步骤。
- [采集器 stat 失败或日志被轮转] → `log_age_s=null`（fail-safe，不猜、不填 0）。

## Migration Plan

1. 后端与前端先合：端点缺 `log_age_s` 时降级为不判定；前端遇 `progress` 缺失/`null` 时按不提示处理
   （旧端点无该块即天然兼容）。
2. 采集器按 `tests/scripts/spike/README.md` 部署到 rdagent distro（`tr -d '\r'` 推入 + 重启代理守护），
   随后验证真实快照含 `log_age_s` 且数值与日志 mtime 一致。
3. 回滚：后端/前端回退即恢复旧行为；采集器回退到部署时的 `.bak` 副本（旧快照字段被新端点忽略）。

## Open Questions

- `stalled=true` 时是否要把 `actual_state` 标为 degraded？本 delta 不引入——`actual_state` 的语义是
  「看护在不在跑」，僵死是另一维度；页面用独立提示表达，避免污染既有三态判定与依赖它的开关位置逻辑。
