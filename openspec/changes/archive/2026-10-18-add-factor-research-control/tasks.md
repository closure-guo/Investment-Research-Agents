# Tasks: add-factor-research-control

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T1 研发代理（distro 侧 Python，tests/scripts/spike/）

- [x] 先红后绿：控制文件解析与调和判定——`desired_running=true` 且未运行 → 启动动作；
      `false` 且运行中 → 停止动作；期望=实态 → 零动作（幂等）（`test_factor_research_agent.py` 20/20）
- [x] 先红后绿：fail-safe——控制文件缺失 / 非合法 JSON / `desired_running` 缺失或非布尔 →
      零启停动作 + 留日志（MUST NOT 猜测期望态）（真机实证：`reconcile: no_intent`）
- [x] 先红后绿：启停动作实现（启动 = 写 supervisor 脚本 + setsid 拉起 + pidfile 校验；
      停止 = kill 子进程/看护/`pkill -f fin_factor` 兜底序列 + 校验；与旧脚本逐字同语义）
- [x] 先红后绿：代理独立存活——看护停止后代理继续周期采集（快照 `running=false` 且
      `collected_at_epoch` 推进）；停止动作 MUST NOT 删除工作区/候选/日志（真机实证：workspace 720 保持）
- [x] `flock` 串行化「守护轮询」与「手动 reconcile-once」；CLI：`--loop`（默认守护）/
      `--reconcile-once` / `--ensure-daemon` / `--set-intent`
- [x] `start/stop/status-factor-research.sh` 改薄包装（确保代理 + 原子写期望态 + 立即调和 +
      校验；stop 保留 bash 兜底硬停）；复用 `collect_factor_status.py` 采集（只读面不动，
      增量状态文件改原子写防并发半读）

## T2 后端端点（api.py）

- [x] 先红后绿（pytest）：`GET /api/factor-research/status` 附加 `control` 块——控制文件缺失 →
      `desired_running=null`；`pending` 三态（期望≠实态且新鲜 → true；一致/陈旧/不可用 → false）
- [x] 先红后绿：`PUT /api/factor-research/control`——原子写（含 requested_at/request_id）；
      幂等重复；409 `control_unavailable`（快照缺失/损坏/陈旧三态，且不写控制文件）；
      调用期读 `FACTOR_RESEARCH_CONTROL_PATH`；StrictBool + `extra="forbid"`（零命令面）；
      同步 IO 走 `asyncio.to_thread`（`test_api_factor_research.py` 23/23）

## T3 前端监控页（FactorResearchPage.tsx）

- [x] 先红后绿（vitest）：看护启停开关——两态位置正确（按快照实态，不乐观翻转）；
      pending → 「切换中」+ 禁用；陈旧/缺失/损坏 → 禁用 + 明示理由；停止二次确认（含「终止当前
      研发会话」语义）后才发请求；409/网络失败如实提示且位置不翻转（`factorResearchPage.test.tsx` 19/19）
- [x] `types.ts` 扩展（control 块 + 请求类型）；页面头注释更新（只读页 → 观测 + 控制）

## T4 E2E（默认套件，真实后端 + 文件驱动，红线：不 mock 业务接口）

- [x] 新用例：运行中 → 开关为开；确认停止 → 控制文件真写入（断言文件内容）+「切换中」+ 禁用；
      fixture 推进快照 `running=false` → 开关翻转为关恢复可操作
- [x] 新用例：陈旧快照 → 开关禁用 + 理由文案；缺失快照 → 空态 + 开关禁用
- [x] 默认 config env 注入 `FACTOR_RESEARCH_CONTROL_PATH`（隔离路径，防落生产控制文件）

## 收口

- [x] `uv run pytest -m "not live"` 全量绿（**5779 passed / 6 skipped / 12 deselected**）+
      spike 专属 20/20 + `ruff check` 全绿 + 前端 `tsc --noEmit` 绿 + 全量 `vitest` 绿（**823/823**）；
      `mypy` 存量 78 错（历史包袱，新代码零新增）
- [x] `openspec validate add-factor-research-control --strict` 通过
- [x] E2E 默认套件：本改动 spec **8/8 绿**（三轮）；全量 55 passed，唯一红
      `agui-session-switch-isolation` 在**基线提交 4e95ebfc 复现同败**（既有问题非本回归，见验证报告 §2）
- [x] distro 部署：代理 + 脚本推入 `rdagent` distro（停旧采集守护 → 起新代理，看护未重启），
      README 部署步骤更新（含 CRLF 去 CR 坑回填）
- [x] 人工验证（真实栈）：界面点停止 → 看护真停（agent.log `action=stopped`、pidfile 消失、
      快照 `running=false`）；点启动 → 看护真起（新 pid + 新会话）；恢复原运行状态；
      报告落 `tests/validation/2026-10-18-add-factor-research-control-validation.md`
- [x] sync + archive + PR（PR 描述含实跑读数）
