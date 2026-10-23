# 会话进度僵死检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「采集正常但研发会话无进展」成为可观测、可判定、可如实呈现的状态，终结 incident 049 那类「死锁 4 小时而页面显示运行中」的监控盲区。

**Architecture:** 沿用既有分层——distro 采集器只报**原始读数** `current_session.log_age_s`（日志 mtime 距采集时刻秒数）；后端 `GET /api/factor-research/status` 派生顶层 `progress` 块（`log_age_seconds` / `stalled`），阈值单源在后端常量；前端只披露不改语义（僵死时横幅 + 把步骤进度标注为陈旧读数）。零新增控制面。

**Tech Stack:** Python 3.12 / FastAPI / pytest（后端与 distro 采集器）；React 18 + TypeScript + vitest（前端）；Playwright（E2E）。

## Global Constraints

- **阈值单源**：`FACTOR_RESEARCH_STALL_AFTER_S = 900` 只在 `src/finance_agent/api.py` 定义；采集器与前端 MUST NOT 出现第二份 900（前端只读 `progress.stalled`）。
- **响应形状（顶层，与快照原始字段并列）**：`progress: {"log_age_seconds": int|null, "stalled": bool}`；快照不可用/陈旧时同样返回，取 `{null, false}`。
- **`stalled` 四条件**：快照 `available` 且非 `stale` + `supervisor.running=true` + `current_session.alive=true` + `log_age_seconds > 900`；缺一为 `false`。
- **快照字段名**：`current_session.log_age_s`（snake_case，与既有快照字段一致）；前端类型字段名同形。
- **文案口径**：无进展时长 MUST 经既有 `formatDurationS`（`frontend/src/lib/factorResearch.ts`）格式化——3600 秒 → `1小时0分`。
- **testid**：僵死横幅 `factor-research-stall-banner`；僵死时的进度读数块 `factor-research-progress-stale`（替换既有 `factor-research-step-timing`）。
- **零命令面**：横幅内 MUST NOT 有可点击的处置入口。
- **注释中文**；前端变量 camelCase；采集器只读观测（MUST NOT 干预研发进程）。
- 交付门禁：交互类变更 → E2E 门禁全绿 + 人工验证报告落 `tests/validation/`。

---

### Task 1: 采集器报 `log_age_s` 原始读数

**Files:**
- Modify: `tests/scripts/spike/collect_factor_status.py`（`collect_once` 内 `current_session` 构造处，约 :398-430）
- Test: `tests/scripts/spike/test_collect_factor_status.py`

**Interfaces:**
- Produces: 快照 `current_session.log_age_s: int | null`（日志 mtime 距 `now_epoch` 的秒数，非负；无会话 → 该字段不存在；stat 失败 → `null`）。

- [ ] **Step 1: 写失败测试**

在 `tests/scripts/spike/test_collect_factor_status.py` 末尾追加（沿用本文件既有 `_make_workdir` 助手；若该助手已创建会话日志，直接对它 `os.utime`，否则自行 `write_text` 一个 `session-*.log`）：

```python
class TestLogAge:
    def test_log_age_from_mtime(self, tmp_path):
        wd = _make_workdir(tmp_path, pid=os.getpid())
        logdir = wd / "factor_research_logs"
        log = sorted(logdir.glob("session-*.log"))[-1]
        os.utime(log, (1_800_000_000 - 42, 1_800_000_000 - 42))
        out = tmp_path / "status.json"
        state = logdir / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        assert snap["current_session"]["log_age_s"] == 42

    def test_log_age_null_when_stat_fails(self, tmp_path):
        """日志文件在 glob 与 stat 之间消失 → null（不猜、不填 0）。"""
        wd = _make_workdir(tmp_path, pid=os.getpid())
        logdir = wd / "factor_research_logs"
        log = sorted(logdir.glob("session-*.log"))[-1]
        log.unlink()
        out = tmp_path / "status.json"
        state = logdir / ".collector_state.json"
        snap = cfs.collect_once(
            wd, out, state, now_epoch=1_800_000_000.0, session_alive=lambda: True
        )
        assert snap["current_session"] is None  # 无会话 → 无该字段可报
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/scripts/spike/test_collect_factor_status.py -q -k LogAge`
Expected: FAIL（`KeyError: 'log_age_s'`）

- [ ] **Step 3: 实现**

`collect_once` 内，`latest = session_files[-1]` 之后加：

```python
        # 进度新鲜度原始读数：日志 mtime 距采集时刻的秒数（采集器只报读数，判定归后端）
        log_age_s = None
        try:
            log_age_s = max(0, int(now_epoch - latest.stat().st_mtime))
        except OSError:
            log_age_s = None
```

并在 `current_session` 字典里 `"step_eta_s"` 之后加一行：

```python
            "log_age_s": log_age_s,
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/scripts/spike/test_collect_factor_status.py -q`
Expected: 全绿（含既有用例）

- [ ] **Step 5: Commit**

```bash
git add tests/scripts/spike/collect_factor_status.py tests/scripts/spike/test_collect_factor_status.py
git commit -m "feat: 采集器上报会话日志新鲜度原始读数 log_age_s"
```

---

### Task 2: 后端派生 `progress` 块

**Files:**
- Modify: `src/finance_agent/api.py`（常量区 :4151 附近；`_factor_research_snapshot_payload` :4171-4199）
- Test: `tests/test_api_factor_research.py`

**Interfaces:**
- Consumes: 快照 `current_session.log_age_s`（Task 1）。
- Produces: 响应顶层 `progress: {"log_age_seconds": int|null, "stalled": bool}`；常量 `FACTOR_RESEARCH_STALL_AFTER_S = 900`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_api_factor_research.py` 追加（沿用本文件既有的快照 fixture 构造助手；下列断言即契约）：

```python
class TestProgressStall:
    def test_stalled_over_threshold(self, client, write_snapshot):
        write_snapshot(
            collected_at_epoch=time.time(),
            supervisor={"running": True, "pid": 1, "started_at": None},
            current_session={"log_file": "s.log", "started_at": None, "duration_s": 10,
                             "alive": True, "log_age_s": 3600, "factors": [], "log_tail": []},
        )
        body = client.get("/api/factor-research/status").json()
        assert body["progress"] == {"log_age_seconds": 3600, "stalled": True}

    def test_not_stalled_within_threshold(self, client, write_snapshot):
        """602 秒是实测历史最大静默上界 → 不告警。"""
        ...同构造但 log_age_s=602...
        assert body["progress"] == {"log_age_seconds": 602, "stalled": False}

    def test_supervisor_stopped_never_stalled(self, client, write_snapshot):
        """看护没在跑 ≠ 会话僵死。"""
        ...supervisor.running=False, current_session.alive=True, log_age_s=99999...
        assert body["progress"]["stalled"] is False

    def test_session_not_alive_never_stalled(self, client, write_snapshot):
        ...alive=False, log_age_s=99999...
        assert body["progress"]["stalled"] is False

    def test_legacy_collector_unreported(self, client, write_snapshot):
        """旧采集器无 log_age_s → null + false（不判定）。"""
        ...current_session 不含 log_age_s...
        assert body["progress"] == {"log_age_seconds": None, "stalled": False}

    def test_snapshot_unavailable(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTOR_RESEARCH_STATUS_PATH", str(tmp_path / "nope.json"))
        body = client.get("/api/factor-research/status").json()
        assert body["available"] is False
        assert body["progress"] == {"log_age_seconds": None, "stalled": False}
```

> 若本文件没有 `write_snapshot` 助手，按既有用例的写法直接写 `status.json`（`FACTOR_RESEARCH_STATUS_PATH` 指向 tmp_path）——不要新造 fixture 体系。

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_api_factor_research.py -q -k ProgressStall`
Expected: FAIL（`KeyError: 'progress'`）

- [ ] **Step 3: 实现**

常量区（`FACTOR_RESEARCH_STALE_AFTER_S` 之后）加：

```python
FACTOR_RESEARCH_STALL_AFTER_S = 900  # 会话日志静默阈值（实测历史最大静默 602s 的 1.5 倍）
```

`_factor_research_snapshot_payload` 之前加：

```python
def _factor_research_progress_block(snapshot: dict[str, Any]) -> dict[str, Any]:
    """进度新鲜度派生块（阈值单源：FACTOR_RESEARCH_STALL_AFTER_S）。

    ``stalled`` 需四条件同时成立：快照可用且非陈旧 + 看护运行中 + 会话存活 + 静默超阈值。
    看护未运行（日志本就不更新）或会话非存活（正常会话间隔）MUST NOT 判僵死。
    """
    session = snapshot.get("current_session")
    raw = session.get("log_age_s") if isinstance(session, dict) else None
    log_age = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None
    supervisor = snapshot.get("supervisor")
    running = bool(supervisor.get("running")) if isinstance(supervisor, dict) else False
    alive = bool(session.get("alive")) if isinstance(session, dict) else False
    stalled = bool(
        snapshot.get("available")
        and not snapshot.get("stale")
        and running
        and alive
        and log_age is not None
        and log_age > FACTOR_RESEARCH_STALL_AFTER_S
    )
    return {"log_age_seconds": log_age, "stalled": stalled}
```

`_factor_research_snapshot_payload` 两处返回值改为（不可用分支两个 reason 都要带）：

```python
        return {
            "available": False,
            "reason": "missing",  # 或 "corrupt"
            "progress": {"log_age_seconds": None, "stalled": False},
        }
```

可用分支在 `payload.update({...})` 之后、`return payload` 之前加：

```python
    payload["progress"] = _factor_research_progress_block(payload)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_api_factor_research.py -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/api.py tests/test_api_factor_research.py
git commit -m "feat: 状态端点派生 progress 块（会话僵死判定，阈值单源 900s）"
```

---

### Task 3: 监控页僵死披露

**Files:**
- Modify: `frontend/src/types.ts`（`FactorResearchStatusResponse` :1236-1258）
- Modify: `frontend/src/pages/factors/FactorResearchPage.tsx`（僵死横幅插在陈旧横幅之后 :403；进度读数块 :470-475）
- Test: `frontend/src/test/factorResearchPage.test.tsx`

**Interfaces:**
- Consumes: 端点 `progress` 块（Task 2）。
- Produces: `FactorResearchProgress` 类型；testid `factor-research-stall-banner` / `factor-research-progress-stale`。

- [ ] **Step 1: 写失败测试**

在 `frontend/src/test/factorResearchPage.test.tsx` 追加（沿用既有 `mockFetch` + `snapshotBody` 助手）：

```tsx
it('僵死：横幅可见且不把陈旧进度当当前进度', async () => {
  mockFetch(snapshotBody({
    progress: { log_age_seconds: 3600, stalled: true },
    current_session: {
      ...currentSessionBody(),
      step_elapsed_s: 603,
      step_eta_s: 905,
    },
  }))
  const banner = await screen.findByTestId('factor-research-stall-banner')
  expect(banner.textContent).toContain('无进展 1小时0分')
  expect(screen.getByTestId('factor-research-progress-stale').textContent).toContain('1小时0分')
  expect(screen.queryByTestId('factor-research-step-timing')).toBeNull()
})

it('未僵死：不显示横幅且照常显示步骤进度', async () => {
  mockFetch(snapshotBody({ progress: { log_age_seconds: 42, stalled: false } }))
  await screen.findByTestId('factor-research-progress')
  expect(screen.queryByTestId('factor-research-stall-banner')).toBeNull()
})

it('未上报新鲜度：不显示横幅也不声称进度正常', async () => {
  mockFetch(snapshotBody({ progress: { log_age_seconds: null, stalled: false } }))
  await screen.findByTestId('factor-research-progress')
  expect(screen.queryByTestId('factor-research-stall-banner')).toBeNull()
  expect(document.body.textContent).not.toContain('进度正常')
})
```

> `currentSessionBody()` 若不存在，用本文件既有的会话构造方式内联展开；`snapshotBody` 已支持传入 `current_session` 覆盖（既有用例已这样用）。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/test/factorResearchPage.test.tsx -t 僵死`
Expected: FAIL（找不到 `factor-research-stall-banner`）

- [ ] **Step 3: 实现**

`frontend/src/types.ts`：`FactorResearchStatusResponse` 之前加类型，并在接口内加字段：

```ts
export interface FactorResearchProgress {
  /** 当前会话日志 mtime 距采集时刻的秒数（旧版采集器未上报 → null） */
  log_age_seconds: number | null
  /** 采集正常但会话无进展（阈值单源在后端；未上报 → false 且不可判定） */
  stalled: boolean
}
```

```ts
  /** 进度新鲜度派生块（delta add-factor-research-stall-detection） */
  progress?: FactorResearchProgress | null
```

`frontend/src/pages/factors/FactorResearchPage.tsx`：在 `stale` 派生的同处加：

```tsx
  const stalled = data?.progress?.stalled === true
  const logAgeS = data?.progress?.log_age_seconds ?? null
```

陈旧横幅块（`{stale && (...)}`）之后插入：

```tsx
            {stalled && (
              <div
                data-testid="factor-research-stall-banner"
                className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
                style={{ background: 'var(--status-danger-default, #b91c1c)', color: 'white' }}
              >
                <i className="fas fa-exclamation-triangle"></i>
                当前会话无进展 {formatDurationS(logAgeS)}——进程仍存活但日志已停止更新，
                看护不会自动重拉；需经「重启管线」由人发起
              </div>
            )}
```

进度读数块改为（僵死时读数即历史值，MUST NOT 当当前进度展示）：

```tsx
                  {stalled ? (
                    <div
                      data-testid="factor-research-progress-stale"
                      className="text-xs"
                      style={{ color: 'var(--text-tertiary)' }}
                    >
                      进度读数来自 {formatDurationS(logAgeS)} 前的日志（会话无进展，读数已定格）
                    </div>
                  ) : (
                    (current.step_elapsed_s !== null || current.step_eta_s !== null) && (
                      <div data-testid="factor-research-step-timing" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                        本步已用 {formatDurationS(current.step_elapsed_s)}
                        {current.step_eta_s !== null && <> · 预计剩余 {formatDurationS(current.step_eta_s)}</>}
                      </div>
                    )
                  )}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run src/test/factorResearchPage.test.tsx`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/pages/factors/FactorResearchPage.tsx frontend/src/test/factorResearchPage.test.tsx
git commit -m "feat: 监控页披露会话僵死并停止冒充当前进度"
```

---

### Task 4: E2E 场景

**Files:**
- Modify: `tests/e2e/playwright/tests/factor-research-monitor.spec.ts`

**Interfaces:**
- Consumes: testid `factor-research-stall-banner`（Task 3）。

- [ ] **Step 1: 写失败 spec**

沿用本文件既有的快照注入方式（既有用例已能构造 `stale=true` 快照），新增用例：

```ts
test('会话僵死 → 明示无进展且不显示预计剩余', async ({ page }) => {
  // 注入 progress.stalled=true + 陈旧步骤读数（沿用本文件既有的快照注入助手）
  await page.goto('/factor-research')
  const banner = page.getByTestId('factor-research-stall-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('无进展')
  await expect(page.getByTestId('factor-research-step-timing')).toHaveCount(0)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd tests/e2e/playwright && npx playwright test factor-research-monitor.spec.ts --config playwright.isolated.config.ts -g 僵死`
Expected: FAIL（找不到横幅）

> **必须用 `playwright.isolated.config.ts`**（侦察已核实）：默认 config 的 `reuseExistingServer: !CI` 在本机会复用**生产栈**（8000/5173 当前正被 docker 生产容器占用），届时 `PUT /api/factor-research/control` 会写**生产控制文件**——正是 incident 038/044/045 的失效形态（红线）。隔离 config 用 8001/5174、`reuseExistingServer:false`、且 `FACTOR_RESEARCH_*_PATH` 全部指向 `data/test-e2e-factor-research-*.json`，与生产 `data/factor_research/*` 物理隔离。

- [ ] **Step 3: 实现（本任务无产品代码；失败通常来自注入未生效或 Task 3 未合）**

按 `docs/project-workflow.md` §5.5 红线自检：无恒真断言、`expect` 均带 await、selector 来自真实快照。

- [ ] **Step 4: 运行确认通过**

Run: `cd tests/e2e/playwright && npx playwright test factor-research-monitor.spec.ts`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/playwright/tests/factor-research-monitor.spec.ts
git commit -m "test: E2E 覆盖监控页会话僵死披露"
```

---

### Task 5: 部署采集器到 distro 并真实验证（控制器执行，非子代理）

**Files:**
- Modify（distro）: `/root/collect_factor_status.py`

- [ ] **Step 1: 推送并重启代理守护**

```bash
tr -d '\r' < /mnt/d/WorkSpace/finance_analysis_agent/tests/scripts/spike/collect_factor_status.py \
  > /root/collect_factor_status.py
python3 -m py_compile /root/collect_factor_status.py
# 重启代理守护（不看看护；沿用 tests/scripts/spike/README.md 2026-10-21 节的部署脚本语义）
```

- [ ] **Step 2: 验证真实快照**

```bash
python3 - <<'PY'
import json, os
from pathlib import Path
p = Path("/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/status.json")
s = json.loads(p.read_text())
cs = s["current_session"]
log = Path("/root/rdagent-work/factor_research_logs") / cs["log_file"]
print("log_age_s =", cs.get("log_age_s"), "| mtime 差 =", int(os.path.getmtime(log)))
PY
```

Expected：`log_age_s` 与日志 mtime 差一致（±2 秒）

- [ ] **Step 3: 验证端点派生**

```bash
curl -s http://127.0.0.1:8000/api/factor-research/status | python -c "import json,sys; print(json.load(sys.stdin)['progress'])"
```

Expected：`{'log_age_seconds': <小整数>, 'stalled': False}`（健康会话不误报）

---

## 交付门禁

- `uv run pytest tests/test_api_factor_research.py tests/scripts/spike/test_collect_factor_status.py -q` 全绿
- `cd frontend && npx vitest run src/test/factorResearchPage.test.tsx` 全绿
- `cd tests/e2e/playwright && npx playwright test factor-research-monitor.spec.ts` 全绿（隔离栈）
- 人工验证报告落 `tests/validation/2026-10-22-add-factor-research-stall-detection-validation.md`
- `openspec validate add-factor-research-stall-detection --strict` 通过
