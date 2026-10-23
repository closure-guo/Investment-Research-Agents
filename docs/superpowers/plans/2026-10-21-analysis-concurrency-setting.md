# 批量分析并发设置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在设置中心「会话管理」分区新增「批量分析最大并发数」设置项，默认 10、范围 1–20，保存后立即作用于 `AnalyzeQueue` 并持久化。

**Architecture:** 新增 `analysis_concurrency` 模块负责「值从哪来 / 怎么写」（文件 > env > 默认，如实披露来源）；`AnalyzeQueue` 增加运行期 `set_max_concurrency`；API 暴露 GET/PUT 并在 PUT 后把生效值应用到队列单例；前端 SessionsPane 增加设置区块。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic / pytest / React 18 + TS + Vitest / Playwright

## Global Constraints

- 默认值 **10**，合法区间 **1–20**；来源优先级：设置文件 > 环境变量 `SCREENING_ANALYZE_CONCURRENCY` > 默认值
- 非法入参（非整数 / 布尔 / 越界）→ 400，且不落盘、不改变运行中队列
- 降低上限 MUST NOT 终止已运行分析；提高上限 SHALL 立即补位
- 设置文件损坏 / 字段非法 → 回退并披露 `file_status`（`corrupt` / `invalid`），MUST NOT 500
- 设置文件路径 env `ANALYSIS_CONCURRENCY_PATH` 可覆盖，默认 `data/settings/analysis_concurrency.json`（gitignored）
- E2E 禁止 mock 被测系统；状态准备经真实 API / 文件 fixture

---

### Task 1: 并发设置模块（读写 / 校验 / 来源披露）

**Files:**
- Create: `src/finance_agent/analysis_concurrency.py`
- Test: `tests/test_analysis_concurrency.py`

**Interfaces:**
- Produces: `DEFAULT_MAX_CONCURRENCY: int`, `MIN_MAX_CONCURRENCY: int`, `MAX_MAX_CONCURRENCY: int`, `ENV_VAR: str`, `settings_path() -> Path`, `validate(value: Any) -> int`, `resolve() -> dict[str, Any]`, `save(value: Any) -> dict[str, Any]`
- `resolve()/save()` 返回键：`value` / `source`(`file|env|default`) / `default` / `min` / `max` / `env_value`(`int|null`) / `file_status`(`ok|missing|corrupt|invalid`)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_analysis_concurrency.py
from __future__ import annotations

import json

import pytest

from finance_agent import analysis_concurrency as ac


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(ac.ENV_VAR, raising=False)
    monkeypatch.setenv("ANALYSIS_CONCURRENCY_PATH", str(tmp_path / "analysis.json"))
    return tmp_path / "analysis.json"


def test_default_is_ten(_isolated):
    body = ac.resolve()
    assert body["value"] == 10
    assert body["source"] == "default"
    assert body["file_status"] == "missing"
    assert body["env_value"] is None


def test_env_fallback(_isolated, monkeypatch):
    monkeypatch.setenv(ac.ENV_VAR, "3")
    body = ac.resolve()
    assert body["value"] == 3
    assert body["source"] == "env"


def test_save_persists_and_file_wins_over_env(_isolated, monkeypatch):
    monkeypatch.setenv(ac.ENV_VAR, "3")
    saved = ac.save(7)
    assert saved["value"] == 7 and saved["source"] == "file"
    assert saved["env_value"] == 3  # 被覆盖的环境变量如实披露
    on_disk = json.loads(_isolated.read_text(encoding="utf-8"))
    assert on_disk["max_concurrency"] == 7
    assert ac.resolve()["value"] == 7


@pytest.mark.parametrize("value", [0, 21, -1, True, "7", 7.0, None])
def test_save_rejects_invalid(value, _isolated):
    with pytest.raises(ValueError):
        ac.save(value)
    assert ac.resolve()["value"] == 10  # 未落盘


def test_corrupt_file_falls_back(_isolated, monkeypatch):
    _isolated.parent.mkdir(parents=True, exist_ok=True)
    _isolated.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv(ac.ENV_VAR, "3")
    body = ac.resolve()
    assert body["value"] == 3
    assert body["source"] == "env"
    assert body["file_status"] == "corrupt"


def test_invalid_saved_value_falls_back(_isolated):
    _isolated.parent.mkdir(parents=True, exist_ok=True)
    _isolated.write_text(json.dumps({"max_concurrency": 99}), encoding="utf-8")
    body = ac.resolve()
    assert body["value"] == 10
    assert body["source"] == "default"
    assert body["file_status"] == "invalid"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_analysis_concurrency.py -v`
Expected: FAIL（`ModuleNotFoundError: finance_agent.analysis_concurrency`）

- [ ] **Step 3: 最小实现**

```python
# src/finance_agent/analysis_concurrency.py
"""批量分析最大并发数设置（delta add-analysis-concurrency-setting）。

来源优先级：设置文件 > 环境变量 ``SCREENING_ANALYZE_CONCURRENCY`` > 默认值。
文件缺失/损坏/字段非法时回退并如实披露 ``file_status``，不以默认值冒充已保存设置。
本模块只管「值从哪来、怎么写」；运行期生效由 api 层应用到 AnalyzeQueue。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_logger = logging.getLogger("finance_agent.analysis_concurrency")

DEFAULT_MAX_CONCURRENCY = 10
MIN_MAX_CONCURRENCY = 1
MAX_MAX_CONCURRENCY = 20
ENV_VAR = "SCREENING_ANALYZE_CONCURRENCY"


def settings_path() -> Path:
    return Path(
        os.environ.get("ANALYSIS_CONCURRENCY_PATH", "data/settings/analysis_concurrency.json")
    )


def _valid(value: Any) -> int | None:
    """合法值 → int；布尔/非整数/越界 → None（bool 是 int 子类，先排除）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not MIN_MAX_CONCURRENCY <= value <= MAX_MAX_CONCURRENCY:
        return None
    return value


def validate(value: Any) -> int:
    parsed = _valid(value)
    if parsed is None:
        raise ValueError(
            f"最大并发分析数必须是 {MIN_MAX_CONCURRENCY}–{MAX_MAX_CONCURRENCY} 的整数: {value!r}"
        )
    return parsed


def _env_value() -> int | None:
    raw = os.getenv(ENV_VAR)
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning("非法 %s=%r，忽略", ENV_VAR, raw)
        return None
    if _valid(parsed) is None:
        _logger.warning("%s=%r 越界，忽略", ENV_VAR, parsed)
        return None
    return parsed


def _read_settings_file() -> tuple[int | None, str]:
    path = settings_path()
    if not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, "corrupt"
    if not isinstance(data, dict):
        return None, "corrupt"
    value = _valid(data.get("max_concurrency"))
    if value is None:
        return None, "invalid"
    return value, "ok"


def resolve() -> dict[str, Any]:
    saved, file_status = _read_settings_file()
    env_value = _env_value()
    if saved is not None:
        value, source = saved, "file"
    elif env_value is not None:
        value, source = env_value, "env"
    else:
        value, source = DEFAULT_MAX_CONCURRENCY, "default"
    return {
        "value": value,
        "source": source,
        "default": DEFAULT_MAX_CONCURRENCY,
        "min": MIN_MAX_CONCURRENCY,
        "max": MAX_MAX_CONCURRENCY,
        "env_value": env_value,
        "file_status": file_status,
    }


def save(value: Any) -> dict[str, Any]:
    parsed = validate(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "max_concurrency": parsed,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return resolve()
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_analysis_concurrency.py -v`
Expected: PASS（13 passed）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/analysis_concurrency.py tests/test_analysis_concurrency.py
git commit -m "feat(settings): 批量分析并发设置模块（文件>env>默认 10）"
```

---

### Task 2: AnalyzeQueue 运行期调整上限

**Files:**
- Modify: `src/finance_agent/screening/queue.py`
- Test: `tests/test_api_screening.py`（队列段）

**Interfaces:**
- Consumes: `finance_agent.analysis_concurrency.DEFAULT_MAX_CONCURRENCY`
- Produces: `AnalyzeQueue.set_max_concurrency(value: int) -> None`

- [ ] **Step 1: 写失败测试（追加到 `tests/test_api_screening.py` 队列段）**

```python
def test_queue_set_max_concurrency_rejects_invalid() -> None:
    queue = AnalyzeQueue(max_concurrency=2)
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ValueError, match="max_concurrency"):
            queue.set_max_concurrency(bad)  # type: ignore[arg-type]


async def test_queue_raise_limit_promotes_queued(monkeypatch) -> None:
    started: list[str] = []
    queue = AnalyzeQueue(
        max_concurrency=1, poll_interval=0.01, starter=lambda symbol, sid: started.append(sid)
    )
    monkeypatch.setattr("finance_agent.session_store.get_session", lambda sid: {"status": "running"})
    queue.enqueue("600001", "s1")
    queue.enqueue("600002", "s2")
    queue.enqueue("600003", "s3")
    assert started == ["s1"]
    queue.set_max_concurrency(3)
    assert started == ["s1", "s2", "s3"]
    for entry in queue._entries:
        if entry.task is not None:
            entry.task.cancel()


async def test_queue_lower_limit_keeps_running(monkeypatch) -> None:
    started: list[str] = []
    queue = AnalyzeQueue(
        max_concurrency=2, poll_interval=0.01, starter=lambda symbol, sid: started.append(sid)
    )
    monkeypatch.setattr("finance_agent.session_store.get_session", lambda sid: {"status": "running"})
    queue.enqueue("600001", "s1")
    queue.enqueue("600002", "s2")
    queue.enqueue("600003", "s3")
    queue.set_max_concurrency(1)
    assert started == ["s1", "s2"]  # 运行中不终止
    assert [e["state"] for e in queue.snapshot()] == ["running", "running", "queued"]
    for entry in queue._entries:
        if entry.task is not None:
            entry.task.cancel()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_api_screening.py -k "set_max_concurrency or raise_limit or lower_limit" -v`
Expected: FAIL（`AttributeError: 'AnalyzeQueue' object has no attribute 'set_max_concurrency'`）

- [ ] **Step 3: 实现**

```python
# queue.py：默认值改为共享常量
from finance_agent.analysis_concurrency import DEFAULT_MAX_CONCURRENCY
...
    def set_max_concurrency(self, value: int) -> None:
        """运行期调整上限：提高立即补位；降低只约束后续补位（绝不终止运行中）。"""
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"max_concurrency 必须是正整数: {value!r}")
        self._max_concurrency = value
        self._promote()
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_api_screening.py -k "queue" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/screening/queue.py tests/test_api_screening.py
git commit -m "feat(queue): 支持运行期调整并发上限（提高补位/降低不杀运行中）"
```

---

### Task 3: API 端点 + 应用到队列单例

**Files:**
- Modify: `src/finance_agent/api.py`（并发段 + 端点）
- Test: `tests/test_api_analysis_concurrency.py`

**Interfaces:**
- Consumes: `analysis_concurrency.resolve/save`、`AnalyzeQueue.set_max_concurrency`
- Produces: `GET /api/settings/analysis-concurrency`、`PUT /api/settings/analysis-concurrency`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_api_analysis_concurrency.py
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import finance_agent.api as api_module
from finance_agent.api import app
from finance_agent import analysis_concurrency as ac


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(ac.ENV_VAR, raising=False)
    monkeypatch.setenv("ANALYSIS_CONCURRENCY_PATH", str(tmp_path / "analysis.json"))
    monkeypatch.setattr(api_module, "_analyze_queue", None)
    return tmp_path / "analysis.json"


def test_get_default():
    body = TestClient(app).get("/api/settings/analysis-concurrency").json()
    assert body["value"] == 10 and body["source"] == "default"


def test_put_persists_and_applies_to_queue():
    with TestClient(app) as client:
        resp = client.put("/api/settings/analysis-concurrency", json={"value": 7})
        assert resp.status_code == 200
        assert resp.json()["value"] == 7
        assert api_module._get_analyze_queue().max_concurrency == 7
        assert client.get("/api/settings/analysis-concurrency").json()["value"] == 7


@pytest.mark.parametrize("value", [0, 21, "abc", True])
def test_put_rejects_invalid(value):
    with TestClient(app) as client:
        assert client.put("/api/settings/analysis-concurrency", json={"value": value}).status_code == 400
        assert client.get("/api/settings/analysis-concurrency").json()["value"] == 10
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_api_analysis_concurrency.py -v`
Expected: FAIL（404）

- [ ] **Step 3: 实现**

```python
# api.py 导入区
from finance_agent import analysis_concurrency

# api.py 并发段：替换默认值与 _analyze_concurrency
def _analyze_concurrency() -> int:
    """并发上限：设置文件 > env > 默认 10（来源解析见 analysis_concurrency）。"""
    return int(analysis_concurrency.resolve()["value"])

# api.py 端点（放在 screening 队列段之后）
class AnalysisConcurrencyUpdate(BaseModel):
    value: Any = None


@app.get("/api/settings/analysis-concurrency")
async def get_analysis_concurrency() -> dict[str, Any]:
    """批量分析并发设置（只读）：生效值 + 来源 + 合法区间。"""
    return await asyncio.to_thread(analysis_concurrency.resolve)


@app.put("/api/settings/analysis-concurrency")
async def put_analysis_concurrency(req: AnalysisConcurrencyUpdate) -> dict[str, Any]:
    """保存并发设置：校验 1–20 → 原子落盘 → 立即应用到当前队列。"""
    try:
        payload = await asyncio.to_thread(analysis_concurrency.save, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    queue = _get_analyze_queue()
    if queue.max_concurrency != payload["value"]:
        queue.set_max_concurrency(int(payload["value"]))
    return payload
```

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `uv run pytest tests/test_api_analysis_concurrency.py tests/test_api_screening.py -v`
Expected: PASS（screening 用例的默认上限断言需按 Step 5 固定为 2）

- [ ] **Step 5: 固定 screening 测试的上限来源（默认已改 10）**

在 `tests/test_api_screening.py` 的 `_fresh_analyze_queue` fixture 中：

```python
@pytest.fixture(autouse=True)
def _fresh_analyze_queue(monkeypatch, tmp_path):
    """逐用例重置 api 内队列单例；固定上限 2 + 隔离设置文件（默认已改 10）。"""
    import finance_agent.api as api_module

    monkeypatch.setattr(api_module, "_analyze_queue", None)
    monkeypatch.setenv("SCREENING_ANALYZE_CONCURRENCY", "2")
    monkeypatch.setenv("ANALYSIS_CONCURRENCY_PATH", str(tmp_path / "analysis-concurrency.json"))
```

Run: `uv run pytest tests/test_api_screening.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/finance_agent/api.py tests/test_api_analysis_concurrency.py tests/test_api_screening.py
git commit -m "feat(api): 并发设置读写端点 + 保存即应用队列"
```

---

### Task 4: 前端「会话管理」设置项

**Files:**
- Modify: `frontend/src/pages/settings/panes/SessionsPane.tsx`
- Test: `frontend/src/test/settings/sessionsPane.test.tsx`

- [ ] **Step 1: 写失败测试（追加）**

```tsx
// stubFetch 增加并发设置分支
if (url === '/api/settings/analysis-concurrency') {
  const init = calls[calls.length - 1]?.init
  if (init?.method === 'PUT') {
    const value = JSON.parse(String(init.body)).value as number
    return Promise.resolve(new Response(JSON.stringify({
      value, source: 'file', default: 10, min: 1, max: 20, env_value: null, file_status: 'ok',
    })))
  }
  return Promise.resolve(new Response(JSON.stringify({
    value: 10, source: 'default', default: 10, min: 1, max: 20, env_value: null, file_status: 'missing',
  })))
}

it('展示并发设置并可保存', async () => {
  render(<SessionsPane />)
  const input = await screen.findByTestId('sessions-concurrency-input')
  expect(input).toHaveValue(10)
  fireEvent.change(input, { target: { value: '7' } })
  fireEvent.click(screen.getByTestId('sessions-concurrency-save'))
  await screen.findByTestId('sessions-concurrency-saved')
  expect(captureCalls().some(c => c.url === '/api/settings/analysis-concurrency'
    && c.init?.method === 'PUT')).toBe(true)
})

it('越界输入不发起 PUT', async () => {
  render(<SessionsPane />)
  const input = await screen.findByTestId('sessions-concurrency-input')
  fireEvent.change(input, { target: { value: '21' } })
  fireEvent.click(screen.getByTestId('sessions-concurrency-save'))
  expect(await screen.findByTestId('sessions-concurrency-save-error')).toBeInTheDocument()
  expect(captureCalls().some(c => c.init?.method === 'PUT')).toBe(false)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/test/settings/sessionsPane.test.tsx`
Expected: FAIL（找不到 `sessions-concurrency-input`）

- [ ] **Step 3: 实现 SessionsPane（新增状态、加载、保存与渲染区块）**

关键实现（完整接入现有组件）：

```tsx
interface AnalysisConcurrency {
  value: number
  source: 'file' | 'env' | 'default'
  default: number
  min: number
  max: number
  env_value?: number | null
  file_status?: string
}

const SOURCE_LABEL: Record<string, string> = {
  file: '已保存设置', env: '环境变量', default: '默认值',
}

// state
const [concurrency, setConcurrency] = useState<AnalysisConcurrency | null>(null)
const [concurrencyInput, setConcurrencyInput] = useState('')
const [concurrencyStatus, setConcurrencyStatus] = useState<'loading' | 'ready' | 'error'>('loading')
const [concurrencySaving, setConcurrencySaving] = useState(false)
const [concurrencySaved, setConcurrencySaved] = useState(false)
const [concurrencyError, setConcurrencyError] = useState<string | null>(null)

// load / save 与 §计划一致；onChange 清 saved/error
// 渲染：会话总数卡之后，data-testid="sessions-concurrency"
// 输入框 min/max/step、保存按钮、已保存、错误、当前生效+来源披露
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run src/test/settings/sessionsPane.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/settings/panes/SessionsPane.tsx frontend/src/test/settings/sessionsPane.test.tsx
git commit -m "feat(settings-ui): 会话管理分区新增批量分析最大并发数"
```

---

### Task 5: E2E + 运行时隔离

**Files:**
- Create: `tests/e2e/playwright/tests/settings-concurrency.spec.ts`
- Modify: `tests/e2e/playwright/playwright.config.ts`（env 注入）
- Modify: `.gitignore`

- [ ] **Step 1: 写 spec（真实 API，串行）**

```ts
import { expect, test } from '@playwright/test'

function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5173'
  return base.replace('//localhost', '//127.0.0.1')
}

test.describe.configure({ mode: 'serial' })

test('会话管理并发设置：读取默认 10、保存 7、刷新后仍为 7', async ({ page, request }) => {
  // fixture 归一（不是 mock）：把真实设置恢复到 10
  const reset = await request.put(`${apiBase()}/api/settings/analysis-concurrency`, { data: { value: 10 } })
  expect(reset.ok()).toBeTruthy()

  await page.goto('/settings')
  await page.getByTestId('settings-nav-sessions').click()
  const input = page.getByTestId('sessions-concurrency-input')
  await expect(input).toHaveValue('10')

  await input.fill('7')
  await page.getByTestId('sessions-concurrency-save').click()
  await expect(page.getByTestId('sessions-concurrency-saved')).toContainText('已保存')

  await page.reload()
  await page.getByTestId('settings-nav-sessions').click()
  await expect(page.getByTestId('sessions-concurrency-input')).toHaveValue('7')

  // 收尾恢复（不影响其他 spec）
  const restore = await request.put(`${apiBase()}/api/settings/analysis-concurrency`, { data: { value: 10 } })
  expect(restore.ok()).toBeTruthy()
})
```

- [ ] **Step 2: 隔离与忽略**

`playwright.config.ts` 后端 env 增加 `ANALYSIS_CONCURRENCY_PATH: 'data/test-e2e-analysis-concurrency.json'`；`.gitignore` 增加：

```
# 批量分析并发设置（运行时数据 + E2E 隔离文件）
data/settings/
data/test-e2e-analysis-concurrency.json*
```

- [ ] **Step 3: 运行 E2E**

Run: `cd tests/e2e/playwright && npx playwright test tests/settings-concurrency.spec.ts`
Expected: 1 passed

---

### Task 6: 验证报告 + sync + archive

- [ ] 全量相关单测：`uv run pytest tests/test_analysis_concurrency.py tests/test_api_analysis_concurrency.py tests/test_api_screening.py -v`
- [ ] 前端：`cd frontend && npx vitest run src/test/settings/sessionsPane.test.tsx`
- [ ] Lint/类型：`uv run ruff check src/finance_agent/analysis_concurrency.py src/finance_agent/screening/queue.py src/finance_agent/api.py`
- [ ] 验证报告落 `tests/validation/2026-10-21-add-analysis-concurrency-setting-validation.md`
- [ ] tasks.md 全勾 → `openspec validate add-analysis-concurrency-setting --strict`
- [ ] sync 三份 delta 到主规范 → archive → `openspec validate --strict`
