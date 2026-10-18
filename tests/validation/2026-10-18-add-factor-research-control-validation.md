# 人工验证报告: add-factor-research-control

**日期**: 2026-10-18
**验证人**: ZCode（自主执行；Owner 待 PR 复核）
**关联 delta**: `openspec/changes/add-factor-research-control/`
**E2E 门禁**: `tests/e2e/playwright/`（默认 config；结果见 §2）
**截图**: `tests/validation/2026-10-18-frc-stop-*.png` / `-start-1-running.png`
**驱动脚本**: `tests/e2e/playwright/validate-factor-research-control-live.cjs`

## 1. 验证环境与方法

- **真实后端**（本 delta 实现）：worktree 起 uvicorn `:8010`，`FACTOR_RESEARCH_STATUS_PATH` /
  `FACTOR_RESEARCH_CONTROL_PATH` 均指向宿主主检出真实 `data/factor_research/`（与 distro
  `/mnt/d` 桥同一文件），其余 DB 走隔离路径（`SESSIONS_DB_PATH=…/val-sessions.db`）。
  专用端口对（8010/5175）原因见 §4-4（8000/5173 被生产栈与 wslrelay 残留争用）。
- **真实前端**（本 delta 实现）：worktree vite `:5175`，`VITE_API_TARGET=http://127.0.0.1:8010`。
- **真实 distro**：`rdagent` distro 已部署新代理（`factor_research_agent.py`，pid 28200）+ 薄包装脚本；
  看护（supervisor pid 7752）处于运行态。浏览器经 Playwright chromium 真实点击 + 截图。

## 2. E2E 门禁（交互类变更）

- 本改动 spec `factor-research-monitor.spec.ts`：**8/8 绿**（原 5 例 + 新 3 例：控制文件真值断言 /
  停止二次确认与「切换中」/ 陈旧与缺失禁用）——单跑、与相邻 spec 子集、全量套件三轮均绿。
- 默认套件全量（本次改动，clean run）：**80 项 = 55 passed + 1 failed + 1 flaky（重试绿）+ 23 skipped**
  （23 skipped 全部为设计性跳过：@live stub 跳过、视觉基线无 BASELINE_DIR 跳过、串行文件前序失败连带）。
- 唯一红：`agui-session-switch-isolation.spec.ts:51`（AG-UI 会话切换隔离）——**在基线提交
  4e95ebfc（不含本改动）单独复跑同样失败**，属既有环境/瞬态问题，非本 delta 回归；与本改动范围
  （/factor-research 页 + 控制端点 + distro 脚本）无交集。
- run #1（无效轮）作废原因与污染处置见 §4-1。

## 3. 真实栈端到端（看护真停真启）

| 步骤 | 操作 | 观察 | 结果 |
|---|---|---|---|
| 初始 | 打开 `/factor-research`（应用内导航） | 开关到位=开、文案「已开启」；端点如实透传真快照 `running=true`、`control.desired_running=null` | ✅ |
| 停止·确认 | 点开关 → 弹窗（含「将终止当前研发会话（进行中的实验作废）…工作区与候选产物保留」）→ 确认 | 真实 `PUT` 落控制文件：`{"desired_running":false,"requested_at":"2026-10-18T08:32:00+08:00","request_id":"bb62ab996d16"}`；UI 转「切换中…（08:32:00 发起，等待研发侧受理）」且开关禁用、位置不翻转 | ✅ |
| 调和（真停） | （代理 ≤15s 轮询） | agent.log：`调和：desired_running=False 实态=True（request_id=bb62ab99…）` → 同秒 `action=stopped`；`factor_research.pid` 消失、`fin_factor` 会话进程终止；快照推进为 `running=false`（08:32:22） | ✅ |
| 幂等 | 后续周期 | `action=noop`（零重复动作） | ✅ |
| 数据保全 | 停止后核对 | workspace 任务 **720 个保持**；`candidates.json` 保持；控制文件与快照共存 | ✅ |
| 重新开启 | 点开关（无二次确认） | agent.log `action=started`；新看护 pid **33830** + 新会话 `session-20261018-083252.log`；快照 `running=true`；UI 恢复「已开启」 | ✅ |

截图证据：`frc-stop-1-running`（已开启）· `-2-confirm`（确认弹窗）· `-3-pending`（切换中）·
`-4-stopped`（已关闭）· `frc-start-1-running`（恢复开启）。

## 4. 事故与偏差记录（本次会话内发现/处置，供 Owner 知情）

1. **E2E run #1 误跑生产栈（incident 038 同型，已处置）**：Docker 生产栈在 run #1 启动前被**外部**
   拉起（docker events：00:18:35 `compose up`，非本会话发起），Playwright `reuseExistingServer`
   探活复用生产前后端 → run #1 结果**作废**（不采信）；且部分用例将 **24 条测试会话**写入生产
   `data/sessions.db`（00:18:59–00:22:07，测试命名）→ 已精准清理（删除前逐表核验：无报告 / 无
   predictions / 无 decision_log 关联；共删 24 sessions + 40 events），生产库复核 **331 会话完好**、
   无测试残留。教训：本机跑默认套件前必须确认 8000/5173 空闲（既有纪律），且启动后需复查未被复用。
2. **CRLF 部署坑（已修复回填）**：Windows 检出的 `.sh` 带 CRLF → distro 上 `$'\r'` 报错；
   部署改用 `tr -d '\r'`，README 部署步骤已更新（含显式全参建议）。
3. **workdir 空串自伤（已完全恢复）**：一次部署命令中变量被宿主 shell 吞掉 → 代理以 `--workdir .`
   （WSL 承接 Windows CWD=worktree）运行约 40 秒，写出 2 个错误快照（running=false）；旧采集器
   30 秒内覆盖恢复，看护本体全程未受影响；已 kill 并以显式全参重部署（复刻证据见 git 提交与本节）。
4. **端口争用环境问题（外部原因，建议后续专项治理）**：`localhost` 解析优先 `::1`，而 `[::1]:5173/8000`
   由 **wslrelay 残留**（转发到容器旧前端=主检出另一分支代码）与 Docker 代理占据 —— 本地 E2E/验证
   会随机「命中旧代码」；本次验证以 **8010/5175 专用端口对 + 127.0.0.1** 规避并全绿。属 incident 038
   同族的本机环境债，建议立 issue（本轮未动）。
5. **发现（非本改动引入）：研发看护自今晨起崩溃循环，根因 = opencode key 过期**：会话每 ~23 秒死于
   `litellm.AuthenticationError: This API key has expired. Create a new API key in OpenCode Console…`
   （distro `/root/rdagent-work/.env` 的 chat key；错误信息明确指引换 key）。本 delta 未触碰
   LLM/研发链路；**待 Owner 处置**——换 key 后看护即恢复（当前处于 10 分钟退避重试空转）。

## 4bis. 独立审查与修复轮（2026-10-18 上午）

按 SOP 派发双审查（规格合规 + 代码质量，general-purpose 只读）：

- **规格合规：通过**（0 Critical / 0 Important；4 项 Minor）。实测取证：spike 20/20、端点 23/23、
  vitest 19/19（修复轮前）。
- **代码质量：Needs fixes（5 Important + 5 Minor）**，修复轮全部处置（提交 `250971f4`）：
  | 编号 | 问题 | 处置 |
  |---|---|---|
  | I1 | 停止残留永不收敛、页面可能谎称「已关闭」 | 停止验证失败落残留标记，后续调和继续清扫；清洁时清标记（有专项测试） |
  | I2 | `ensure_daemon` 无单例、pidfile 无身份校验 | 守护生命周期持 `singleton.lock`（重复拉起拒绝）+ `/proc cmdline` 身份校验（PID 复用防误判） |
  | I3 | 等锁无超时，应急停止可被长采集卡死 | `reconcile-once` 限时 8s（超时告警降级继续）+ stop 脚本 `timeout 20` 兜底 |
  | I4 | 409 未即时禁用开关、失败提示不随轮询清除 | `channelBlocked` 置位（成功刷新解封）；`controlError` 随成功刷新清除 |
  | I5 | 写控制文件用例缺「非生产栈」前置守卫 | 点击前 fixture 读取探针（`collected_at` 逐字比对，不一致即拒绝继续） |
  | M1 | 多处固定 `.tmp` 名 | api.py 与采集器原子写改唯一 tmp 名（pid+随机后缀） |
  | M2 | stop 不再清理遗留采集守护 | stop 保留幂等清理 + status 增遗留行 |
  | M3 | 「长时间切换中」不可判读 | **挂账**（design Open Questions：候选加代理心跳/最近动作字段） |
  | M4 | CLI 退出码/拉起路径/互斥测试缺口 | 补 6 用例（退出码 0/1、env 默认、拉起参数、单例互斥、等锁超时、快照反映启动） |
  | M5 | 确认弹窗样式偏离先例 | 对齐 ConfirmSpendDialog（secondary+sm / sm） |
- 规格 Minor 亦已处置：损坏态/网络失败/非 dict 控制文件补测；代理 `--control/--out` 支持 env
  默认（规格「经环境变量可覆盖」）；「单一来源」措辞改为「共享同一启停语义（代理主实现 +
  脚本兜底）」；归档守卫实证（改名场景被拒 → 恢复原名「停止写末次快照」并改写正文）。
- **修复轮后复验**：spike 86/86、端点 23/23、vitest 826/826、`ruff` 全绿、`tsc` 绿、`mypy`
  存量 78 零新增、E2E 目标 spec 8/8（8602/5177 隔离配置复跑，含新守卫）。
- 环境事故另落 `docs/incidents/045`（本地 E2E 误复用生产栈 + [::1] 端口遮蔽；治理三项挂账）。

## 5. 结论

- [x] 交互行为人工验证通过（真实栈点击 → 控制文件 → distro 代理调和 → 看护真停真启 → 恢复原运行态）
- [x] E2E 门禁：本改动 spec 全绿（三轮）；全量唯一红经基线复现定性为既有问题
- [x] 生产数据完整性复核通过（会话库 331 行、无测试残留；看护产物保全）
- [ ] 待 Owner：PR 复核；opencode key 过期处置（研发看护当前空转）
