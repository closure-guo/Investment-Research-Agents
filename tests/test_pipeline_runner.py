"""PipelineRunner 后台执行器测试。

对应 change: resume-pipeline-across-sessions Task 2。
验证：
- start 幂等，后台线程跑完事件源后 is_running 归 False
- 事件经 get_events 消费式取走
- 节点事件驱动 pipeline_snapshot 持久化（layerTree 状态推进）
- mark_swept_failed 将悬挂 running 会话置 failed
"""

from __future__ import annotations

import json
import time

from finance_agent import session_store
from finance_agent.pipeline_runner import PipelineRunner


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _fake_events():
    """模拟 _run_graph_streaming 的 SSE 事件序列（两节点 + 完成）。"""
    yield _sse({"type": "analysis_start", "session_id": "s1"})
    yield _sse({"type": "node_start", "node_id": "check_cache", "layer": "PREP"})
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "check_cache",
            "layer": "PREP",
            "completed": ["check_cache"],
            "progress": 0.03,
            "output": {"summary": "ok"},
        }
    )
    yield _sse({"type": "node_start", "node_id": "fetch_data", "layer": "PREP"})
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "fetch_data",
            "layer": "PREP",
            "completed": ["check_cache", "fetch_data"],
            "progress": 0.06,
            "output": {},
        }
    )
    yield _sse({"type": "report_ready", "session_id": "s1", "report_markdown": "# 报告"})


def test_start_idempotent_and_events_accumulate(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid, _fake_events, {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    )
    PipelineRunner.start(
        sid, _fake_events, {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    )  # 幂等不重复启动

    # 等后台线程跑完
    deadline = time.time() + 5
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)
    assert not PipelineRunner.is_running(sid)

    events = PipelineRunner.get_events(sid)
    assert any('"node_start"' in e for e in events)
    assert any('"report_ready"' in e for e in events)

    # 快照已持久化
    row = session_store.get_session(sid)
    snap = json.loads(row["pipeline_snapshot"])
    assert snap["currentNodeId"] in ("", "fetch_data")
    assert snap["progress"] > 0


def test_snapshot_tracks_node_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid, _fake_events, {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    )
    deadline = time.time() + 5
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)

    snap = json.loads(session_store.get_session(sid)["pipeline_snapshot"])
    # layerTree 为内嵌的序列化 JSON 字符串，需二次解析得到树结构
    tree = json.loads(snap["layerTree"])
    all_children = [c for layer in tree for c in layer["children"]]
    by_id = {c["nodeId"]: c for c in all_children}
    assert by_id["check_cache"]["status"] == "completed"
    assert by_id["fetch_data"]["status"] == "completed"


def test_sweep_marks_running_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")
    n = PipelineRunner.mark_swept_failed()
    assert n >= 1
    assert session_store.get_session(sid)["status"] == "failed"


def _failing_events():
    """迭代中途抛异常的事件源。"""
    yield _sse({"type": "node_start", "node_id": "check_cache", "layer": "prep"})
    raise RuntimeError("模拟事件源异常")


def test_event_source_exception_marks_failed(tmp_path, monkeypatch):
    """事件源迭代中抛异常 → session 置 failed 且 is_running 归 False。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid,
        _failing_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    deadline = time.time() + 5
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)

    assert not PipelineRunner.is_running(sid)
    assert session_store.get_session(sid)["status"] == "failed"


def test_publish_timeout_does_not_kill_pipeline(tmp_path, monkeypatch):
    """单次事件投递等待超时（主循环积压）不得崩掉整条管线（issue #490）。

    投递是 best-effort：`run_coroutine_threadsafe(...).result(timeout=5)` 在积压时
    抛空 message TimeoutError，若按管线异常处理会把整条分析标 failed——2026-10-26
    生产 `'TimeoutError: '` 会话族即此类无信息量失败。超时应跳过该事件继续推进。
    """
    import finance_agent.pipeline_runner as pipeline_runner_module

    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    class _TimeoutFuture:
        def result(self, timeout=None):
            raise TimeoutError()

    def _fake_rct(coro, loop):
        coro.close()  # 未被调度的协程显式关闭，避免 never-awaited 警告
        return _TimeoutFuture()

    monkeypatch.setattr(pipeline_runner_module.asyncio, "run_coroutine_threadsafe", _fake_rct)

    PipelineRunner.start(
        sid,
        _fake_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
        loop=object(),  # 非 None → Fast path（publish 桥接）
    )
    deadline = time.time() + 10
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)

    assert not PipelineRunner.is_running(sid)
    assert session_store.get_session(sid)["status"] != "failed"


def test_get_events_cleanup_after_done(tmp_path, monkeypatch):
    """管线跑完后 get_events 消费取空 → _running 条目被清理。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid, _fake_events, {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    )
    deadline = time.time() + 5
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)

    # 第一次取走全部累积事件；第二次取空触发清理
    assert PipelineRunner.get_events(sid)
    assert PipelineRunner.get_events(sid) == []
    assert sid not in PipelineRunner._running


def _timeline_events():
    """含 thinking_token（带 node）与 node_complete 的受控事件序列。"""
    yield _sse({"type": "analysis_start", "session_id": "s1"})
    yield _sse({"type": "node_start", "node_id": "check_cache", "layer": "PREP"})
    yield _sse({"type": "thinking_token", "token": "读取缓存", "node": "check_cache"})
    yield _sse({"type": "thinking_token", "token": "…命中", "node": "check_cache"})
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "check_cache",
            "layer": "PREP",
            "output": {"summary": "ok"},
        }
    )
    yield _sse({"type": "node_start", "node_id": "fetch_data", "layer": "PREP"})
    yield _sse({"type": "thinking_token", "token": "拉取行情", "node": "fetch_data"})
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "fetch_data",
            "layer": "PREP",
            "output": {},
        }
    )
    yield _sse({"type": "report_ready", "session_id": "s1", "report_markdown": "# 报告"})


def _wait_done(sid: str) -> None:
    deadline = time.time() + 5
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)


def test_pipeline_timelines_grouped_and_closed(tmp_path, monkeypatch):
    """thinking_token 按 node 分组持久化；node_complete 收口该节点末尾 thinking。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid,
        _timeline_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    _wait_done(sid)

    timelines = session_store.get_session(sid)["pipeline_timelines"]
    assert isinstance(timelines, dict)
    assert timelines["check_cache"] == [
        {"type": "thinking", "content": "读取缓存…命中", "done": True}
    ]
    assert timelines["fetch_data"] == [{"type": "thinking", "content": "拉取行情", "done": True}]


def test_pipeline_timelines_defensive_close_across_nodes(tmp_path, monkeypatch):
    """node_complete 缺失时，下一节点 thinking_token 防御性收口上一节点。"""

    def _events_without_complete():
        yield _sse({"type": "thinking_token", "token": "未收口", "node": "check_cache"})
        yield _sse({"type": "thinking_token", "token": "新节点", "node": "fetch_data"})

    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid,
        _events_without_complete,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    _wait_done(sid)

    timelines = session_store.get_session(sid)["pipeline_timelines"]
    assert timelines["check_cache"][0]["done"] is True
    assert timelines["fetch_data"][0]["done"] is False


def _search_tool_events():
    """模拟 fast path 事件序列：node_start/thinking/search/tool/node_complete 交错。

    search/tool 事件不带 node 字段，应按「当前运行节点」（最近 node_start
    且未 node_complete）归属持久化到 pipeline_timelines。
    """
    yield _sse({"type": "analysis_start", "session_id": "s1"})
    yield _sse({"type": "node_start", "node_id": "fetch_data", "layer": "PREP"})
    yield _sse({"type": "thinking_token", "token": "拉取行情", "node": "fetch_data"})
    yield _sse({"type": "tool_call", "name": "search_stock", "args": {"query": "茅台"}})
    yield _sse({"type": "tool_result", "name": "search_stock", "result": "600519"})
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "fetch_data",
            "layer": "PREP",
            "output": {},
        }
    )
    yield _sse({"type": "node_start", "node_id": "market_analyst", "layer": "I"})
    yield _sse({"type": "thinking_token", "token": "分析中", "node": "market_analyst"})
    yield _sse({"type": "search_start", "query": "茅台 最新消息"})
    yield _sse(
        {
            "type": "search_result",
            "query": "茅台 最新消息",
            "results": [{"title": "新闻", "url": "http://x", "content": "c"}],
        }
    )
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "market_analyst",
            "layer": "I",
            "output": {},
        }
    )
    yield _sse({"type": "report_ready", "session_id": "s1", "report_markdown": "# 报告"})


def test_search_tool_events_attributed_to_current_node(tmp_path, monkeypatch):
    """search/tool 事件归入当前运行节点；node_complete 后该节点 timeline 收口。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid,
        _search_tool_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    _wait_done(sid)

    timelines = session_store.get_session(sid)["pipeline_timelines"]
    # fetch_data 节点：thinking 被 tool_call 收口，tool_result 同名回填
    assert timelines["fetch_data"] == [
        {"type": "thinking", "content": "拉取行情", "done": True},
        {
            "type": "tool_call",
            "name": "search_stock",
            "args": "茅台",
            "result": "600519",
            "done": True,
        },
    ]
    # market_analyst 节点：search_start → search_result 归入该节点；
    # 该节点 thinking 末尾是 search item，node_complete 不再收口
    # （close_last_thinking 仅作用于末尾 thinking item，与前端语义一致）
    assert timelines["market_analyst"] == [
        {"type": "thinking", "content": "分析中", "done": False},
        {
            "type": "search",
            "query": "茅台 最新消息",
            "status": "done",
            "results": [{"title": "新闻", "url": "http://x", "content": "c"}],
        },
    ]
    # 不应泄漏到 '' 键或其他节点
    assert "" not in timelines


def test_search_tool_before_any_node_start_falls_into_empty_key(tmp_path, monkeypatch):
    """无任何 node_start 前到达的 search/tool 事件（currentNode 为 ''）归入 '' 键。"""

    def _events_before_node():
        yield _sse({"type": "search_start", "query": "预热搜索"})
        yield _sse({"type": "search_result", "query": "预热搜索", "results": []})

    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid,
        _events_before_node,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    _wait_done(sid)

    timelines = session_store.get_session(sid)["pipeline_timelines"]
    assert timelines[""] == [
        {"type": "search", "query": "预热搜索", "status": "done", "results": []}
    ]


def test_search_tool_after_node_complete_falls_into_empty_key(tmp_path, monkeypatch):
    """node_complete 清空 currentNode 后到达的 search 事件归入 '' 键。"""

    def _events_after_complete():
        yield _sse({"type": "node_start", "node_id": "check_cache", "layer": "PREP"})
        yield _sse(
            {
                "type": "node_complete",
                "node_id": "check_cache",
                "layer": "PREP",
                "output": {},
            }
        )
        yield _sse({"type": "search_start", "query": "节点间隙搜索"})

    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    PipelineRunner.start(
        sid,
        _events_after_complete,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    _wait_done(sid)

    timelines = session_store.get_session(sid)["pipeline_timelines"]
    assert timelines[""] == [{"type": "search", "query": "节点间隙搜索", "status": "searching"}]
