# 人工验证报告: add-factor-research-selfheal

**日期**: 2026-10-19
**验证人**: Agent（在执行所有者批准「部署并恢复研发」后执行；关键状态截图留档）
**关联 delta**: `openspec/changes/add-factor-research-selfheal/`
**E2E 门禁**: `tests/e2e/playwright/tests/factor-research-monitor.spec.ts` 12/12 通过
（默认 config 的 8000/5173 被开发栈占用 → 用等价一次性 config 在 8006/5179 隔离端口 + 隔离
DB/夹具运行；spec 与断言未改动。MUST NOT 打到生产栈的守卫用例 6 通过。）

## 环境

- 宿主：Windows，`rdagent` WSL2 distro（Ubuntu，root）；研发看护真实运行、真实 LLM 会话。
- 页面/后端：worktree 代码（本地 uvicorn `127.0.0.1:8010` + vite `5180`），**读取真实**
  `data/factor_research/{status,control,launcher}.json`。生产 Docker 栈仍是合并前镜像（无
  `launcher`/`honor` 字段），故页面级验证走本地实例；真机自愈验证用真实 distro/计划任务。
- 部署：`ensure-factor-research.sh` / `factor_research_agent.py` / `collect_factor_status.py`
  经 `tr -d '\r'` 推入 `/root`；计划任务 `finance-agent-factor-research-launcher` 注册为 Ready。

## 验证结果

| # | Scenario | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 首次拉起（停滞 20h 后） | 启动器写心跳并拉起代理；代理按 `desired_running=true` 调和启动看护 | 19:59:52 `launcher tick: agent_ok=True pid=252`（`snapshot_age_s=72024`）；20:00:38 快照推进、`supervisor.running=true pid=253`、`agent.last_action` 出现 | ✅ |
| 2 | 代理被杀自愈 | ≤2 分钟内计划任务重启代理，看护不受影响 | 20:01:02 kill 252 → 20:02:56 任务触发，新代理 pid=378、心跳更新；快照 20:03:10 继续推进，看护 pid 253 未变 | ✅ |
| 3 | distro 终止自愈 | ≤2 分钟内 distro/代理/看护恢复，研发继续 | 20:03:22 `wsl --terminate rdagent` → 20:04:56 任务拉起 distro，代理 pid=143，看护 pid=144，新会话 `session-20261019-200455.log`；快照恢复推进 | ✅ |
| 4 | 启动器不写意图 | 连续节拍后控制文件字节不变 | 部署前 `control.json` SHA256 `8C8A0696…DA0ED8F`；代理运行 ~13 分钟（数十个节拍，期间发生一次调和启动看护）后 20:13 复核完全一致 | ✅ |
| 5 | 开着重启继续 | 意图=开时 distro 重启后自动继续研发 | 场景 3 即该语义（`desired_running=true` 持久化，重启后自动拉起看护并开新会话） | ✅ |
| 6 | 关着重启保持停止 | 意图=关时 distro 重启后保持停止 | 20:13 PUT false → `running=false`；20:13:10 terminate → 20:14:56 代理/看护恢复后 `running=false`、`agent.last_action=noop`、无 `fin_factor` 进程 | ✅ |
| 7 | 离线可开（真实窗口） | 快照陈旧 + 心跳新鲜 → 开关可点、明示 ≤3 分钟；点击真实写入意图 | 停用计划任务 + 终止 distro 后 20:21 实测 `stale=true(222s)`、`launcher_age=219s`、`honor=launcher`；页面「研究侧离线（最近采集 …）——将在 ≤3 分钟内自动拉起」、开关可用（关）；点击后 `desired_running=false→true`，页面转「已请求（20:22:11），等待启动器拉起」 | ✅ |
| 8 | 点击后自愈闭合 | 计划任务恢复后 ≤2 分钟拉起研究侧，页面转「已开启」 | 20:22:20 启用任务 → 20:22:56 自愈（`launcher_age=114s`、`honor=live`、新会话 `session-20261019-202256.log`），页面「已开启」 | ✅ |

### 截图

| 状态 | 文件 |
|---|---|
| 在线：已开启（honor=live） | `tests/validation/2026-10-19-selfheal-online.png` |
| 离线可开：陈旧 + 心跳新鲜（≤3 分钟，开关可点） | `tests/validation/2026-10-19-selfheal-offline-openable.png` |
| 已请求：等待启动器拉起 | `tests/validation/2026-10-19-selfheal-waiting-launcher.png` |
| 自愈恢复后：已开启 | `tests/validation/2026-10-19-selfheal-online-after-heal.png` |

## 自动化验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端单测（含 spike 代理/采集器/启动器脚本） | `uv run pytest tests/test_api_factor_research.py tests/scripts/spike/test_factor_research_agent.py tests/scripts/spike/test_collect_factor_status.py tests/scripts/spike/test_launcher_scripts.py -q` | 96 passed |
| 前端单测 | `cd frontend && npx vitest run` | 842 passed（97 files） |
| 类型检查 | `cd frontend && npx tsc --noEmit` | 0 错误 |
| Lint | `uv run ruff check` | All checks passed |
| 类型检查（后端） | `uv run mypy` | 78 errors / 20 files —— 均为存量（无新增；新增函数与测试零报错） |
| E2E 门禁 | `npx playwright test --config <隔离端口一次性 config> tests/factor-research-monitor.spec.ts` | 12/12 passed |
| OpenSpec | `openspec validate add-factor-research-selfheal --strict` | valid |

## 异常记录

1. **宿主机跑 spike 测试会静默自杀**：`collect_factor_status._process_alive` 用 `os.kill(pid, 0)`，
   Windows 下等价 `TerminateProcess`，pytest 进程被无声终止（exit 1、零输出）。本地验证用一次性
   `-p` 插件把 `sig=0` 探测改为 `OpenProcess` 存活查询（未入库）；CI（Linux）不受影响。属工具链
   环境问题，非本次变更缺陷——已记录，后续可考虑在 spike 测试里显式冻结该实现。
2. **生产栈页面仍是旧代码**：Docker 镜像重建需要合并后执行；本次页面级证据来自同一份代码的本地
   实例（读真实文件）。合并 + `docker compose up -d --build backend web` 后需在 `/factor-research`
   做一次目视抽查（4 张截图对应状态）。
3. 验证期间为构造「离线可开」窗口曾临时停用计划任务并多次终止 distro，验证结束后已恢复（任务
   Ready、研发运行中）。

## 结论

- [x] 全部通过（真栈自愈 + 页面状态），可进入合并
- [ ] 合并后需补一次生产栈目视抽查（异常记录 2）；此后才能 sync + archive
