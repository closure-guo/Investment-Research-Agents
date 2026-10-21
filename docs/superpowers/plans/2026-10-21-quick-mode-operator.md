# Quick Mode Operator (P1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让快速模式通过 `search_stock` + `start_stock_analysis` 把「分析某只股票」派单到独立 analysis 会话并立即返回回执，同时保持 AG-UI 双轨隔离。

**Architecture:** 快速模式 Agent 增加两个工具；`start_stock_analysis` 复用 `/api/analyze` fast path 同款「建会话 + PipelineRunner.start」装配，不在快速通道内跑管线。能力门禁由前端 probe 的 `tool_call` 事实驱动，`QUICK_OPERATOR_TOOLS` 提供总开关。

**Tech Stack:** Python 3.12 / FastAPI / pytest / React 18 / TypeScript / Vitest / Playwright

## Global Constraints

- 变量命名 camelCase，注释中文（项目规范）。
- 工具名只在 `src/finance_agent/tool_registry.py` 定义；Agent 注册引用常量，不硬编码第二份。
- 深度管线事件 MUST NOT 经 `/api/agui/quick` 传输（`openspec/specs/chat-stream/spec.md` 双轨隔离）。
- 快速模式会话 `session_type=chat`；派单创建的分析会话 `session_type=analysis`。
- 提示词权威源是 `src/finance_agent/prompts/quick_mode.md`，改完必须跑 `uv run python scripts/deploy_prompts.py`。
- 测试先红后绿：每个 Task 先写失败测试，确认失败原因，再写最小实现。
- 单测命令：`uv run pytest <path>::<test> -v`；全量：`uv run pytest`；lint：`uv run ruff check`；类型：`uv run mypy`。

---

### Task 1: 工具注册表与能力门禁

**Files:**
- Modify: `src/finance_agent/tool_registry.py`
- Create: `src/finance_agent/operator/__init__.py`
- Create: `src/finance_agent/operator/gating.py`
- Test: `tests/test_operator_registry.py`
- Test: `tests/test_operator_gating.py`

**Interfaces:**
- Produces: `TOOL_START_STOCK_ANALYSIS: str`；`QUICK_OPERATOR_TOOLS: frozenset[str]`；`READ_TOOLS: frozenset[str]`；`WRITE_TOOLS: frozenset[str]`；`operator_tools_enabled(capability: Mapping[str, Any] | None, *, env_value: str | None = None) -> bool`。

- [ ] **Step 1: Write the failing registry test**

```python
# tests/test_operator_registry.py
"""系统操作工具注册表契约（add-quick-mode-operator）。"""

from finance_agent.tool_registry import (
    AGENT_TOOL_NAMES,
    QUICK_OPERATOR_TOOLS,
    READ_TOOLS,
    TOOL_SEARCH_STOCK,
    TOOL_START_STOCK_ANALYSIS,
    WRITE_TOOLS,
)


def test_quick_operator_tools_are_single_sourced():
    assert QUICK_OPERATOR_TOOLS == frozenset({TOOL_SEARCH_STOCK, TOOL_START_STOCK_ANALYSIS})
    assert QUICK_OPERATOR_TOOLS <= AGENT_TOOL_NAMES


def test_read_and_write_sets_are_disjoint():
    assert not (READ_TOOLS & WRITE_TOOLS)
    assert TOOL_SEARCH_STOCK in READ_TOOLS
    assert TOOL_START_STOCK_ANALYSIS in WRITE_TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_operator_registry.py -v`
Expected: FAIL with `ImportError: cannot import name 'QUICK_OPERATOR_TOOLS'`.

- [ ] **Step 3: Implement the registry additions**

```python
# src/finance_agent/tool_registry.py（在既有常量下追加）
TOOL_START_STOCK_ANALYSIS = "start_stock_analysis"

AGENT_TOOL_NAMES = frozenset(
    {
        TOOL_WEB_SEARCH,
        TOOL_BATCH_WEB_SEARCH,
        TOOL_SEARCH_STOCK,
        TOOL_RUN_DEEP_ANALYSIS,
        TOOL_START_STOCK_ANALYSIS,
    }
)

# 读写分类：写 = 产生会话/管线/订单/批次等副作用。
READ_TOOLS = frozenset({TOOL_WEB_SEARCH, TOOL_BATCH_WEB_SEARCH, TOOL_SEARCH_STOCK})
WRITE_TOOLS = frozenset({TOOL_RUN_DEEP_ANALYSIS, TOOL_START_STOCK_ANALYSIS})

# 快速模式可用的系统操作工具（P1）
QUICK_OPERATOR_TOOLS = frozenset({TOOL_SEARCH_STOCK, TOOL_START_STOCK_ANALYSIS})
```

```python
# src/finance_agent/operator/__init__.py
"""快速模式系统操作工具层（add-quick-mode-operator）。"""
```

- [ ] **Step 4: Write the failing gating test**

```python
# tests/test_operator_gating.py
"""系统操作工具能力门禁（add-quick-mode-operator）。"""

import pytest

from finance_agent.operator.gating import operator_tools_enabled


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (None, True),
        ({}, True),
        ({"tool_call": True}, True),
        ({"tool_call": False}, False),
    ],
)
def test_capability_gate(capability, expected):
    assert operator_tools_enabled(capability) is expected


@pytest.mark.parametrize("env_value", ["0", "false", "off", "no"])
def test_env_switch_disables(env_value):
    assert operator_tools_enabled({"tool_call": True}, env_value=env_value) is False


def test_env_switch_defaults_on(monkeypatch):
    monkeypatch.delenv("QUICK_OPERATOR_TOOLS", raising=False)
    assert operator_tools_enabled(None) is True
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run pytest tests/test_operator_gating.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finance_agent.operator'`.

- [ ] **Step 6: Implement gating**

```python
# src/finance_agent/operator/gating.py
"""系统操作工具的能力门禁。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


def operator_tools_enabled(
    capability: Mapping[str, Any] | None,
    *,
    env_value: str | None = None,
) -> bool:
    """判断是否注册系统操作工具。

    - 环境总开关 ``QUICK_OPERATOR_TOOLS``（默认开）优先；
    - probe 事实 ``tool_call=False`` → 关闭；
    - capability 缺失/None → 放行（probe 未完成不误伤）。
    """
    raw = os.getenv("QUICK_OPERATOR_TOOLS", "1") if env_value is None else env_value
    if str(raw).strip().lower() in {"0", "false", "off", "no"}:
        return False
    if isinstance(capability, Mapping) and capability.get("tool_call") is False:
        return False
    return True
```

- [ ] **Step 7: Run both tests and commit**

Run: `uv run pytest tests/test_operator_registry.py tests/test_operator_gating.py -v`
Expected: PASS.

```bash
git add src/finance_agent/tool_registry.py src/finance_agent/operator/__init__.py src/finance_agent/operator/gating.py tests/test_operator_registry.py tests/test_operator_gating.py
git commit -m "feat(operator): add tool registry entries and capability gate"
```

---

### Task 2: 共享派单服务（fast path 复用）

**Files:**
- Modify: `src/finance_agent/api.py:2345-2445`（`analyze` fast path 与新增 helper）
- Test: `tests/test_api_operator_dispatch.py`

**Interfaces:**
- Produces: `async def start_analysis_session_for_operator(stock_code: str, stock_name: str = "", *, api_key: str | None = None, llm_config: LLMConfig | None = None) -> dict[str, Any]`，返回 `{session_id, display_name, status, symbol}`。
- Produces: `async def _start_fast_path_pipeline(*, session_id, stock_code, stock_name, req, analysis_id, start_time, display_name, llm_config) -> None`。
- Consumes: `session_store.create_session/append_chat/set_pipeline_anchor/update_session_for_clarify`、`PipelineRunner.start`、`_run_graph_streaming`、`build_layer_tree`。

- [ ] **Step 1: Write the failing dispatch test**

```python
# tests/test_api_operator_dispatch.py
"""operator 派单服务：建 analysis 会话并启动管线，返回回执。"""

from __future__ import annotations

import pytest

import finance_agent.api as api_mod
from finance_agent import session_store


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    return tmp_path / "t.db"


@pytest.mark.asyncio
async def test_dispatch_creates_analysis_session_and_starts_pipeline(isolated_db, monkeypatch):
    started: list[str] = []

    def fake_start(cls, session_id, event_source, initial_snapshot, loop=None):
        started.append(session_id)

    monkeypatch.setattr(api_mod.PipelineRunner, "start", classmethod(fake_start))

    receipt = await api_mod.start_analysis_session_for_operator("600519", "贵州茅台")

    assert receipt["symbol"] == "600519"
    assert receipt["status"] == "running"
    assert receipt["display_name"] == "贵州茅台 深度分析"
    assert started == [receipt["session_id"]]

    session = session_store.get_session(receipt["session_id"])
    assert session["session_type"] == "analysis"
    assert session["status"] == "running"
    assert session["stock_code"] == "600519"
    assert session["chat_history"][0]["role"] == "user"
    assert "600519" in session["chat_history"][0]["content"]


@pytest.mark.asyncio
async def test_dispatch_propagates_llm_config(isolated_db, monkeypatch):
    captured: dict = {}

    def fake_start(cls, session_id, event_source, initial_snapshot, loop=None):
        # event_source 是 lambda；直接调用会真跑管线，这里只记录容器
        captured["session_id"] = session_id

    monkeypatch.setattr(api_mod.PipelineRunner, "start", classmethod(fake_start))
    cfg = api_mod.LLMConfig(model="openai/gpt-4o", apiKey="sk-test")

    receipt = await api_mod.start_analysis_session_for_operator(
        "600519", "贵州茅台", api_key="sk-test", llm_config=cfg
    )

    assert captured["session_id"] == receipt["session_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_operator_dispatch.py -v`
Expected: FAIL with `AttributeError: module 'finance_agent.api' has no attribute 'start_analysis_session_for_operator'`.

- [ ] **Step 3: Extract the shared fast-path helper**

在 `api.py` 的 `analyze` 端点之前插入（`_run_graph_streaming` 之后）：

```python
async def _start_fast_path_pipeline(
    *,
    session_id: str,
    stock_code: str,
    stock_name: str,
    req: AnalyzeRequest,
    analysis_id: str,
    start_time: float,
    display_name: str | None,
    llm_config: LLMConfig | None,
) -> None:
    """fast path 与 operator 工具共用的管线启动装配。

    只负责建锚点/置状态/写 session_created/启动 PipelineRunner；事件经 journal
    桥接，SSE 侧零改动。
    """
    await asyncio.to_thread(append_chat, session_id, "user", req.query)
    await asyncio.to_thread(set_pipeline_anchor, session_id)
    await asyncio.to_thread(
        update_session_for_clarify,
        session_id,
        stock_code=stock_code,
        stock_name=stock_name,
        status="running",
    )
    append_session_event(
        session_id,
        {
            "type": "session_created",
            "session_id": session_id,
            "display_name": display_name,
            "timestamp": _now(),
        },
    )
    loop = asyncio.get_running_loop()
    PipelineRunner.start(
        session_id,
        lambda: _run_graph_streaming(
            stock_code,
            stock_name,
            req,
            analysis_id,
            start_time,
            session_id=session_id,
            llm_config=llm_config,
        ),
        {
            "layerTree": build_layer_tree(),
            "currentNodeId": "",
            "progress": 0.0,
            "updatedAt": int(time.time() * 1000),
            "pipeline_start_ts": int(start_time * 1000),
        },
        loop=loop,
    )


async def start_analysis_session_for_operator(
    stock_code: str,
    stock_name: str = "",
    *,
    api_key: str | None = None,
    llm_config: LLMConfig | None = None,
) -> dict[str, Any]:
    """operator 派单：创建独立 analysis 会话并启动管线，返回回执。

    与 /api/analyze fast path 共用 _start_fast_path_pipeline；不在调用方
    事件流内运行管线。
    """
    symbol = (stock_code or "").strip()
    name = (stock_name or "").strip()
    display_name = f"{name or symbol} 深度分析"
    session_id = await asyncio.to_thread(
        create_session,
        stock_code="",
        stock_name="",
        display_name=display_name,
        status="clarifying",
        session_type="analysis",
    )
    analysis_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    req = AnalyzeRequest(
        query=f"分析 {name or symbol}（{symbol}）",
        stock_code=symbol,
        stock_name=name or None,
        api_key=api_key,
    )
    await _start_fast_path_pipeline(
        session_id=session_id,
        stock_code=symbol,
        stock_name=name,
        req=req,
        analysis_id=analysis_id,
        start_time=start_time,
        display_name=display_name,
        llm_config=llm_config,
    )
    return {
        "session_id": session_id,
        "display_name": display_name,
        "status": "running",
        "symbol": symbol,
    }
```

- [ ] **Step 4: Refactor `analyze` fast path to call the helper**

把 `if stock_code and not req.session_id:` 内从 `await asyncio.to_thread(append_chat, ...)` 到 `PipelineRunner.start(...)` 的整段替换为：

```python
        await _start_fast_path_pipeline(
            session_id=session_id,
            stock_code=stock_code,
            stock_name=stock_name,
            req=req,
            analysis_id=analysis_id,
            start_time=start_time,
            display_name=display_name,
            llm_config=llm_config,
        )
```

保留其后的 `_enrich_done` 与 `return StreamingResponse(...)`。

- [ ] **Step 5: Run the new test plus the fast-path regression suite**

Run: `uv run pytest tests/test_api_operator_dispatch.py tests/test_api_pipeline_resume.py tests/test_pipeline_anchor.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finance_agent/api.py tests/test_api_operator_dispatch.py
git commit -m "refactor(api): share fast-path pipeline dispatch with operator"
```

---

### Task 3: `start_stock_analysis` 工具

**Files:**
- Create: `src/finance_agent/operator/analysis.py`
- Test: `tests/test_operator_analysis_tool.py`

**Interfaces:**
- Produces: `make_start_stock_analysis(*, api_key: str | None = None, llm_config: LLMConfig | None = None, dispatcher: Dispatcher | None = None) -> Callable[[str, str], Awaitable[str]]`。
- Dispatcher 签名：`async def dispatcher(symbol: str, name: str, api_key: str | None, llm_config: LLMConfig | None) -> dict[str, Any]`。
- 成功回执 JSON：`{"ok": true, "session_id", "display_name", "status", "symbol"}`；失败：`{"ok": false, "error"}`。

- [ ] **Step 1: Write the failing tool tests**

```python
# tests/test_operator_analysis_tool.py
"""start_stock_analysis 工具：派单、回执、幂等、失败披露。"""

from __future__ import annotations

import json

import pytest

from finance_agent.operator.analysis import make_start_stock_analysis


@pytest.mark.asyncio
async def test_rejects_non_six_digit_code():
    calls: list[tuple] = []

    async def dispatcher(symbol, name, api_key, llm_config):
        calls.append((symbol, name))
        return {}

    tool = make_start_stock_analysis(dispatcher=dispatcher)
    out = json.loads(await tool("茅台", "贵州茅台"))

    assert out["ok"] is False
    assert "6 位" in out["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_dispatches_and_returns_receipt():
    calls: list[tuple] = []

    async def dispatcher(symbol, name, api_key, llm_config):
        calls.append((symbol, name, api_key, llm_config))
        return {
            "session_id": "sid-1",
            "display_name": "贵州茅台 深度分析",
            "status": "running",
            "symbol": symbol,
        }

    tool = make_start_stock_analysis(api_key="sk-test", dispatcher=dispatcher)
    out = json.loads(await tool("600519", "贵州茅台"))

    assert out == {
        "ok": True,
        "session_id": "sid-1",
        "display_name": "贵州茅台 深度分析",
        "status": "running",
        "symbol": "600519",
    }
    assert calls[0][:2] == ("600519", "贵州茅台")


@pytest.mark.asyncio
async def test_duplicate_dispatch_returns_first_receipt():
    calls: list[str] = []

    async def dispatcher(symbol, name, api_key, llm_config):
        calls.append(symbol)
        return {
            "session_id": f"sid-{len(calls)}",
            "display_name": "x",
            "status": "running",
            "symbol": symbol,
        }

    tool = make_start_stock_analysis(dispatcher=dispatcher)
    first = await tool("600519")
    second = await tool("600519")

    assert first == second
    assert calls == ["600519"]


@pytest.mark.asyncio
async def test_dispatch_failure_has_no_session_id():
    async def dispatcher(symbol, name, api_key, llm_config):
        raise RuntimeError("db down")

    tool = make_start_stock_analysis(dispatcher=dispatcher)
    out = json.loads(await tool("600519"))

    assert out["ok"] is False
    assert "db down" in out["error"]
    assert "session_id" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_operator_analysis_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finance_agent.operator.analysis'`.

- [ ] **Step 3: Implement the tool**

```python
# src/finance_agent/operator/analysis.py
"""快速模式分析派单工具（P1）。"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from finance_agent.llm.config import LLMConfig

_logger = logging.getLogger("finance_agent.operator.analysis")

_SYMBOL_RE = re.compile(r"^\d{6}$")

Dispatcher = Callable[[str, str, str | None, LLMConfig | None], Awaitable[dict[str, Any]]]


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _receipt(receipt: dict[str, Any]) -> str:
    return json.dumps(
        {
            "ok": True,
            "session_id": receipt["session_id"],
            "display_name": receipt["display_name"],
            "status": receipt["status"],
            "symbol": receipt["symbol"],
        },
        ensure_ascii=False,
    )


def make_start_stock_analysis(
    *,
    api_key: str | None = None,
    llm_config: LLMConfig | None = None,
    dispatcher: Dispatcher | None = None,
):
    """创建 ``start_stock_analysis`` 工具（派单到独立 analysis 会话）。

    dispatcher 注入点用于测试与解耦；生产缺省从 api 模块函数内导入共享派单服务。
    """
    dispatched: dict[str, str] = {}

    async def start_stock_analysis(stock_code: str, stock_name: str = "") -> str:
        """启动一只 A 股的深度分析（派单到独立分析会话，立即返回回执）

        只负责派单，不等待分析完成。调用前须先用 search_stock 确认 6 位代码；
        用户尚未确认标的或存在多个候选时不要调用本工具。

        Args:
            stock_code: 6 位 A 股代码，如 "600519"
            stock_name: 股票名称，如 "贵州茅台"
        """
        symbol = (stock_code or "").strip()
        if not _SYMBOL_RE.fullmatch(symbol):
            return _error(
                f"股票代码必须是 6 位数字，收到 {stock_code!r}；请先调用 search_stock 确认标的"
            )
        cached = dispatched.get(symbol)
        if cached is not None:
            return cached

        try:
            if dispatcher is None:
                from finance_agent.api import start_analysis_session_for_operator

                receipt = await start_analysis_session_for_operator(
                    symbol,
                    (stock_name or "").strip(),
                    api_key=api_key,
                    llm_config=llm_config,
                )
            else:
                receipt = await dispatcher(
                    symbol, (stock_name or "").strip(), api_key, llm_config
                )
        except Exception as exc:  # noqa: BLE001 -- 工具边界必须把失败转成可读结果
            _logger.exception("operator 派单失败 symbol=%s", symbol)
            return _error(f"派单失败：{type(exc).__name__}: {exc}")

        payload = _receipt(receipt)
        dispatched[symbol] = payload
        _logger.info(
            "operator_dispatch session_id=%s symbol=%s status=%s",
            receipt.get("session_id"),
            symbol,
            receipt.get("status"),
        )
        return payload

    return start_stock_analysis
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_operator_analysis_tool.py -v`
Expected: PASS.

```bash
git add src/finance_agent/operator/analysis.py tests/test_operator_analysis_tool.py
git commit -m "feat(operator): add start_stock_analysis dispatch tool"
```

---

### Task 4: 快速模式注册、capability 透传与提示词

**Files:**
- Modify: `src/finance_agent/agent_factory.py:1655-1698`
- Modify: `src/finance_agent/agui/endpoint.py:469-490`
- Modify: `frontend/src/llmConfig.ts:98-211`
- Modify: `src/finance_agent/prompts/quick_mode.md`
- Modify: `tests/test_agent_factory.py:14-35`
- Create: `tests/test_quick_prompt_contract.py`
- Modify: `tests/agui/test_endpoint.py`（追加 capability 门禁用例）

**Interfaces:**
- `build_agent(..., operator_enabled: bool | None = None)`；`None` 时从 `operator_tools_enabled(None)` 读环境开关。
- `LLMConfigPayload` 新增 `capability?: CapabilityMatrix | null`；`buildLlmConfigPayload` 在存在时携带。
- AG-UI 端点从 `forwardedProps.llmConfig.capability` 读取 `tool_call`。

- [ ] **Step 1: Write the failing Agent registration tests**

```python
# tests/test_agent_factory_quick_operator.py
"""快速模式系统操作工具注册（add-quick-mode-operator）。"""

from finance_agent.agent_factory import build_agent


def test_quick_mode_registers_operator_tools_by_default():
    agent = build_agent(mode="quick", api_key="test-key")
    names = set(agent.tools.get_tool_names())
    assert {"search_stock", "start_stock_analysis", "web_search"} <= names
    assert agent.max_iterations == 6


def test_quick_mode_can_disable_operator_tools():
    agent = build_agent(mode="quick", api_key="test-key", operator_enabled=False)
    names = set(agent.tools.get_tool_names())
    assert names == {"web_search"}


def test_quick_mode_env_switch_disables_operator_tools(monkeypatch):
    monkeypatch.setenv("QUICK_OPERATOR_TOOLS", "0")
    agent = build_agent(mode="quick", api_key="test-key")
    assert set(agent.tools.get_tool_names()) == {"web_search"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_factory_quick_operator.py -v`
Expected: FAIL — `start_stock_analysis` missing and `max_iterations == 3`.

- [ ] **Step 3: Update `build_agent`**

在 `build_agent` 签名中加入 `operator_enabled: bool | None = None`，并在 quick 分支：

```python
    if mode == "quick":
        from finance_agent.operator.gating import operator_tools_enabled

        operatorEnabled = (
            operator_tools_enabled(None) if operator_enabled is None else operator_enabled
        )
        operatorNote = (
            ""
            if operatorEnabled
            else "当前模型不支持工具调用，系统操作不可用。请提示用户切换支持工具调用的模型。"
        )
        prompt = (
            _mode_pinfo.template.format(now=_now(), operator_note=operatorNote)
            if _mode_pinfo
            else ""
        )
        agent = Agent(
            model=model,
            api_key=api_key,
            system_prompt=prompt,
            permission_mode=PermissionMode.YOLO,
            max_iterations=6,
            llm=llm_client,
            context_budget=context_budget,
        )
        from finance_agent.api import TESTING

        agent.tools.register(
            _trace_tool(TOOL_WEB_SEARCH)(_stub_web_search if TESTING else _web_search),
            name=TOOL_WEB_SEARCH,
        )
        if operatorEnabled:
            from finance_agent.operator.analysis import make_start_stock_analysis

            agent.tools.register(
                _trace_tool(TOOL_SEARCH_STOCK)(_make_search_stock(api_key)),
                name=TOOL_SEARCH_STOCK,
            )
            agent.tools.register(
                _trace_tool(TOOL_START_STOCK_ANALYSIS)(
                    make_start_stock_analysis(api_key=api_key, llm_config=llm_config)
                ),
                name=TOOL_START_STOCK_ANALYSIS,
            )
        session_id = kwargs.get("session_id")
        if session_id:
            _inject_chat_history(agent, session_id)
        return agent
```

顶部 import 增加 `TOOL_START_STOCK_ANALYSIS`。

- [ ] **Step 4: Update the old max_iterations test**

```python
# tests/test_agent_factory.py
def test_quick_mode_max_iterations_is_6():
    """快速模式 max_iterations=6（容纳解析→派单→复述回执）。"""
    agent = build_agent(mode="quick", api_key="test-key")
    assert agent.max_iterations == 6
```

- [ ] **Step 5: Write the failing prompt contract test**

```python
# tests/test_quick_prompt_contract.py
"""quick_mode 提示词契约（add-quick-mode-operator）。"""

from pathlib import Path


def test_quick_prompt_has_operator_dispatch_policy():
    text = Path("src/finance_agent/prompts/quick_mode.md").read_text(encoding="utf-8")

    assert "search_stock" in text
    assert "start_stock_analysis" in text
    assert "{operator_note}" in text
    assert "不得" in text
    assert "已完成" in text
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_quick_prompt_contract.py -v`
Expected: FAIL — prompt lacks the new tool names/placeholder.

- [ ] **Step 7: Update `quick_mode.md`**

在 `# Workflow` 段后追加：

```markdown
# 系统操作（工具）

## 股票深度分析
当用户明确要求分析某只 A 股、或对比两只及以上明确标的时：
1. 先用 `search_stock` 解析标的。唯一结果 → 继续；多个候选 → 只列出候选并请用户确认，禁止直接派单。
2. 确认唯一 6 位代码后，调用 `start_stock_analysis(stock_code, stock_name)` 派单。
3. 派单成功后，只复述回执：会话名、任务号（session_id）、状态，以及「在左侧会话列表点进去查看进度」。
4. 禁止声称分析已完成、禁止编造报告内容或进度；深度分析由独立会话执行，不在当前对话内完成。
5. 同一次请求只派一单；用户需要分析多只时，请其逐只确认。

## 能力不可用
{operator_note}
```

在 `# Tool Policy` 段替换为：

```markdown
# Tool Policy
- web_search: 获取实时信息（天气、行情、新闻、政策等）。简单知识性问题不需要搜索。
- search_stock: 解析 A 股名称/代码，派单前必须先确认唯一标的。
- start_stock_analysis: 把已确认标的派单到独立分析会话，立即返回回执；不等待分析完成。
```

- [ ] **Step 8: Write the failing capability passthrough test**

在 `tests/agui/test_endpoint.py` 追加：

```python
def test_capability_tool_call_false_disables_operator_tools(isolated_db, monkeypatch):
    """probe 事实 tool_call=false → build_agent 收到 operator_enabled=False。"""
    sid = session_store.create_chat_session("能力门禁")
    captured: dict = {}

    class _Stub:
        async def run(self, user_input: str, force_tool: bool = False):
            yield StreamEvent.answer("ok")

    def fake_build_agent(**kwargs):
        captured.update(kwargs)
        return _Stub()

    monkeypatch.setattr(agent_factory, "build_agent", fake_build_agent)

    with TestClient(app) as client:
        _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-cap",
                "messages": [{"id": "m1", "role": "user", "content": "分析茅台"}],
                "forwardedProps": {
                    "llmConfig": {"capability": {"tool_call": False, "stream": True}}
                },
            },
        )

    assert captured["operator_enabled"] is False
```

- [ ] **Step 9: Run test to verify it fails**

Run: `uv run pytest tests/agui/test_endpoint.py::test_capability_tool_call_false_disables_operator_tools -v`
Expected: FAIL — `operator_enabled` 未传入或为 True。

- [ ] **Step 10: Implement endpoint capability passthrough**

在 `endpoint.py` 的 forwardedProps 解析块：

```python
    capability: dict[str, Any] | None = None
    if isinstance(req.forwarded_props, dict):
        ...
        raw_cfg = req.forwarded_props.get("llmConfig")
        if isinstance(raw_cfg, dict):
            raw_cap = raw_cfg.get("capability")
            if isinstance(raw_cap, dict):
                capability = raw_cap
            ...
    from finance_agent.operator.gating import operator_tools_enabled

    agent = build_agent(
        mode="quick",
        api_key=api_key,
        session_id=thread_id,
        llm_config=llm_config,
        operator_enabled=operator_tools_enabled(capability),
    )
```

- [ ] **Step 11: Frontend capability payload**

```ts
// frontend/src/llmConfig.ts
export interface LLMConfigPayload {
  ...
  /** probe 能力矩阵：仅供后端决定是否注册系统操作工具，不进入 LLMConfig */
  capability?: CapabilityMatrix | null
}
```

```ts
// buildLlmConfigPayload 内、return 前：
  if (cfg.capability) payload.capability = cfg.capability
```

- [ ] **Step 12: Run backend + frontend tests**

Run:
```bash
uv run pytest tests/test_agent_factory_quick_operator.py tests/test_quick_prompt_contract.py tests/test_agent_factory.py tests/agui/test_endpoint.py -v
cd frontend && npm test -- --run src/test/llmConfig.test.ts
```
Expected: PASS.

- [ ] **Step 13: Deploy the prompt**

Run: `uv run python scripts/deploy_prompts.py`
Expected: quick_mode 发布成功；若 Langfuse 未配置/凭证缺失，记录实际输出并在验证报告中标注未发布。

- [ ] **Step 14: Commit**

```bash
git add src/finance_agent/agent_factory.py src/finance_agent/agui/endpoint.py frontend/src/llmConfig.ts src/finance_agent/prompts/quick_mode.md tests/test_agent_factory_quick_operator.py tests/test_quick_prompt_contract.py tests/test_agent_factory.py tests/agui/test_endpoint.py
git commit -m "feat(quick): register operator tools and gate by tool_call capability"
```

---

### Task 5: 集成验证与 E2E

**Files:**
- Create: `tests/e2e/playwright/tests/quick-operator.spec.ts`
- Create: `tests/validation/2026-10-21-add-quick-mode-operator-validation.md`

**Interfaces:**
- Consumes: 前四个 Task 的工具与门禁。
- 验证：快速模式输入「分析茅台」→ 出现 `start_stock_analysis` 工具横幅 + 回执文本；左侧出现 `茅台 深度分析` 或 `600519 深度分析` 会话；快速会话不出现管线节点事件。

- [ ] **Step 1: Write the E2E spec**

```ts
// tests/e2e/playwright/tests/quick-operator.spec.ts
import { test, expect } from '@playwright/test'

test.describe('快速模式系统操作（add-quick-mode-operator）', () => {
  test('分析茅台派单到独立分析会话', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: /快速模式/ }).click()
    await page.getByPlaceholder(/输入问题/).fill('分析茅台')
    await page.getByPlaceholder(/输入问题/).press('Enter')

    await expect(page.getByText(/调用工具 · start_stock_analysis/)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/session_id|任务号/)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/深度分析/).first()).toBeVisible({ timeout: 30_000 })
  })
})
```

- [ ] **Step 2: Run E2E（专用端口，避免打到生产）**

Run:
```bash
cd tests/e2e/playwright && npx playwright test quick-operator.spec.ts
```
Expected: PASS。若环境未启动或数据源不可用，记录实际输出，不伪造通过。

- [ ] **Step 3: Run full verification**

Run:
```bash
uv run pytest tests/test_operator_registry.py tests/test_operator_gating.py tests/test_api_operator_dispatch.py tests/test_operator_analysis_tool.py tests/test_agent_factory_quick_operator.py tests/test_quick_prompt_contract.py tests/test_agent_factory.py tests/agui/test_endpoint.py tests/test_api_pipeline_resume.py -v
uv run ruff check src/finance_agent/operator src/finance_agent/tool_registry.py src/finance_agent/agent_factory.py src/finance_agent/api.py
uv run mypy src/finance_agent/operator src/finance_agent/tool_registry.py
```
Expected: 全绿；记录失败项。

- [ ] **Step 4: Write the validation report**

```markdown
# 人工验证报告: add-quick-mode-operator

**日期**: 2026-10-21
**关联 delta**: openspec/changes/add-quick-mode-operator/
**E2E**: [playwright-report 路径 / 未跑原因]

## 验证结果

| 场景 | E2E 已覆盖 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| 快速模式「分析茅台」派单 | 是 | 工具横幅 + 回执 + 新 analysis 会话 | 待填 | 待填 |
| 快速通道不出现管线节点 | 是 | 无 node_start/node_complete | 待填 | 待填 |
| tool_call=false 披露 | 否 | 不派单、提示不可用 | 待填 | 待填 |

## 结论

[ ] 全部通过，可 archive
[ ] 存在失败项，需修复后重新验证
```

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/playwright/tests/quick-operator.spec.ts tests/validation/2026-10-21-add-quick-mode-operator-validation.md
git commit -m "test(quick): add operator dispatch E2E and validation report"
```

---

## Self-Review

**Spec coverage:**

- 工具注册单一来源 → Task 1。
- 派单独立会话、立即回执、llm_config 透传、失败披露 → Task 2 + Task 3。
- 模糊/多候选不派单 → Task 4 prompt + Task 3 代码校验。
- 双轨隔离 → Task 4 端点不改 AG-UI 管线传输 + Task 5 E2E。
- tool_call=false 披露 → Task 1 gating + Task 4 endpoint/prompt。
- 可观测与幂等 → Task 3 日志 + 幂等表。

**Placeholder scan:** 无 TBD/TODO；未决项在 design.md 的 Open Questions 中显式标注。

**Type consistency:** `make_start_stock_analysis` 的 dispatcher 签名在 Task 3/4 一致；`build_agent(operator_enabled=...)` 在 Task 4 两处一致；`start_analysis_session_for_operator` 的 `api_key` 关键字参数在 Task 2/3 一致。
