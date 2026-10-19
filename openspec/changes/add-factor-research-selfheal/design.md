# Design: add-factor-research-selfheal

## Context

`/factor-research` 的看护开关（add-factor-research-control）以「控制文件桥 + distro 常驻代理」实现：
后端写 `data/factor_research/control.json`，代理轮询并按**期望态调和**启停看护；受理门要求快照
新鲜（`stale=false`）作为「通道存活」证据。上一版 design 的 Non-Goals 明确把「distro 开机自启」
留作后续 ops 项——因为代理与看护都是**手动常驻**。

2026-10-18 23:59 宿主 WSL 虚拟机停机，代理/看护/采集随之消失；`status.json` 定格 19 小时，
`agent.log` 尾巴为 `OSError(5, 'Input/output error')`。页面如实进入「数据已陈旧 + 控制通道暂不可用」，
开关禁用——而世界上没有任何一只「手」能把研究侧拉起来，只能人工登录 distro 跑脚本。这是本次
变更要消除的失效形态。

关键约束（沿用，不重新讨论）：

- 后端容器**无法执行 distro 命令**（无 wsl 能力，攻击面/路由成本高——见上一版 D1）；
- 单 uvicorn worker、控制端点只做文件读写；
- 控制面零命令面（只有布尔期望态）；
- 交互类变更 → 完整管线 + E2E 门禁 + 人工验证。

## Goals / Non-Goals

**Goals:**

- 研究侧具备**自愈常驻**：虚拟机重启、代理被杀、distro 被终止后，≤2 分钟节拍自动恢复。
- 开关在「研究侧离线但启动器可用」时**仍可受理**，并如实披露自动拉起时限；仅「双无证据」时禁用。
- 开关位置在离线时取**持久化意图**，与「重启后按上次开关状态恢复」语义一致。
- 「点了开但起不来」可读：`start-failed` / `stop-failed` + 失败摘要，不再无限「切换中」；
  连续短命会话（凭证/配额故障）在页面可见。
- 单测 / E2E / 真栈人工验证覆盖。

**Non-Goals:**

- 不把研发搬进 Docker 容器（conda/qlib/百 MB 状态迁移成本大，见 Alternatives）；
- 不纳管收割日批 / 翻译等容器内定时任务（生命周期语义不同）；
- 不加鉴权、多用户审计、任意命令面（沿用上一版边界）；
- **不自动重启「进程活着但卡死」的代理**：本次事故是虚拟机停机，不是卡死；卡死需独立证据再立项
  （项目纪律：先归因再处置）。心跳里的 `snapshot_age_s` 会让页面如实暴露该形态；
- 不做「机器级看门狗」（Windows 服务常驻、失败告警通道），启动器本身失效时页面如实禁用即可。

## Approach

### 组件与职责

1. **宿主级启动器（Windows 计划任务）**：用户登录时 + 每 2 分钟，执行
   `wsl.exe -d rdagent -u root -e bash /root/ensure-factor-research.sh`；不并发实例、窗口隐藏。
   `scripts/register-factor-research-task.ps1` 幂等提供注册/更新/卸载。
2. **distro 入口 `ensure-factor-research.sh`（薄）**：调用代理 `--launcher-tick`。
3. **代理 `--launcher-tick`**：确保代理守护存活（复用 `--ensure-daemon` 的单例锁语义；已在跑返回
   既有 pid），原子写 `data/factor_research/launcher.json` 心跳（`checked_at` / `checked_at_epoch` /
   `agent_pid` / `agent_ok` / `snapshot_age_s` / `detail`），随后退出。**不调和、不写意图**。
4. **代理快照 `agent` 块**：`run_tick` 把 `reconcile_once` 的 action + 失败摘要作为 `agent_meta`
   传入采集器，写入快照；独立采集路径写 `agent: null`。
5. **后端**：读取 `launcher.json`（路径调用期 `FACTOR_RESEARCH_LAUNCHER_PATH`），派生 `age_seconds`
   / `fresh`（≤300 秒）；`control.honor` 单源判定（`live` / `launcher` / `unavailable`）；PUT 受理门
   与 GET 共用同一判定。
6. **前端**：离线可开 + ETA 文案、陈旧时位置取意图、失败可读、短命会话告警。

### 数据流

- **在线切换**：点击 → `PUT` → `control.json` → 代理 ≤15 秒调和 → 快照 → 页面翻转（不变）。
- **离线开启**：快照 stale + 心跳新鲜 → `honor=launcher` → 开关可点、明示 ≤3 分钟 → `PUT` 受理 →
  下一节拍启动 distro/代理 → 代理按 `desired_running=true` 拉起看护 → 首个快照 → 页面转「已开启」。
- **重启**：`control.json` 持久化 → 开机后启动器拉起代理 → 代理按期望态调和。开=继续，关=保持停止。

## Decisions

**D1 启动器落点 = Windows 计划任务（而非 `/etc/wsl.conf [boot] command`、而非容器化）。**
只有计划任务能启动**已停止**的 distro；boot command 只在 distro 因别的原因被启动时触发，无法覆盖
「重启后没人碰它」这一主场景。容器化（研发整体搬进 Docker）最干净但迁移成本高，且本次问题不需要
它。计划任务按登录态注册，与 Docker Desktop 的启动边界一致（页面存在的前提也是登录态栈在跑）。

**D2 心跳 = 独立文件 `launcher.json`，不并入 `status.json`。**
快照由代理写、心跳由启动器入口写，两者职责不同：代理卡死时快照会停，但启动器仍然活着——心跳正是
用来证明「手还在」，不能与快照同生共死。两个文件都原子写，后端分别派生新鲜度。

**D3 受理门 = 快照 OR（心跳新鲜且 `agent_ok`）；409 仅双无证据。**
上一版 D4 的动机是「不留无人受理的请求」。启动器存在后，请求有确定的受理方（≤2 分钟节拍），
「无人受理」前提消失；因此受理门从「快照新鲜」放宽为「有受理能力」，并把能力与 ETA 明示给用户。
双无证据时仍 409——真·机器级故障，不装活。

**D4 快照陈旧时，开关位置取持久化意图 `control.desired_running`。**
离线时实态不可证，意图是唯一可信状态；这也让「重启按上次开关状态恢复」在 UI 上自洽。快照新鲜时
仍取实态、不做乐观翻转（沿用上一版 D5）。

**D5 失败可读 = 快照新增 `agent` 块 + 前端派生短命会话告警。**
`start-failed` / `stop-failed` 让动作失败可判读；连续短命会话（10-18 的 key 过期形态：会话
23/24 秒死）用既有 `sessions[].duration_s` 派生，零新增采集面。阈值 120 秒 = supervisor 的短命
会话定义（`<120s` 触发 10 分钟退避），与研发侧既有语义对齐。

**D6 节拍 2 分钟 / 心跳新鲜 300 秒 / ETA 披露 180 秒。**
2 分钟是 Task Scheduler 的舒适粒度（最小 1 分钟）；300 秒 = 2×节拍 + 余量；180 秒与既有陈旧阈值
同值（节拍 + 代理拉起 + 调和 + 首个快照的保守上界）。所有阈值单源在常量中（后端一处、脚本一处）。

**D7 意图唯一写入面不变。**
启动器/ensure 入口 MUST NOT 写 `control.json`，也 MUST NOT 直接启停看护；启停动作仍由代理按期望态
调和（单一实现）。计划任务只保证「手在场」，不表达意图——这与上一版 D8 一致。

**D8 并发与幂等。**
代理单例锁（flock）保证重复节拍安全；计划任务设置「已在运行则不启动新实例」；手动脚本与启动器
共用同一 `--ensure-daemon` 语义。心跳写用临时文件 + `os.replace`，与状态/控制文件同纪律。

**D9 抖动与回退。**
心跳文件缺失/损坏/过期 → `launcher.available=false` / `fresh=false` → `honor=unavailable`（回到
上一版的禁用语义，fail-safe）。前端对旧后端（无 `launcher` 字段）按 `unavailable` 处理；旧读方
忽略新增字段，无破坏性。

## Alternatives Considered

- **A1 `/etc/wsl.conf` boot command**：只在 distro 被启动时触发，不能解决「重启后 distro 未启动」；
  可作第二保险（低成本），但不是主力。
- **A2 后端直接执行 distro 命令**：需要容器 → 宿主 → WSL 的通路（挂 docker socket / Windows 侧
  代理服务），攻击面与运维面大，沿用上一版否决理由。
- **A3 研发容器化**：最干净，但迁移 conda/qlib/状态体积不划算；留作长期方向。
- **A4 心跳并入 status.json**：实现省一个文件，但代理卡死会同时丢快照与心跳——恰好丢掉最需要
  区分的信息，否决。
- **A5 无条件受理（fire-and-forget）**：无启动器时请求永久悬挂且无解释，沿用上一版否决。
- **A6 自动重启卡死代理**：需要额外的探测/冷却/竞态设计；当前无证据支持，先由心跳暴露，再议。

## Risks / Trade-offs

- [计划任务依赖用户登录态] → 与 Docker Desktop 启动边界一致；若要无人值守，需「无论用户是否
  登录都运行」+ 凭据，留作可选项；失效时页面如实禁用（不装活）。
- [每 2 分钟一次 `wsl exec` 的常量开销] → 正常态 <2 秒；distro 停止时拉起 5–15 秒，仅发生在
  恢复路径。计划任务不并发，避免堆积。
- [9p 桥挂起导致心跳写失败] → 代理存活不受影响；后端按 `launcher` 不可用处理（fail-safe），
  页面回到禁用文案。
- [心跳与快照时刻不一致] → 页面同时展示两者时刻；ETA 是上界，不是承诺的精确值。
- [短命会话告警误报] → 阈值与研发侧「短命会话退避」定义（<120 秒）对齐；若实际出现正常短会话
  误报，按项目纪律先归因再调阈值，MUST NOT 直接改判定。
- [计划任务被安全软件拦截/系统休眠] → 心跳不新鲜 → 页面禁用并明示「研究侧与启动器均离线」；
  排障步骤入 README。

## Migration Plan

1. **distro 侧**：推 `ensure-factor-research.sh` / 更新 `factor_research_agent.py` 与
   `collect_factor_status.py`（CRLF 用 `tr -d '\r'`）；执行一次 `--launcher-tick` 生成首个心跳
   （不动看护、不改意图）。
2. **Windows 侧**：运行 `scripts/register-factor-research-task.ps1`（幂等注册；可 `-Unregister`）；
   验证 2 分钟内心跳持续更新。
3. **代码面**：后端端点 + 前端开关随 PR 合并；dev override 挂载下 `restart backend` 即生效。
4. **回滚**：卸载计划任务 → 心跳停止 → `honor` 退回 `live`（前端显示与上一版一致）；控制文件、
   快照均可被旧实现忽略（纯新增字段）。
5. **验证**：E2E stub 套件 + 真栈人工验证（kill 代理 / `wsl --terminate` / 离线点开 / 开着重启
   继续 / 关着重启保持），报告落 `tests/validation/`。

## Open Questions

（无——三项取向已由 Owner 拍板：自愈 + 离线可开 / 重启按上次开关状态恢复 / 离线可点并明示时长。）
