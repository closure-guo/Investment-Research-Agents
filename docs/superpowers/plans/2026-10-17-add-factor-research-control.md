# 因子研发看护开关（add-factor-research-control）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 执行方式记录：本会话为**自主执行**（用户不实时在线），实施由主会话直接按 TDD 五步执行，
> 每任务完成后派发独立 reviewer（spec 合规 + 代码质量），最终全分支审查。偏差原因见会话记录。

**Goal:** `/factor-research` 页加研发看护启停开关，经宿主 `data/` 控制文件桥 ↔ WSL distro 常驻代理，
把既有 start/stop 脚本语义原样搬到界面。

**Architecture:** 控制文件 `data/factor_research/control.json`（后端 PUT 原子写、distro 代理轮询读）
表达布尔期望态；代理（观测 + 控制合一、独立于看护存活）按期望态调和启停（复用既有脚本语义）；
前端开关位置只反映快照实态，pending 期显示「切换中」；通道无存活证据（快照缺失/损坏/陈旧）时
端点 409 + 界面禁用。

**Tech Stack:** FastAPI(api.py) / React18+TS(Tailwind 令牌) / pytest / vitest / Playwright / Python3(distro 代理)

## Global Constraints

- 规范权威：`openspec/changes/add-factor-research-control/`（proposal/design/specs/tasks）——实现必须逐条对齐 delta spec。
- 测试纪律：先红后绿；`tests/scripts/spike/` 专属用例运行方式 `uv run pytest tests/scripts/spike/<f> -q`（不在默认套件，`norecursedirs`）。
- 后端端点：同步 IO 走 `asyncio.to_thread`；路径调用期读 env（`FACTOR_RESEARCH_CONTROL_PATH`，默认 `data/factor_research/control.json`）。
- 前端：复用既有令牌与组件（Button/Dialog/StatusBadge），不新造样式体系；`data-testid` 供 E2E。
- 代理（distro）：控制文件缺失/损坏 → 零动作；停止 MUST NOT 删数据；启停与手动脚本同语义；flock 串行。
- E2E 红线：不 mock 业务接口；fixture 路径必须 `test-e2e` 隔离；本地跑默认套件前停 Docker 占用的 8000/5173。
- 不得改 `openspec/specs/` 主库（只经 delta 编辑 + 收口时 sync）。

---

### Task 1: 研发代理（distro 侧 `factor_research_agent.py`）

**Files:**
- Create: `tests/scripts/spike/factor_research_agent.py`
- Test: `tests/scripts/spike/test_factor_research_agent.py`
- Modify: `tests/scripts/spike/collect_factor_status.py`（增量状态文件改原子写——新增并发写者）

**Interfaces (Produces):**
- `read_desired(path) -> tuple[bool|None, str|None, str|None]`（desired/requested_at/request_id；缺失/损坏/非法 → (None, None, None)）
- `reconcile_once(workdir: Path, control_path: Path) -> str`（"started"/"stopped"/"noop"/"no_intent"/"start-failed"/"stop-failed"）
- `start_supervisor(workdir) -> bool` / `stop_supervisor(workdir) -> bool`
- CLI：`--loop`（默认）/`--reconcile-once`/`--ensure-daemon`/`--set-intent {running,stopped}`；`--workdir/--control/--out/--state/--interval/--pidfile`

- [ ] Step 1: 写失败测试（read_desired 三态 / reconcile 六态 / stop 命令序列 / supervisor 脚本内容含
      pidfile+退避 / 停止不清数据 / 代理独立存活快照续写（fixture 目录 + collect_once 集成）/ flock 存在性）
- [ ] Step 2: 运行确认失败（`uv run pytest tests/scripts/spike/test_factor_research_agent.py -q` → FAIL import）
- [ ] Step 3: 最小实现（调和逻辑 + 启停动作（与旧脚本逐字同语义）+ collect 复用 + flock + CLI）
- [ ] Step 4: 运行确认通过（全绿）
- [ ] Step 5: 提交

### Task 2: 后端控制端点 + status `control` 块（`src/finance_agent/api.py`）

**Files:**
- Modify: `src/finance_agent/api.py`（因子研发监控节，~3696-3739）
- Test: `tests/test_api_factor_research.py`（扩展）

**Interfaces:**
- `GET /api/factor-research/status` → 附 `control: {desired_running, requested_at, pending}`
- `PUT /api/factor-research/control`（body `{"desired_running": bool}`）→ 200 `{"control": {...}}`；通道无证据 → 409 `control_unavailable`

- [ ] Step 1: 写失败测试（status control 三态 / PUT 原子写 + 幂等 + 409 三态不落文件 + 响应形状 + env 路径）
- [ ] Step 2: 运行确认失败（`uv run pytest tests/test_api_factor_research.py -q`）
- [ ] Step 3: 实现（`_factor_research_snapshot_payload` 拆出 + `_read_control_file` + `_control_block` + PUT + pydantic 模型）
- [ ] Step 4: 运行确认通过
- [ ] Step 5: 提交

### Task 3: 前端开关（`FactorResearchPage.tsx` + `types.ts`）

**Files:**
- Modify: `frontend/src/pages/factors/FactorResearchPage.tsx`（控制条 + 确认弹窗 + 提交逻辑 + 头注释）
- Modify: `frontend/src/types.ts`（`FactorResearchControl`）
- Test: `frontend/src/test/factorResearchPage.test.tsx`（扩展）

**Interfaces:**
- testid：`factor-research-control`（条）/`-switch`（role=switch）/`-state`/`-error`/`-confirm`（确认按钮）`-cancel`

- [ ] Step 1: 写失败测试（两态 / 停止二次确认（未确认不发请求）/ pending 切换中+禁用 / stale 禁用+理由 /
      缺失禁用 / 409 如实+位置不翻转 / 启动直发）
- [ ] Step 2: 运行确认失败（`cd frontend && npx vitest run src/test/factorResearchPage.test.tsx`）
- [ ] Step 3: 实现
- [ ] Step 4: 运行确认通过
- [ ] Step 5: 提交

### Task 4: E2E（默认套件）

**Files:**
- Modify: `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`（新增 3 用例）
- Modify: `tests/e2e/playwright/playwright.config.ts`（env 注入 `FACTOR_RESEARCH_CONTROL_PATH`）
- Modify: `.gitignore`（`data/test-e2e-factor-research-control.json*`）

- [ ] Step 1: 写用例（两态+确认写入控制文件+切换中→快照推进后翻转 / 陈旧禁用 / 缺失禁用）
- [ ] Step 2: 本地诚实跑（停 Docker 8000/5173 → `npx playwright test factor-research-monitor.spec.ts`）确认新用例失败（红）
- [ ] Step 3: 补齐实现缝隙（如有）→ 转绿
- [ ] Step 4: 默认套件全绿
- [ ] Step 5: 提交

### Task 5: 脚本薄包装 + 部署文档

**Files:**
- Modify: `tests/scripts/spike/start-factor-research.sh` / `stop-factor-research.sh` / `status-factor-research.sh`（薄包装）
- Modify: `tests/scripts/spike/README.md`（代理部署步骤）

- [ ] Step 1: 脚本改薄包装（确保代理 + set-intent + reconcile-once + 校验；stop 保留 bash 兜底硬停 + 兜底末次快照）
- [ ] Step 2: 本地 bash 语法检查（`bash -n`）
- [ ] Step 3: 提交

### Task 6: 收口（验证 + E2E 门禁 + 人工验证 + sync/archive + PR）

- [ ] 全量 pytest + spike 用例 + ruff + 前端 tsc + vitest 全绿
- [ ] E2E 默认套件绿（本地停 Docker 后跑）
- [ ] distro 部署（代理 + 脚本；停旧采集守护起新代理，看护不重启）
- [ ] 人工验证：真实栈点击停止 → 看护真停；点击启动 → 真起；恢复原态；报告落 `tests/validation/`
- [ ] sync + archive + PR

---

## Self-Review

- **Spec coverage**: 控制通道（T1）/ 控制端点（T2）/ status control 块（T2）/ 页面开关（T3+T4）/
  采集存活语义（T1 代理常驻 + T2 无变化）/ 部署（T5）/ 人工验证（T6）。✓
- **Placeholder scan**: 无 TBD/TODO；各步含确切命令与预期。✓
- **Type consistency**: `desired_running`/`requested_at`/`requested_at_epoch`/`request_id`/`pending`
  三处（代理/端点/前端）同名同型；testid 三处（页面/单测/E2E）一致。✓
