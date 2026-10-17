# 因子研发开关：/factor-research 看护启停控制面（add-factor-research-control）

## Why

因子研发监控页（add-factor-research-monitor）当前是**严格只读**：主规范明写「页面 MUST NOT 提供
启停/控制入口（只读页）」，控制面标注「另议」（后端容器无法执行 distro 命令）。于是研发看护
（RD-Agent 无界循环，持续消耗 LLM 额度）的启停只能到 WSL distro 终端手动跑
`start/stop-factor-research.sh`——产品面上没有开关。本 delta 补上控制面：监控页加「研发看护」
启停开关，经宿主 `data/` 文件桥（与快照同一条桥、反向）连通 distro 侧常驻代理，把**既有启停
语义原样**搬到界面（不新增研发侧行为、不改变闸门与入库链）。

## What Changes

- **ADDED「看护控制通道」（factor-research-monitor）**：distro 侧新增常驻**研发代理**（观测 + 控制）：
  周期轮询宿主可见控制文件（默认 `data/factor_research/control.json`，env 可覆盖），按**期望状态
  调和**语义管理看护——`desired_running=true` 且未运行 → 复用既有启动流程拉起；`false` 且运行中 →
  复用既有停止语义停止（终止当前会话）；期望=实态 → 不动作（幂等）。控制文件缺失/损坏 → 不干预
  （fail-safe，MUST NOT 猜测）。控制动作 MUST NOT 删除研发产物。
- **MODIFIED「状态快照采集契约」（factor-research-monitor）**：代理**独立于看护存活**——看护停止后
  不退出，继续周期写 `running=false` 快照（否则停机 3 分钟后监控页判陈旧、且「再开启」所需的
  控制通道也随之失联）；采集路径本身仍只读（MUST NOT 修改研发状态）。
- **ADDED「控制请求端点」（factor-research-monitor）**：`PUT /api/factor-research/control`
  （body：`desired_running` 布尔）——原子写控制文件；快照缺失/损坏/陈旧（控制通道无存活证据）→
  409 `control_unavailable`；仅接受布尔期望态，MUST NOT 引入任意命令面。
- **MODIFIED「只读查询端点」（factor-research-monitor）**：`GET /api/factor-research/status` 附加
  `control` 块（`desired_running` / `requested_at` / `pending`——pending = 期望与新鲜快照实态不一致）。
- **MODIFIED「因子研发监控页」（frontend）**：看护状态区新增启停开关——位置反映快照**实际**状态
  （不做乐观翻转）；停止需二次确认（明示终止当前会话）；pending 期显示「切换中」并禁用重复操作；
  快照缺失/损坏/陈旧或 409 → 开关禁用并明示理由（如「控制通道未接入」）；MUST NOT 把 pending 冒充
  已生效。页面其余仍只读。
- **实现面**：distro 侧 `factor_research_agent.py`（新：控制轮询 + 调和 + 进程锁 + 复用
  `collect_factor_status.py` 采集；启停动作单一来源在代理内）；`start/stop/status-factor-research.sh`
  改为薄包装（确保代理存活 + 写期望态 + 立即调和 + 校验，保留兜底硬停）；后端 `api.py` 控制端点 +
  status 附加 `control`；前端 `FactorResearchPage.tsx` 开关 + 确认弹窗 + `types.ts`。

## Capabilities

### New Capabilities

（无——全部落在既有 capability）

### Modified Capabilities

- `factor-research-monitor`: 采集存活语义（停止≠退出）、新增控制通道（代理调和）、新增控制请求
  端点、状态端点附加 `control` 块
- `frontend`: 因子研发监控页从只读页改为「观测 + 启停控制」（余仍只读）

## Impact

- 代码：`tests/scripts/spike/factor_research_agent.py`（新）、`start/stop/status-factor-research.sh`
  （重写为薄包装）、`src/finance_agent/api.py`（PUT 控制 + status 附加 control）、
  `frontend/src/pages/factors/FactorResearchPage.tsx`、`frontend/src/types.ts`。
- 测试：代理 pytest（调和三态/幂等/fail-safe/停止不清数据/锁串行，落 `tests/scripts/spike/` 专属先例）、
  后端端点测试、前端 vitest、E2E `factor-research-monitor.spec.ts` 增补开关用例（默认 config）。
- 运维：distro 部署步骤更新（README）；代理需常驻（distro 重启后需重新拉起——与既有看护同级的
  运维前提，不做开机自启）；旧采集守护由代理取代。
- **不做（明确边界）**：收割桥/翻译/日批任务的启停开关（本次只做研发看护）；多用户并发控制审计；
  非本机 distro 的通用远端化。
- 人工验证：真实栈点击开关 → distro 看护真停真启（含恢复原运行状态），报告落 `tests/validation/`。
