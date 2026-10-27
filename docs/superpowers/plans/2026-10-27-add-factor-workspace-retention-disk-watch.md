# add-factor-workspace-retention-disk-watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 落地 incident 051 防复发四件套：会话产物保留窗清理、双侧磁盘水位采集、低水位停发保护、探活读写双探针（delta：`openspec/changes/add-factor-workspace-retention-disk-watch/`）。

**Architecture:** distro 侧看护（bash 循环 + python 子命令）承担清理与停发；采集器每节拍写 `disk` 读数块；后端只读派生 `low_watermark` 判定（阈值单源、env 可覆盖、字面同步）；C# 启动器探活升级双探针；前端 /factor-research 水位卡 + 低水位横幅。

**Tech Stack:** Python 3.11（distro）/ pytest（host）、FastAPI api.py、React+TS+vitest、Playwright E2E、C# WinExe（csc 编译）。

## Global Constraints

- 阈值/默认值单源字面同步：distro `FACTOR_WORKSPACE_RETENTION_H=48`、`FACTOR_DISK_MIN_FREE_GB_GUEST=30`、`FACTOR_DISK_MIN_FREE_GB_HOST=20`、`FACTOR_WORKSPACE_BYTES_INTERVAL_S=1200`、宿主挂载 `FACTOR_RESEARCH_HOST_MOUNT=/mnt/d` ↔ api.py 同名常量。
- 读数失败如实置空（None），MUST NOT 编造；`--disk-check` 的 statvfs 全失败 → fail-open（放行）但记录错误（不静默阻断研发）。
- 快照契约：新增字段只增不改；旧快照（无 disk 块）端点派生与前端 MUST 降级不崩。
- 停发只拦新会话，MUST NOT 中断运行中会话；清理 MUST NOT 触碰保留窗内目录与运行中会话目录。
- C# 是 winexe（GUI 子系统），零窗口；测试以源码文本断言（test_launcher_scripts.py 先例）。
- 红线：E2E 禁 mock 被测系统业务接口；LLM/第三方用 TESTING=1 stub。
- 部署面：distro `/root/factor_research_agent.py`、`/root/collect_factor_status.py`（`tr -d '\r'` 落盘去 CRLF）；重启看护走 `POST /api/factor-research/restart`（既有通道）。

---

### Task 1: 保留窗清理 `prune_workspace` + `--prune` CLI

**Files:**
- Modify: `tests/scripts/spike/factor_research_agent.py`
- Test: `tests/scripts/spike/test_factor_research_agent.py`

**Interfaces:**
- Produces: `prune_workspace(workdir: Path, retention_h: int, *, now_epoch: float | None = None) -> dict`，返回 `{"deleted": [name...], "failed": [{"path","error"}], "kept": int, "freed_bytes": int}`；目录判定=**目录内最新文件 mtime**（os.walk，OSError 容忍跳过该文件）早于 `now - retention_h*3600` 才删。
- Produces: CLI `--prune [--retention-h N]`（缺省读 env `FACTOR_WORKSPACE_RETENTION_H`，默认 48）打印 JSON 结果，exit 0（失败目录仅记录不致非零）。
- Consumes: 无（Task 2 的看护脚本将调用此 CLI）。

- [ ] **Step 1: 写失败测试** `TestPruneWorkspace`：① 目录 A（内部文件 mtime=now-72h）删、目录 B（now-10h）与文件（非目录）与保留窗内目录全留；② 边界：mtime 恰好 `now-retention_h*3600-1` 删、`+0` 留；③ 某目录 os.walk 抛 OSError（monkeypatch）→ 进 `failed`，其余照删，返回值含 freed_bytes=已删目录 st_blocks*512 之和；④ CLI `--prune` 走 tmp_path workdir 返回合法 JSON。
- [ ] **Step 2: 跑红** `uv run pytest tests/scripts/spike/test_factor_research_agent.py -k prune -x -q` → FAIL
- [ ] **Step 3: 最小实现**（函数 + argparse 分支，风格对齐既有 CLI 分支早退模式）
- [ ] **Step 4: 跑绿** 同上命令 PASS
- [ ] **Step 5: Commit** `feat(spike): workspace 保留窗清理 prune_workspace（incident 051 防复发 1/4）`

### Task 2: 低水位判定 `disk_watermark` + `--disk-check` CLI（写暂停文件）

**Files:**
- Modify: `tests/scripts/spike/factor_research_agent.py`
- Test: `tests/scripts/spike/test_factor_research_agent.py`

**Interfaces:**
- Produces: `disk_watermark(*, guest_path="/", host_path=None, disk_usage_fn=None) -> dict`：`{"guest_free_bytes": int|None, "host_free_bytes": int|None, "low": bool, "reason": None|"guest"|"host", "thresholds": {"guest_min_bytes":…, "host_min_bytes":…}, "error": None|str}`。`disk_usage_fn(path)` 返回 `(total, used, free)`（测试注入）；任一 OSError → 该侧 None（fail-open 不计入 low），双侧全失败 → `low=False, error=…`。host_path 缺省取 env `FACTOR_RESEARCH_HOST_MOUNT`（默认 `/mnt/d`），路径不存在 → None。
- Produces: 阈值解析 `disk_thresholds() -> tuple[int,int]`（env `FACTOR_DISK_MIN_FREE_GB_GUEST/HOST`，GB→bytes，默认 30/20）。
- Produces: CLI `--disk-check`：计算后原子写 `<logdir>/.disk_pause.json`（`{"paused": bool, "reason": …, "guest_free_bytes":…, "host_free_bytes":…, "checked_at": iso}`，ok 时也写 `paused:false`——防陈旧暂停态残留）；exit 0=放行 / 2=低水位暂停 / 1=写文件失败。
- Consumes: `cfs.atomic_write_json`。

- [ ] **Step 1: 写失败测试** `TestDiskWatermark`：① happy 两阈值边界（恰等阈值=不 low；低 1 byte=low，reason 指向正确侧）；② guest OSError→None 且不计 low；③ 双侧失败→error 非空 low=False；④ env 阈值覆盖生效；⑤ `--disk-check` 低水位时写 `.disk_pause.json` paused=true 且 exit=2，正常时 paused=false exit=0。
- [ ] **Step 2: 跑红** 同文件 `-k disk` → FAIL
- [ ] **Step 3: 最小实现**
- [ ] **Step 4: 跑绿**
- [ ] **Step 5: Commit** `feat(spike): 低水位判定与暂停文件 --disk-check（incident 051 防复发 2/4）`

### Task 3: 看护脚本接线（prune + disk-check 进 SUPERVISOR_SCRIPT）

**Files:**
- Modify: `tests/scripts/spike/factor_research_agent.py`（`SUPERVISOR_SCRIPT` / `render_supervisor_script`）
- Test: `tests/scripts/spike/test_factor_research_agent.py`

**Interfaces:**
- Produces: 渲染后脚本含三段（新占位符 `{{AGENT}}`=`/root/factor_research_agent.py`、`{{DISK_BACKOFF}}`=`300`）：
  1. 启动会话前：`if ! python3 "{{AGENT}}" --disk-check; then sleep {{DISK_BACKOFF}}; continue; fi`
  2. 会话结束日志行之后：`python3 "{{AGENT}}" --prune >> "$LOGDIR/prune.log" 2>&1 || true`
  3. 既有 `{{WORKDIR}}/{{LAUNCH}}/{{SHORT_THRESHOLD}}/{{SHORT_BACKOFF}}/{{SESSION_GAP}}` 占位符保持逐字不变（render 兼容旧调用面）
- Consumes: Task 1/2 的 CLI。

- [ ] **Step 1: 写失败测试**：① render 输出含 `--prune`、`--disk-check`、`sleep 300`、`continue`；② 既有 5 占位符替换不回归；③ `bash -n` 校验渲染产物语法（subprocess `bash -n`，Windows 无 bash → skipif）。
- [ ] **Step 2: 跑红** `-k supervisor_render` → FAIL
- [ ] **Step 3: 实现**（heredoc 插两段 + 占位符替换）
- [ ] **Step 4: 跑绿**
- [ ] **Step 5: Commit** `feat(spike): 看护循环接保留窗清理与低水位停发（incident 051 防复发 3/4）`

### Task 4: 采集器 `disk` 读数块 + 后端派生 + C# 双探针

**Files:**
- Modify: `tests/scripts/spike/collect_factor_status.py`（collect_once 加 `disk` 块）
- Modify: `src/finance_agent/api.py`（`_factor_research_disk_block` + 挂 payload + 兜底形状）
- Modify: `scripts/FinanceAgentLauncher.cs`（rw 双探针）
- Test: `tests/scripts/spike/test_collect_factor_status.py`、`tests/test_api_factor_research.py`（如无则新建，参考既有 api 测试写法）、`tests/scripts/spike/test_launcher_scripts.py`

**Interfaces:**
- Produces（快照 `disk` 块，采集侧）: `{"guest_free_bytes": int|None, "host_free_bytes": int|None, "workspace_bytes": int|None, "paused_reason": str|None}`。statvfs 用 `shutil.disk_usage` 包一层（collect_once 新参 `disk_usage_fn=None` 注入）；`workspace_bytes` 走缓存（state 文件 `disk_cache={workspace_bytes, epoch}`，间隔 env `FACTOR_WORKSPACE_BYTES_INTERVAL_S` 默认 1200s，首轮同步算）；`paused_reason` 读看护暂停文件 `.disk_pause.json` 的 `reason`（paused=true 时）。
- Produces（端点派生）: `payload["disk"] = {"guest_free_gb": float|None, "host_free_gb": float|None, "workspace_gb": float|None, "low_watermark": bool, "low_watermark_reason": None|"guest"|"host"}`；阈值常量 `FACTOR_RESEARCH_DISK_MIN_FREE_GB_GUEST=30/HOST=20`（env 可覆盖，GB 判定）；快照缺 disk 块/available=false/corrupt → 全 None + low_watermark=False（不崩）。
- Produces（C#）: exec 探针成功后追加 rw 探针 `wsl -d {distro} -e /bin/sh -c ": > /tmp/.factor_probe_rw && rm /tmp/.factor_probe_rw"`（同 ProbeTimeoutMs）；rw 失败 → `ok=false`、`detail="fs_rw_failed"`、计入 consecutive_failures；exec 失败路径不变。

- [ ] **Step 1: 写失败测试**（三文件各一组）：① collect：fake disk_usage 正常→两读数;OSError→None;workspace 缓存命中（interval 内不重算，monkeypatch 计数）/过期重算;paused_reason 从暂停文件读出；② api：disk 块 GB 换算与 low_watermark 真值表（恰等/低/缺块/available=false）；③ cs：源码含 `factor_probe_rw` 与 `fs_rw_failed`（文本断言，test_launcher_scripts.py 先例）。
- [ ] **Step 2: 跑红** 三个目标文件各自 -k
- [ ] **Step 3: 实现**（api.py 阈值注释注明与 distro 侧字面同步；C# 在 ProbeAndMaybeHeal 内加第二 Probe 调用抽出 helper）
- [ ] **Step 4: 跑绿** + `uv run ruff check` + `uv run mypy`（api.py 在 mypy 覆盖内）
- [ ] **Step 5: Commit** `feat(monitor): 磁盘水位读数/端点派生/探活读写双探针（incident 051 防复发 4/4）`

### Task 5: 前端水位卡 + 低水位横幅（vitest + E2E）

**Files:**
- Modify: `frontend/src/lib/factorResearch.ts`（DiskBlock 类型 + 解析）
- Modify: `frontend/src/pages/factors/FactorResearchPage.tsx`
- Test: `frontend/src/test/factorResearchPage.test.tsx`、`tests/e2e/playwright/tests/factor-research-monitor.spec.ts`

**Interfaces:**
- Consumes: Task 4 的端点 `disk` 块。
- Produces: ① 水位卡（data-testid `factor-research-disk-card`）：guest/host 空闲（GB，1 位小数）+ workspace 占用；disk 块缺失 → 卡不渲染（旧快照兼容）。② 低水位横幅（data-testid `factor-research-low-watermark-banner`）：`low_watermark===true` 或 `paused_reason` 非空时显示，文案陈述事实+说明「新会话已暂停，非故障」，不提供处置入口（零命令面先例）。
- E2E（先读 `factor-research-monitor.spec.ts` 现有 stub 模式照抄）：场景 1=快照含低水位 disk 块 → 横幅可见；场景 2=正常 disk 块 → 横幅不可见且卡片渲染 GB 数字。

- [ ] **Step 1: vitest 失败用例**（横幅显隐 + 卡片渲染 + 缺块降级）→ 跑红 `cd frontend && npx vitest run src/test/factorResearchPage.test.tsx`
- [ ] **Step 2: 实现** → 跑绿
- [ ] **Step 3: E2E spec 两场景**（selector 必须来自真实 DOM：先跑一次带 stub 的探索或从 vitest 快照确认 data-testid；禁 waitForTimeout）
- [ ] **Step 4: E2E 跑绿** `cd tests/e2e/playwright && npx playwright test factor-research-monitor --project=stub`（具体 project 名以现有 config 为准，先查）
- [ ] **Step 5: Commit** `feat(frontend): /factor-research 磁盘水位卡与低水位横幅`

### Task 6: 部署 + 端到端验证 + 人工验证报告 + sync/archive

**Files:**
- Create: `tests/validation/2026-10-27-add-factor-workspace-retention-disk-watch-validation.md`
- Modify: `openspec/changes/add-factor-workspace-retention-disk-watch/tasks.md`（回填勾选）

- [ ] **Step 1: 部署 distro**：`wsl -d rdagent -u root -- bash -c "tr -d '\r' > /root/factor_research_agent.py"` < repo 版本；同法 collector；`/root` 下 `python3 -c "import factor_research_agent"` 自检。
- [ ] **Step 2: 重启看护**：`POST /api/factor-research/restart`（等当前会话自然结束后再发更佳；若会话刚起可先观察一步）；确认新看护脚本含 `--prune`/`--disk-check`。
- [ ] **Step 3: 实跑验证**：快照出现 `disk` 块且读数与 `df` 一致；手动造一个 mtime=72h 前的假目录 → 下一会话结束后被清、48h 内的真目录保留；`/api/factor-research` 返回 `disk` 派生块；页面水位卡渲染。
- [ ] **Step 4: 验证报告落 tests/validation/**（含读数截图/断言证据）。
- [ ] **Step 5: tasks.md 回填 → `openspec sync` → `openspec archive add-factor-workspace-retention-disk-watch`（走 openspec-archive-change skill）。**
