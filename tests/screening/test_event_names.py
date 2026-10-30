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
    # 真实返回形状：read_snapshot_calendar -> list[str] | None（规范日期升序）
    monkeypatch.setattr(
        freshness, "read_snapshot_calendar", lambda: ["2026-10-29", "2026-10-30"]
    )

    pool_rows = [{"symbol": "601952"}]
    findings = screening_freshness_removals(pool_rows, "2026-10-30")

    assert findings == []
    assert recorded == [("601952", "苏垦农发")]
