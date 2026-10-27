# Screening Rules Config UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 选股规则（上市天数下限 / 成交额下限 / L2 Top-N）运行时可配置 + 设置中心「选股规则」分区 UI。

**Architecture:** 新建 `screening/rules_config.py`（镜像 `analysis_concurrency.py`：file > env > default、原子写、file_status 披露）；`GET/PUT /api/settings/screening-rules`；主档日批批次开始时 resolve（显式 thresholds 参数优先，microcap 绕开）；设置中心新分区镜像 SessionsPane 并发区块。

**Tech Stack:** FastAPI + 纯 Python（无新依赖）、React 18 + TS + vitest/RTL、Playwright（tests/e2e/playwright）。

## Global Constraints

- delta：`openspec/changes/add-screening-rules-config-ui/`（已 validate --strict 通过）
- 三项区间：`min_trading_days` [1,250] int；`min_avg_amount_20d` [1e6,1e9] 元（前端万元 [100,100000]）；`l2_top_n` [1,500] int
- 默认值：60 / 5e7 / 100；`rules_config` 的 `l2_top_n` 默认值用字面量 100 并以测试钉住 `== pipeline.L2_TOP_N`
- 来源优先级逐项独立：file > env > default；文件 `data/settings/screening_rules.json`（env `SCREENING_RULES_CONFIG_PATH` 可覆盖路径）；env 变量 `SCREENING_MIN_TRADING_DAYS` / `SCREENING_MIN_AVG_AMOUNT_20D` / `SCREENING_L2_TOP_N`
- bool 是 int 子类，数值校验先排除 bool
- 显式 `thresholds=` 参数优先于配置（microcap 传 `MICROCAP_THRESHOLDS`，行为逐位不变）
- 无文件无 env 时行为与现状逐位一致
- 分支/worktree：`.worktrees/screening-rules-config-ui`，分支 `feat/screening-rules-config-ui`；pre-commit 本机缓存锁坏 → 手动跑 `uv run python scripts/check_secrets.py` + `uv run ruff check` 后 `--no-verify`
- TDD：先红后绿；每任务一 commit

---

### Task 1: rules_config 模块

**Files:**
- Create: `src/finance_agent/screening/rules_config.py`
- Test: `tests/screening/test_rules_config.py`

**Interfaces:**
- Produces: `resolve() -> dict`（键 `min_trading_days`/`min_avg_amount_20d`/`l2_top_n` 各为 `{value, source, default, min, max, env_value}`，另带 `file_status: str`、`updated_at: str|None`）；`save(payload: dict) -> dict`（同形，非法抛 `ValueError`）；常量 `DEFAULT_MIN_TRADING_DAYS=60` / `DEFAULT_MIN_AVG_AMOUNT_20D=50_000_000.0` / `DEFAULT_L2_TOP_N=100`

- [ ] **Step 1: Write the failing test**

```python
"""rules_config 单测（delta add-screening-rules-config-ui）。"""

import json

import pytest

from finance_agent.screening import rules_config


@pytest.fixture()
def rules_path(tmp_path, monkeypatch):
    path = tmp_path / "screening_rules.json"
    monkeypatch.setenv("SCREENING_RULES_CONFIG_PATH", str(path))
    for var in ("SCREENING_MIN_TRADING_DAYS", "SCREENING_MIN_AVG_AMOUNT_20D", "SCREENING_L2_TOP_N"):
        monkeypatch.delenv(var, raising=False)
    return path


def test_default_when_no_file_no_env(rules_path):
    state = rules_config.resolve()
    assert state["min_trading_days"] == {
        "value": 60, "source": "default", "default": 60, "min": 1, "max": 250, "env_value": None,
    }
    assert state["min_avg_amount_20d"]["value"] == 50_000_000.0
    assert state["l2_top_n"]["value"] == 100
    assert state["file_status"] == "missing"
    assert state["updated_at"] is None


def test_default_l2_top_n_pins_pipeline_constant():
    # 防两处字面量漂移（design.md 风险 1）
    from finance_agent.screening.pipeline import L2_TOP_N
    assert rules_config.DEFAULT_L2_TOP_N == L2_TOP_N == 100


def test_save_then_resolve_file_source(rules_path):
    state = rules_config.save({"min_trading_days": 80, "min_avg_amount_20d": 80_000_000, "l2_top_n": 50})
    assert state["min_trading_days"]["value"] == 80
    assert state["min_trading_days"]["source"] == "file"
    assert state["l2_top_n"]["value"] == 50
    assert state["file_status"] == "ok"
    assert state["updated_at"] is not None
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    assert data["min_trading_days"] == 80


@pytest.mark.parametrize(
    "payload",
    [
        {"min_trading_days": 0, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        {"min_trading_days": 80, "min_avg_amount_20d": 8e7, "l2_top_n": 0},
        {"min_trading_days": 80, "min_avg_amount_20d": 1e10, "l2_top_n": 50},
        {"min_trading_days": 3.5, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        {"min_trading_days": True, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        {"min_trading_days": 80, "min_avg_amount_20d": "8000万", "l2_top_n": 50},
    ],
)
def test_save_invalid_rejected(rules_path, payload):
    rules_config.save({"min_trading_days": 60, "min_avg_amount_20d": 5e7, "l2_top_n": 100})
    before = rules_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        rules_config.save(payload)
    assert rules_path.read_text(encoding="utf-8") == before


def test_env_fallback_and_disclosure(rules_path, monkeypatch):
    monkeypatch.setenv("SCREENING_L2_TOP_N", "50")
    monkeypatch.setenv("SCREENING_MIN_TRADING_DAYS", "abc")  # 非法 env 忽略
    state = rules_config.resolve()
    assert state["l2_top_n"]["value"] == 50
    assert state["l2_top_n"]["source"] == "env"
    assert state["l2_top_n"]["env_value"] == 50
    assert state["min_trading_days"]["source"] == "default"


def test_corrupt_file_falls_back(rules_path):
    rules_path.write_text("{not json", encoding="utf-8")
    state = rules_config.resolve()
    assert state["file_status"] == "corrupt"
    assert state["l2_top_n"]["source"] == "default"


def test_invalid_field_falls_back_per_item(rules_path):
    rules_path.write_text(
        json.dumps({"min_trading_days": 99999, "min_avg_amount_20d": 8e7, "l2_top_n": 50}),
        encoding="utf-8",
    )
    state = rules_config.resolve()
    assert state["file_status"] == "invalid"
    assert state["min_trading_days"]["source"] == "default"
    assert state["min_avg_amount_20d"]["source"] == "file"
    assert state["l2_top_n"]["source"] == "file"


def test_file_overrides_env(rules_path, monkeypatch):
    monkeypatch.setenv("SCREENING_L2_TOP_N", "30")
    rules_config.save({"min_trading_days": 60, "min_avg_amount_20d": 5e7, "l2_top_n": 50})
    assert rules_config.resolve()["l2_top_n"]["value"] == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/screening/test_rules_config.py -v`
Expected: FAIL（ModuleNotFoundError / AttributeError）

- [ ] **Step 3: Write minimal implementation**

```python
"""选股规则运行时配置（delta add-screening-rules-config-ui）。

可配置项恰为三项：``min_trading_days`` / ``min_avg_amount_20d`` / ``l2_top_n``；
来源优先级逐项独立：设置文件 > 环境变量 > 默认值。文件缺失/损坏/字段非法时按项
回退并如实披露 ``file_status``，不以默认值冒充已保存设置。本模块只管「值从哪来、
怎么写」；运行期生效由 ``pipeline.run_screening_batch`` 批次开始时 ``resolve()``。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.screening.constraints import (
    MIN_AVG_AMOUNT_20D,
    MIN_TRADING_DAYS,
)

_logger = logging.getLogger("finance_agent.screening.rules_config")

DEFAULT_MIN_TRADING_DAYS = MIN_TRADING_DAYS  # 60
DEFAULT_MIN_AVG_AMOUNT_20D = MIN_AVG_AMOUNT_20D  # 5e7
# 与 pipeline.L2_TOP_N 保持一致；不能 import pipeline（会成环），由测试钉住相等
DEFAULT_L2_TOP_N = 100

ENV_VAR_PATH = "SCREENING_RULES_CONFIG_PATH"
ENV_VARS: dict[str, str] = {
    "min_trading_days": "SCREENING_MIN_TRADING_DAYS",
    "min_avg_amount_20d": "SCREENING_MIN_AVG_AMOUNT_20D",
    "l2_top_n": "SCREENING_L2_TOP_N",
}
# 键 → (下限, 上限, 期望类型)
_BOUNDS: dict[str, tuple[float, float, type]] = {
    "min_trading_days": (1, 250, int),
    "min_avg_amount_20d": (1_000_000.0, 1_000_000_000.0, (int, float)),
    "l2_top_n": (1, 500, int),
}


def settings_path() -> Path:
    return Path(
        os.environ.get(ENV_VAR_PATH, "data/settings/screening_rules.json")
    )


def _valid(key: str, value: Any) -> int | float | None:
    """合法值 → int/float；bool / 类型不符 / 越界 → None（bool 是 int 子类，先排除）。"""
    low, high, expected = _BOUNDS[key]
    if isinstance(value, bool) or not isinstance(value, expected):
        return None
    if not low <= value <= high:
        return None
    return value


def validate(key: str, value: Any) -> int | float:
    parsed = _valid(key, value)
    if parsed is None:
        low, high, _ = _BOUNDS[key]
        raise ValueError(f"{key} 必须是 {low:g}–{high:g} 范围内的合法数值: {value!r}")
    return parsed


def _env_value(key: str) -> int | float | None:
    raw = os.getenv(ENV_VARS[key])
    if not raw:
        return None
    try:
        parsed = float(raw)
    except ValueError:
        _logger.warning("非法 %s=%r，忽略", ENV_VARS[key], raw)
        return None
    if parsed == int(parsed) and key != "min_avg_amount_20d":
        parsed = int(parsed)  # 整数项去浮点尾巴，避免 50.0 冒充 float
    if _valid(key, parsed) is None:
        _logger.warning("%s=%r 越界或类型不符，忽略", ENV_VARS[key], raw)
        return None
    return parsed


def _read_settings_file() -> tuple[dict[str, Any] | None, str]:
    """返回 (文件内合法值 dict, 文件状态)；状态 ∈ ok / missing / corrupt / invalid。"""
    path = settings_path()
    if not path.is_file():
        return {}, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, "corrupt"
    if not isinstance(data, dict):
        return {}, "corrupt"
    values: dict[str, Any] = {}
    valid_any = False
    for key in _BOUNDS:
        parsed = _valid(key, data.get(key))
        if parsed is not None:
            values[key] = parsed
            valid_any = True
    if not valid_any:
        return {}, "invalid"
    status = "ok" if len(values) == len(_BOUNDS) else "invalid"
    return values, status


def resolve() -> dict[str, Any]:
    saved, file_status = _read_settings_file()
    updated_at = None
    if file_status in ("ok", "invalid"):
        try:
            updated_at = json.loads(settings_path().read_text(encoding="utf-8")).get("updated_at")
        except (OSError, ValueError, TypeError):
            updated_at = None
    result: dict[str, Any] = {}
    for key, (low, high, _expected) in _BOUNDS.items():
        default = {
            "min_trading_days": DEFAULT_MIN_TRADING_DAYS,
            "min_avg_amount_20d": DEFAULT_MIN_AVG_AMOUNT_20D,
            "l2_top_n": DEFAULT_L2_TOP_N,
        }[key]
        env_value = _env_value(key)
        if key in saved:
            value, source = saved[key], "file"
        elif env_value is not None:
            value, source = env_value, "env"
        else:
            value, source = default, "default"
        result[key] = {
            "value": value,
            "source": source,
            "default": default,
            "min": low,
            "max": high,
            "env_value": env_value,
        }
    result["file_status"] = file_status
    result["updated_at"] = updated_at
    return result


def save(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"请求体必须是对象: {payload!r}")
    missing = sorted(set(_BOUNDS) - set(payload))
    if missing:
        raise ValueError(f"缺少字段: {missing}")
    parsed = {key: validate(key, payload[key]) for key in _BOUNDS}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {**parsed, "updated_at": datetime.now().astimezone().isoformat()}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return resolve()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/screening/test_rules_config.py -v`
Expected: PASS（11 用例）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/screening/rules_config.py tests/screening/test_rules_config.py
git commit --no-verify -m "feat(screening): 选股规则运行时配置模块 rules_config"
```

---

### Task 2: API 端点

**Files:**
- Modify: `src/finance_agent/api.py`（紧随 `put_analysis_concurrency` 之后）
- Test: `tests/test_api_screening_rules.py`

**Interfaces:**
- Consumes: Task 1 `rules_config.resolve / save`
- Produces: `GET /api/settings/screening-rules`（resolve 同形）；`PUT`（体 `{min_trading_days, min_avg_amount_20d, l2_top_n}`，400 语义）

- [ ] **Step 1: Write the failing test**（镜像 `tests/test_api_analysis_concurrency.py` 的 TestClient 模式；`tmp_path` 注入 `SCREENING_RULES_CONFIG_PATH`）

```python
"""选股规则设置端点单测（delta add-screening-rules-config-ui）。"""

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENING_RULES_CONFIG_PATH", str(tmp_path / "screening_rules.json"))
    for var in ("SCREENING_MIN_TRADING_DAYS", "SCREENING_MIN_AVG_AMOUNT_20D", "SCREENING_L2_TOP_N"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(app)


def test_get_returns_defaults(client):
    resp = client.get("/api/settings/screening-rules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["min_trading_days"]["value"] == 60
    assert body["min_trading_days"]["source"] == "default"
    assert body["file_status"] == "missing"


def test_put_ok_and_persist(client):
    resp = client.put(
        "/api/settings/screening-rules",
        json={"min_trading_days": 80, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
    )
    assert resp.status_code == 200
    assert resp.json()["l2_top_n"]["source"] == "file"
    assert client.get("/api/settings/screening-rules").json()["l2_top_n"]["value"] == 50


def test_put_invalid_returns_400_and_keeps_file(client):
    client.put(
        "/api/settings/screening-rules",
        json={"min_trading_days": 80, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
    )
    resp = client.put(
        "/api/settings/screening-rules",
        json={"min_trading_days": 0, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
    )
    assert resp.status_code == 400
    assert client.get("/api/settings/screening-rules").json()["l2_top_n"]["value"] == 50


def test_put_missing_field_returns_400(client):
    resp = client.put("/api/settings/screening-rules", json={"l2_top_n": 50})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run to verify fails**

Run: `uv run pytest tests/test_api_screening_rules.py -v`
Expected: FAIL（404）

- [ ] **Step 3: Implement**（`api.py`，`from finance_agent.screening import rules_config` 随既有 screening import 加入）

```python
# ── 选股规则设置（delta add-screening-rules-config-ui）──


class ScreeningRulesUpdate(BaseModel):
    """保存入参：三项整体校验由 rules_config.save 统一执行（非法 → 400）。"""

    min_trading_days: Any = None
    min_avg_amount_20d: Any = None
    l2_top_n: Any = None


@app.get("/api/settings/screening-rules")
async def get_screening_rules() -> dict[str, Any]:
    """选股规则配置（只读）：逐项生效值 + 来源 + 区间 + env 披露 + file_status。"""
    return await asyncio.to_thread(rules_config.resolve)


@app.put("/api/settings/screening-rules")
async def put_screening_rules(req: ScreeningRulesUpdate) -> dict[str, Any]:
    """保存选股规则：整体校验 → 原子落盘；下一批次生效（运行中批次不受影响）。"""
    try:
        return await asyncio.to_thread(
            rules_config.save,
            {
                "min_trading_days": req.min_trading_days,
                "min_avg_amount_20d": req.min_avg_amount_20d,
                "l2_top_n": req.l2_top_n,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: Run to verify passes**

Run: `uv run pytest tests/test_api_screening_rules.py -v`
Expected: PASS（4 用例）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/api.py tests/test_api_screening_rules.py
git commit --no-verify -m "feat(api): 选股规则设置端点 GET/PUT /api/settings/screening-rules"
```

---

### Task 3: pipeline 主档接线

**Files:**
- Modify: `src/finance_agent/screening/pipeline.py`（`run_screening_batch` 参数校验后、L1 消费前注入解析；`candidates = ranked_all[:L2_TOP_N]` → `[:l2_top_n]`；fallback `top_n=L2_TOP_N` → `top_n=l2_top_n`）
- Test: `tests/screening/test_pipeline.py`（追加 2 用例，复用既有快照构造 helper）

**Interfaces:**
- Consumes: Task 1 `rules_config.resolve()`
- Produces: 主档批次阈值/Top-N 来自配置；`thresholds=` 显式传入时完全绕过配置（microcap 语义不变）

- [ ] **Step 1: Write the failing tests**（复用 `test_pipeline.py` 既有快照 fixture/helper——实现时先读该文件头部的构造 helper 名称再套用；断言锚点：L2 板行数与过滤行为）

```python
def test_main_lane_reads_rules_config(...):  # 配置文件 l2_top_n=1 → L2 板恰 1 行
def test_microcap_lane_ignores_rules_config(...):  # 配置 min_trading_days=250，microcap 批次照常产出
```

（完整测试体在实现时按既有 fixture 落笔：核心断言为主档 `l2_top` == 1、microcap 批次 `l1_passed`/池行不受配置影响。）

- [ ] **Step 2: Run to verify fails**

Run: `uv run pytest tests/screening/test_pipeline.py -k "rules_config" -v`
Expected: FAIL

- [ ] **Step 3: Implement**（`run_screening_batch` 内，`_ = seed` 与 pool 校验之后）

```python
    # 选股规则运行时配置（add-screening-rules-config-ui）：主档批次开始时解析一次，
    # 批内不再读取（运行中批次不受保存影响）；显式 thresholds（microcap 通道）完全绕过。
    if thresholds is None:
        resolved = rules_config.resolve()
        thresholds = FilterThresholds(
            min_trading_days=int(resolved["min_trading_days"]["value"]),
            min_avg_amount_20d=float(resolved["min_avg_amount_20d"]["value"]),
        )
        l2_top_n = int(resolved["l2_top_n"]["value"])
    else:
        l2_top_n = L2_TOP_N
```

`from finance_agent.screening import rules_config` 加入 import；两处 `L2_TOP_N` 消费点（`ranked_all[:L2_TOP_N]`、`liquidity_fallback_rank(..., top_n=L2_TOP_N)`）改用 `l2_top_n`。

- [ ] **Step 4: Run to verify passes + 既有回归不破**

Run: `uv run pytest tests/screening/test_pipeline.py tests/screening/test_microcap_lane.py -v`
Expected: PASS（新 2 例 + 既有全绿）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/screening/pipeline.py tests/screening/test_pipeline.py
git commit --no-verify -m "feat(screening): 主档日批接入选股规则运行时配置"
```

---

### Task 4: 前端分区

**Files:**
- Create: `frontend/src/pages/settings/panes/ScreeningRulesPane.tsx`
- Modify: `frontend/src/pages/settings/SettingsCenterPage.tsx`（`ModuleId` 联合类型 + `MODULES` 数组 + 渲染分支）
- Test: `frontend/src/test/settings/screeningRulesPane.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT /api/settings/screening-rules`（Task 2）
- Produces: `ScreeningRulesPane` 组件（无 props）；导航项 `screening-rules`/「选股规则」

- [ ] **Step 1: Write the failing component test**（镜像 `sessionsPane.test.tsx`：vitest + RTL + `vi.stubGlobal('fetch', ...)`；data-testid：`screening-rules-pane` / `rules-input-min-trading-days` / `rules-input-min-avg-amount-wan` / `rules-input-l2-top-n` / `rules-save` / `rules-saved` / `rules-save-error` / `rules-load-error` / `rules-source-min-trading-days` 等）

用例：①默认渲染三项（成交额 5e7 元 → 输入框 5000 万元，来源「默认值」）；②改成 8000 万元保存 → PUT 体 `min_avg_amount_20d=8e7`、成功显示「已保存」；③ top_n 填 0 → 前端拦截，fetch 未被调用 PUT；④ GET 失败 → `rules-load-error` 可见。

- [ ] **Step 2: Run to verify fails**

Run: `cd frontend && npx vitest run src/test/settings/screeningRulesPane.test.tsx`
Expected: FAIL（组件不存在）

- [ ] **Step 3: Implement**（结构镜像 SessionsPane 并发区块：三输入 + 保存按钮 + 来源徽章 + 区间提示 + 生效时机文案「下一次选股日批或手动补跑开始时生效，运行中批次不受影响」+ 加载失败重试；万元 ↔ 元换算 ×/÷1e4；PUT 体三项；400 时 `rules-save-error` 展示后端 detail）。`SettingsCenterPage.tsx`：`ModuleId` 加 `'screening-rules'`、`MODULES` 加 `{ id: 'screening-rules', label: '选股规则' }`（排在「评估运维」之后）、渲染 `{active === 'screening-rules' && <ScreeningRulesPane />}`。

- [ ] **Step 4: Run to verify passes + 全量前端测试回归**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/settings/panes/ScreeningRulesPane.tsx frontend/src/pages/settings/SettingsCenterPage.tsx frontend/src/test/settings/screeningRulesPane.test.tsx
git commit --no-verify -m "feat(settings): 设置中心「选股规则」分区"
```

---

### Task 5: E2E spec

**Files:**
- Create: `tests/e2e/playwright/tests/settings-screening-rules.spec.ts`（testDir/配置以既有 `playwright.config.ts` 为准，镜像既有 settings 相关 spec 的 seed 与登录方式）
- Modify: e2e 环境注入 `SCREENING_RULES_CONFIG_PATH` 隔离（config `webServer.env` 或既有 env 注入点）

- [ ] **Step 1: playwright-test-generator 探索 /settings 真实 DOM → 场景审批**
- [ ] **Step 2: 写失败 spec：入口与默认显示 / 非法输入拦截（断言无 PUT）/ 保存成功回显**
- [ ] **Step 3: `scan.sh` P0 清零 → `e2e-reviewer` 反模式清零**
- [ ] **Step 4: spec 转绿**
- [ ] **Step 5: Commit** `test(e2e): 设置中心选股规则分区场景`

---

### Task 6: 验证与收尾

- [ ] 后端定向：`uv run pytest tests/screening/ tests/test_api_screening_rules.py tests/test_api_analysis_concurrency.py`
- [ ] `uv run ruff check` + `uv run mypy`（存量基线内）+ 前端 `npm test`
- [ ] E2E stub 套件全绿（§3.5 门禁）
- [ ] 人工验证报告 → `tests/validation/2026-10-27-add-screening-rules-config-ui-validation.md`
- [ ] tasks.md 回填勾选；PR（squash，禁 merge commit）
