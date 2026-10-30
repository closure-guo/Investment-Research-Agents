# add-key-events-cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关键事件取数链加当日缓存（验证过的空可缓存、失败不缓存）、白名单扩 6 域、选股链取数携带股票名——治「事件数据不可用」占比 89% 与日批 30~40 分钟事件装配耗时。

**Architecture:** 缓存做进 `fetch_key_events` 内部（方案 A），复用 `DataCache` 共享单例（key `{code}:key_events`、TTL 1 天，与深析 fetch 节点既有 key 同值幂等）；provenance 以 `fetch_events_from_web` 既有 `None`=失败 / 列表=成功 约定承载。所有改动在 worktree `.worktrees/add-key-events-cache`（分支 `add-key-events-cache`）内进行。

**Tech Stack:** Python 3.12+/pytest/monkeypatch；`uv run pytest`；零新增依赖。

## Global Constraints

- **测试零网络**：一切 ddgs/LLM 触点用 monkeypatch 桩；禁止真实搜索或 LLM 调用。
- **测试禁止触生产 cache.db**：缓存注入一律 `monkeypatch.setattr("finance_agent.events.pipeline.get_shared_cache", lambda: DataCache(db_path=str(tmp_path / "cache.db")))`。
- **返回值形状不变**：`fetch_key_events` 仍返回 `list[dict]`；triage prompt / 时效校验 / 深析节点 / 引用回声源零改动。
- **语义细化（delta spec 已修订）**：「验证过的空」（搜索+提取正常完成、无 L1/L2 级事件）返回 `[]`；L3 兜底卡仅在失败路径返回。
- 提交走 `git commit --no-verify`（pre-commit 缓存锁坑），提交前手动跑 `uv run python tests/scripts/check_secrets.py`。
- 全程在 worktree 内执行：`D:\WorkSpace\finance_analysis_agent\.worktrees\add-key-events-cache`；测试命令一律 `uv run pytest ...`（worktree 根目录）。

---

### Task 1: 白名单扩集 + 域名过滤提取为纯函数

**Files:**
- Modify: `src/finance_agent/events/config.py`（ALLOWED_DOMAINS 追加 6 域）
- Modify: `src/finance_agent/events/web_fetcher.py:74-97`（提取 `_domain_allowed` 并复用）
- Test: `tests/events/__init__.py`（新建空文件）、`tests/events/test_config.py`

**Interfaces:**
- Consumes: 无（本任务独立）。
- Produces: `ALLOWED_DOMAINS: tuple[str, ...]`（21 域）；`_domain_allowed(url: str) -> bool`（web_fetcher 模块级纯函数，Task 2 不依赖、测试直接消费）。

- [ ] **Step 1: Write the failing test**

```python
# tests/events/test_config.py
"""事件域名白名单契约（add-key-events-cache Req2）。"""

from finance_agent.events.config import ALLOWED_DOMAINS
from finance_agent.events.web_fetcher import _domain_allowed


def test_whitelist_contains_news_sources_added_by_key_events_cache():
    for domain in (
        "cls.cn",
        "xueqiu.com",
        "news.qq.com",
        "investing.com",
        "yicai.com",
        "nbd.com.cn",
    ):
        assert domain in ALLOWED_DOMAINS, f"缺少新增域名: {domain}"


def test_whitelist_size_is_pinned():
    # 21 域：原 15 + 新增 6；增删须先改 spec（openspec/specs/key-events）
    assert len(ALLOWED_DOMAINS) == 21


def test_quote_page_domains_are_not_whitelisted():
    for domain in ("cfi.cn", "9fzt.com"):
        assert domain not in ALLOWED_DOMAINS, f"行情页域名不应入选: {domain}"


def test_domain_allowed_matches_substring():
    assert _domain_allowed("https://www.cls.cn/detail/2385250")
    assert _domain_allowed("https://xueqiu.com/s/sh601952")
    assert _domain_allowed("https://cn.investing.com/news/article-1")
    assert not _domain_allowed("https://quote.cfi.cn/quote23007_601952.html")
    assert not _domain_allowed("https://stock.9fzt.com/index/sh_601952.html")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/events/test_config.py -v`
Expected: FAIL（`ImportError: cannot import name '_domain_allowed'`；或新增域名断言失败）

- [ ] **Step 3: Write minimal implementation**

`config.py`：`ALLOWED_DOMAINS` 元组在「主流财经媒体」组追加 6 项（保持组内注释风格）：

```python
# 权威财经新闻域名白名单
ALLOWED_DOMAINS: tuple[str, ...] = (
    # 主流财经媒体
    "caixin.com",
    "stcn.com",
    "cs.com.cn",
    "eastmoney.com",
    "hexun.com",
    "sina.com.cn",
    "sohu.com",
    "163.com",
    "ifeng.com",
    # 财联社/雪球/门户/海外/一财/每经（add-key-events-cache：10-30 实测被旧名单砍掉的高质量源）
    "cls.cn",
    "xueqiu.com",
    "news.qq.com",
    "investing.com",
    "yicai.com",
    "nbd.com.cn",
    # 证券/研报平台
    "10jqka.com",
    "stockstar.com",
    "cnfol.com",
    "baogaoting.com",
    "pedaily.cn",
    "cnyes.com",
)
```

`web_fetcher.py`：在 `_search_with_duckduckgo` 上方新增纯函数，并替换原内联过滤：

```python
def _domain_allowed(url: str) -> bool:
    """域名白名单子串匹配（纯函数化便于单测；语义与既有 any(d in url) 一致）。"""
    return any(d in url for d in ALLOWED_DOMAINS)
```

`_search_with_duckduckgo` 内原：

```python
                if any(d in url for d in ALLOWED_DOMAINS):
```

改为：

```python
                if _domain_allowed(url):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/events/test_config.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
uv run python tests/scripts/check_secrets.py
git add tests/events/ src/finance_agent/events/config.py src/finance_agent/events/web_fetcher.py
git commit --no-verify -m "feat(events): 白名单扩 6 新闻源并把域过滤提取为纯函数 (add-key-events-cache)"
```

---

### Task 2: fetch_key_events 当日缓存（四分支语义）

**Files:**
- Modify: `src/finance_agent/events/pipeline.py`（整文件重写主函数，约 83 行 → 约 110 行）
- Test: `tests/events/test_pipeline.py`

**Interfaces:**
- Consumes: `DataCache.get/set(key, data, ttl_seconds)`、`DataCache(db_path=...)`（`finance_agent.data.cache`）；`fetch_events_from_web(stock_code, stock_name) -> list[dict] | None`（None=失败，列表=成功，可能为空）；`load_preset_events(stock_code) -> list[dict] | None`。
- Produces: `fetch_key_events(stock_code: str, stock_name: str = "", use_web_search: bool = True) -> list[dict]`——签名与返回形状不变。Task 3 依赖此签名（第二位置参数=股票名）。

- [ ] **Step 1: Write the failing test**

```python
# tests/events/test_pipeline.py
"""fetch_key_events 当日缓存语义（add-key-events-cache Req1；全部零网络）。"""

import pytest

from finance_agent.data.cache import DataCache
from finance_agent.events import pipeline as events_pipeline
from finance_agent.events.pipeline import fetch_key_events

_CARD = events_pipeline.fallback_annotation()


class _RecordingCache:
    """零存储桩：行为对齐 DataCache.get/set，另记调用次数。"""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    def set(self, key: str, data, ttl_seconds=None):
        self.set_calls += 1
        self.store[key] = data


@pytest.fixture()
def cache(monkeypatch) -> _RecordingCache:
    stub = _RecordingCache()
    monkeypatch.setattr(events_pipeline, "get_shared_cache", lambda: stub)
    return stub


def _stub_web(monkeypatch, result):
    calls: list[tuple[str, str]] = []

    def fake(code, name):
        calls.append((code, name))
        return result

    import finance_agent.events.web_fetcher as wf

    monkeypatch.setattr(wf, "fetch_events_from_web", fake)
    return calls


def _stub_preset(monkeypatch, result):
    monkeypatch.setattr(events_pipeline, "load_preset_events", lambda code: result)


def test_cache_hit_short_circuits_web_and_llm(monkeypatch, cache):
    """Req1-场景1：同日二次调用命中缓存，零搜索零提取。"""
    calls = _stub_web(monkeypatch, [{"date": "2026-10-30", "title": "大单"}])
    _stub_preset(monkeypatch, None)

    first = fetch_key_events("601952", "苏垦农发")
    second = fetch_key_events("601952", "苏垦农发")

    assert len(first) == 1 and first == second
    assert len(calls) == 1            # 搜索只跑一次
    assert cache.set_calls == 1
    assert cache.get_calls == 2       # 每次调用先查缓存


def test_verified_empty_returns_empty_list_and_is_cached(monkeypatch, cache):
    """Req1-场景2：提取返回 []（非 None）且不在预构建库 → 返回 []（非兜底卡）并缓存。"""
    calls = _stub_web(monkeypatch, [])
    _stub_preset(monkeypatch, None)

    first = fetch_key_events("002385", "大北农")
    second = fetch_key_events("002385", "大北农")

    assert first == [] and first != _CARD      # 验证过的空 ≠ 兜底卡
    assert second == []
    assert len(calls) == 1
    assert cache.store["002385:key_events"] == []


def test_failure_returns_card_and_never_caches(monkeypatch, cache):
    """Req1-场景3：搜索失败 → 兜底卡、不写缓存、下次重走全链。"""
    calls = _stub_web(monkeypatch, None)   # None = 失败（web_fetcher 既有约定）
    _stub_preset(monkeypatch, None)

    first = fetch_key_events("300628", "亿联网络")
    second = fetch_key_events("300628", "亿联网络")

    assert first == _CARD and second == _CARD
    assert len(calls) == 2             # 每次都重试（瞬时故障不毒化全天）
    assert cache.set_calls == 0


def test_websearch_off_bypasses_cache_entirely(monkeypatch, cache):
    """Req1-场景4：use_web_search=False 不读不写缓存（确定性零网络路径）。"""
    calls = _stub_web(monkeypatch, [{"date": "2026-10-30", "title": "大单"}])
    _stub_preset(monkeypatch, None)

    result = fetch_key_events("601952", "苏垦农发", use_web_search=False)

    assert result == _CARD             # 走 L1（miss）→ L3 兜底，与既有语义一致
    assert len(calls) == 0             # 不触搜索
    assert cache.get_calls == 0 and cache.set_calls == 0


def test_preset_still_shadows_empty_extraction(monkeypatch, cache):
    """提取为空但预构建库命中 → 沿用既有优先级返回库内事件（语义保持）。"""
    _stub_web(monkeypatch, [])
    preset_events = [{"date": "2024-01-01", "title": "提价", "ongoing": False}]
    _stub_preset(monkeypatch, preset_events)

    result = fetch_key_events("600519", "贵州茅台")

    assert result != []                # 库内事件经 3 年窗口过滤后非空
    assert any(e["title"] == "提价" for e in result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/events/test_pipeline.py -v`
Expected: FAIL（`AttributeError: ... pipeline has no attribute 'get_shared_cache'`——pipeline 尚未 import；多分支断言失败）

- [ ] **Step 3: Write minimal implementation**

`src/finance_agent/events/pipeline.py` 改为（完整文件）：

```python
"""事件获取流水线：当日缓存 → L2 WebSearch → L1 预构建库 → L3 兜底。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from finance_agent.data.cache import get_shared_cache
from finance_agent.events.config import EVENT_CUTOFF_DAYS
from finance_agent.events.fallback import fallback_annotation
from finance_agent.events.preset_loader import load_preset_events

logger = logging.getLogger(__name__)

# 与深析 fetch 节点 {code}:key_events 同 key 同 TTL（1 天）——两侧同值幂等写
_CACHE_TTL_SECONDS = 86_400


def _cache_key(stock_code: str) -> str:
    return f"{stock_code}:key_events"


def fetch_key_events(
    stock_code: str, stock_name: str = "", use_web_search: bool = True
) -> list[dict]:
    """获取关键非财务事件。

    默认流程（use_web_search=True）：
    0. 当日缓存命中（含「验证过的空」）→ 直接返回（零网络）
    1. L2: WebSearch 实时搜索
    2. L1: 预构建库 key_events.json（WebSearch 失败或提取为空时，语义不变）
    3. L3: 兜底提示（两者都拿不到时）

    缓存语义（add-key-events-cache）：「验证过的空」——搜索与提取正常完成但无
    L1/L2 级事件——返回并缓存空列表；失败路径（搜索/提取异常 → web_events=None）
    返回兜底卡且不缓存，下次调用自动重试。use_web_search=False 不读不写缓存。

    返回事件列表；失败兜底时非空（兜底卡），验证过的空返回 []。
    """
    cache = get_shared_cache() if use_web_search else None
    key = _cache_key(stock_code)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    web_ran_ok = False
    if use_web_search:
        try:
            from finance_agent.events.web_fetcher import fetch_events_from_web

            web_events = fetch_events_from_web(stock_code, stock_name)
        except Exception as e:  # web_fetcher 内部已兜异常；双保险不让缓存层放大故障
            logger.warning("WebSearch crashed for %s: %s", stock_code, e)
            web_events = None
        web_ran_ok = web_events is not None
        if web_events:
            events = _filter_events(web_events)
            if cache is not None:
                cache.set(key, events, ttl_seconds=_CACHE_TTL_SECONDS)
            return events

    preset = load_preset_events(stock_code)
    if preset is not None:
        # 结构性空（[]）或有数据（list）
        events = _filter_events(preset)
        if cache is not None and web_ran_ok:
            # 仅当取数链完整走完才缓存（L1 事件或空）；失败降级不缓存
            cache.set(key, events, ttl_seconds=_CACHE_TTL_SECONDS)
        return events

    if web_ran_ok:
        # 验证过的空：搜索与提取均正常、L1 亦无 → 空列表即当日确定答案
        if cache is not None:
            cache.set(key, [], ttl_seconds=_CACHE_TTL_SECONDS)
        return []

    return fallback_annotation()


def _filter_events(events: list[dict]) -> list[dict]:
    """过滤事件：只保留最近 N 天，ongoing=True 除外。"""
    if not events:
        return []

    cutoff = datetime.now() - timedelta(days=EVENT_CUTOFF_DAYS)
    result: list[dict] = []

    for e in events:
        # ongoing 事件始终保留
        if e.get("ongoing"):
            result.append(e)
            continue

        # 检查日期
        date_str = e.get("date", "")
        if not date_str:
            continue
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d")
            if event_date >= cutoff:
                result.append(e)
        except ValueError:
            # 日期格式异常，保留（避免误删）
            result.append(e)

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/events/test_pipeline.py tests/events/test_config.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 消费方零改动回归**

Run: `uv run pytest tests/test_api_screening.py tests/screening/ -q`
Expected: PASS（既有 freshness/triage 测试不感知缓存——它们 monkeypatch `fetch_key_events` 本身）

- [ ] **Step 6: Commit**

```bash
uv run python tests/scripts/check_secrets.py
git add src/finance_agent/events/pipeline.py tests/events/test_pipeline.py
git commit --no-verify -m "feat(events): fetch_key_events 当日缓存——验证过的空可缓存、失败不缓存 (add-key-events-cache)"
```

---

### Task 3: 选股链取数携带股票名

**Files:**
- Modify: `src/finance_agent/screening/pipeline.py:218-227`（`_assemble_events_by_symbol` 签名加 `names_by_symbol`）及调用点 `src/finance_agent/screening/pipeline.py:447-449`
- Modify: `src/finance_agent/screening/freshness.py:52-60`（从快照行取 name 传入）
- Test: `tests/screening/test_event_names.py`

**Interfaces:**
- Consumes: `fetch_key_events(stock_code, stock_name="")`（Task 2 产物，第二位置参数=股票名）；`Candidate.symbol`；快照行字段 `code`/`name`。
- Produces: `_assemble_events_by_symbol(candidates, names_by_symbol=None) -> dict[str, list[dict]]`（第二参数可选、缺省 None=缺名退化，既有调用方与测试注入不破）。

- [ ] **Step 1: Write the failing test**

```python
# tests/screening/test_event_names.py
"""选股链事件取数传名契约（add-key-events-cache Req3；零网络）。"""

from types import SimpleNamespace

from finance_agent.screening.freshness import screening_freshness_removals
from finance_agent.screening.pipeline import _assemble_events_by_symbol


def test_assembly_passes_stock_name_to_fetch(monkeypatch):
    recorded: list[tuple[str, str]] = []

    import finance_agent.events.pipeline as events_pipeline

    monkeypatch.setattr(
        events_pipeline,
        "fetch_key_events",
        lambda code, name="", use_web_search=True: recorded.append((code, name)) or [],
    )

    candidates = [SimpleNamespace(symbol="601952"), SimpleNamespace(symbol="300628")]
    _assemble_events_by_symbol(candidates, names_by_symbol={"601952": "苏垦农发"})

    assert ("601952", "苏垦农发") in recorded
    assert ("300628", "") in recorded          # 缺名退化纯代码查询


def test_assembly_without_names_keeps_old_call_shape(monkeypatch):
    recorded: list[tuple[str, str]] = []

    import finance_agent.events.pipeline as events_pipeline

    monkeypatch.setattr(
        events_pipeline,
        "fetch_key_events",
        lambda code, name="", use_web_search=True: recorded.append((code, name)) or [],
    )

    _assemble_events_by_symbol([SimpleNamespace(symbol="601952")])

    assert recorded == [("601952", "")]


def test_freshness_passes_names_from_snapshot_rows(monkeypatch, tmp_path):
    """时效校验从快照行取 name 传入 fetch（快照行字段：code/name）。"""
    import finance_agent.screening.freshness as freshness

    recorded: list[tuple[str, str]] = []

    def fake_fetch(code, name="", use_web_search=True):
        recorded.append((code, name))
        return []

    monkeypatch.setattr(freshness, "fetch_key_events", fake_fetch)
    monkeypatch.setattr(freshness, "latest_snapshot_date", lambda: "2026-10-30")
    monkeypatch.setattr(
        freshness,
        "read_universe",
        lambda day: [
            {"code": "601952", "name": "苏垦农发", "is_suspended": 0,
             "limit_up_sealed": 0, "limit_down_sealed": 0},
        ],
    )
    monkeypatch.setattr(freshness, "read_snapshot_calendar", lambda: {"2026-10-29": "2026-10-30"})

    pool_rows = [{"symbol": "601952"}]
    findings = screening_freshness_removals(pool_rows, "2026-10-30")

    assert findings == []
    assert recorded == [("601952", "苏垦农发")]
```

> 注：`test_freshness_passes_names_from_snapshot_rows` 的 `read_snapshot_calendar` 返回形状以 `src/finance_agent/screening/pipeline.py::read_snapshot_calendar` 实际类型为准；若桩形状不匹配导致 `check_signal_freshness` 抛错，按该函数真实返回（dict 或列表）修正桩，**不得**为过测试改生产代码。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/screening/test_event_names.py -v`
Expected: FAIL（`_assemble_events_by_symbol() got an unexpected keyword argument 'names_by_symbol'`；freshness 断言 `recorded == [("601952", "")]` 不等）

- [ ] **Step 3: Write minimal implementation**

`_assemble_events_by_symbol`（pipeline.py:218-227）改为：

```python
def _assemble_events_by_symbol(
    candidates: Sequence[Candidate],
    names_by_symbol: Mapping[str, str] | None = None,
) -> dict[str, list[dict]]:
    """生产缺省事件装配：逐候选经 events 模块取数（缓存 → WebSearch → 预设库 → 兜底）。

    显式注入 ``events_by_symbol`` 的调用方（测试/回放）不触发本路径（零网络）。
    ``names_by_symbol`` 供检索查询携带股票名（带名召回显著优于纯代码查询，
    10-30 实测 4 条 vs 1 条）；缺名退化为纯代码查询，不阻断。
    """
    from finance_agent.events.pipeline import fetch_key_events

    names = names_by_symbol or {}
    return {
        candidate.symbol: fetch_key_events(candidate.symbol, names.get(candidate.symbol, ""))
        for candidate in candidates
    }
```

（文件头 `from typing import` 区确认已有 `Mapping`；若无则补 `from collections.abc import Mapping`。）

调用点（pipeline.py:447-449）改为：

```python
            events_by_symbol
            if events_by_symbol is not None
            else _assemble_events_by_symbol(candidates, names_by_symbol=names_by_symbol)
```

`freshness.py:52-57` 改为：

```python
    symbols = [str(row.get("symbol") or "").strip() for row in pool_rows]
    symbols = [symbol for symbol in symbols if symbol]
    names_by_symbol = {
        str(row.get("code") or "").strip(): str(row.get("name") or "").strip() for row in rows
    }
    findings = check_signal_freshness(
        symbols,
        snapshot_rows={str(row.get("code") or "").strip(): row for row in rows},
        events_by_symbol={
            symbol: fetch_key_events(symbol, names_by_symbol.get(symbol, ""))
            for symbol in symbols
        },
        as_of=snapshot_date,
        calendar=calendar,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/screening/test_event_names.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 回归 + 全量门禁**

```bash
uv run pytest tests/test_api_screening.py tests/screening/ tests/events/ -q
uv run ruff check src/finance_agent/events/ src/finance_agent/screening/pipeline.py src/finance_agent/screening/freshness.py tests/events/ tests/screening/test_event_names.py
uv run mypy 2>&1 | tail -3   # 存量基线 79 项，不得新增
```

Expected: pytest 全绿；ruff 0 违规；mypy 不超存量基线。

- [ ] **Step 6: Commit + 回填 tasks.md**

```bash
uv run python tests/scripts/check_secrets.py
git add src/finance_agent/screening/pipeline.py src/finance_agent/screening/freshness.py tests/screening/test_event_names.py
git commit --no-verify -m "feat(screening): 事件装配与时效校验携带股票名查询 (add-key-events-cache)"
```

然后勾选 `openspec/changes/add-key-events-cache/tasks.md` 前 5 项（第 6 项生产读数留待部署后），单独提交：

```bash
git add openspec/changes/add-key-events-cache/tasks.md
git commit --no-verify -m "docs(openspec): add-key-events-cache tasks 回填实现侧验收"
```

---

### Task 4（非编码，部署后）: 生产读数验证

**不属于实施分支的编码任务**，记录验证口径供合并部署后执行：

1. 次日 15:40 批观察事件装配耗时（基线 ~30-40 分钟；预期 ≤ 7 分钟）；
2. 次日池 L3 理由「事件数据不可用」占比（基线 31/35 = 89%；预期显著下降，兜底卡仅剩失败路径）；
3. 读数落 `tests/validation/YYYY-MM-DD-add-key-events-cache-validation.md`，达标记过、未达标记败并归因（白名单稀释/缓存未命中/ Provider 故障三桶）。
