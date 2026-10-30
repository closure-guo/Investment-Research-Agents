# tests/events/test_pipeline.py
"""fetch_key_events 当日缓存语义（add-key-events-cache Req1；全部零网络）。"""

import pytest

from finance_agent.events import pipeline as events_pipeline
from finance_agent.events.pipeline import fetch_key_events

_CARD = events_pipeline.fallback_annotation()


class _RecordingCache:
    """零存储桩：行为对齐 DataCache.get/set，另记调用次数。"""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.ttls: dict[str, int | None] = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    def set(self, key: str, data, ttl_seconds=None):
        self.set_calls += 1
        self.ttls[key] = ttl_seconds
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
    # TTL 钉死 spec 规范值（1 天），防静默漂移
    assert cache.ttls["601952:key_events"] == 86_400


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
