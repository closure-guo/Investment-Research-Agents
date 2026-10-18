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

## 5. 结论

- [x] 交互行为人工验证通过（真实栈点击 → 控制文件 → distro 代理调和 → 看护真停真启 → 恢复原运行态）
- [x] E2E 门禁：本改动 spec 全绿（三轮）；全量唯一红经基线复现定性为既有问题
- [x] 生产数据完整性复核通过（会话库 331 行、无测试残留；看护产物保全）
- [ ] 待 Owner：PR 复核；opencode key 过期处置（研发看护当前空转）
