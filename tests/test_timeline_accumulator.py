"""fix-timeline-write-amplification：可变累加器与自适应中间写间隔（TDD 先红）。

语义锚：NodeTimelineAccumulator 的 materialize() 输出 SHALL 与
apply_pipeline_* 纯函数族 fold 的结果逐字节同构——纯函数是前端镜像的
语义真源，累加器只是其 O(1) 摊销实现（design D1）。
"""

from __future__ import annotations

import pytest

from finance_agent.timeline_builder import (
    NodeTimelineAccumulator,
    apply_pipeline_node_complete,
    apply_pipeline_search_event,
    apply_pipeline_thinking_token,
    apply_pipeline_tool_event,
    timeline_persist_interval,
)


def _fold(events: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """纯函数 fold：语义真源（与前端镜像逐行对齐的既有实现）。"""
    timelines: dict[str, list[dict]] = {}
    for kind, payload in events:
        if kind == "thinking":
            timelines = apply_pipeline_thinking_token(
                timelines, payload.get("node"), payload.get("token", "")
            )
        elif kind == "search_start" or kind == "search_result":
            timelines = apply_pipeline_search_event(timelines, payload.get("node"), payload)
        elif kind == "tool_call" or kind == "tool_result":
            timelines = apply_pipeline_tool_event(timelines, payload.get("node"), payload)
        elif kind == "node_complete":
            timelines = apply_pipeline_node_complete(timelines, payload["node"])
    return timelines


def _accumulate(events: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    acc = NodeTimelineAccumulator()
    for kind, payload in events:
        if kind == "thinking":
            acc.add_thinking_token(payload.get("node"), payload.get("token", ""))
        elif kind in ("search_start", "search_result"):
            acc.apply_search_event(payload.get("node"), payload)
        elif kind in ("tool_call", "tool_result"):
            acc.apply_tool_event(payload.get("node"), payload)
        elif kind == "node_complete":
            acc.apply_node_complete(payload["node"])
    return acc.materialize()


class TestAccumulatorEquivalence:
    """materialize() == 纯函数 fold（逐事件对照终态）。"""

    def test_single_node_thinking_flood(self):
        events = [("thinking", {"node": "analyst1", "token": f"t{i}"}) for i in range(200)]
        assert _accumulate(events) == _fold(events)

    def test_multi_node_switch_and_back(self):
        # 切回语义：append_thinking_token 只看末尾 type==thinking（不看 done），
        # 切回 A 时内容追加到 A 既有（已收口）thinking item——累加器须镜像。
        events = [
            ("thinking", {"node": "A", "token": "a1"}),
            ("thinking", {"node": "A", "token": "a2"}),
            ("thinking", {"node": "B", "token": "b1"}),
            ("thinking", {"node": "A", "token": "a3"}),
            ("thinking", {"node": "", "token": "orphan"}),
            ("thinking", {"node": None, "token": "orphan2"}),
        ]
        assert _accumulate(events) == _fold(events)

    def test_tool_and_search_interleaved(self):
        events = [
            ("thinking", {"node": "analyst1", "token": "先想"}),
            ("tool_call", {"node": "analyst1", "name": "get_price", "args": {"query": "茅台"}}),
            ("thinking", {"node": "analyst1", "token": "再想"}),
            (
                "tool_result",
                {"node": "analyst1", "name": "get_price", "result": "1234.5"},
            ),
            ("search_start", {"node": "analyst1", "query": "茅台 新闻"}),
            (
                "search_result",
                {"node": "analyst1", "query": "茅台 新闻", "results": [{"title": "x"}]},
            ),
            ("thinking", {"node": "analyst1", "token": "收尾"}),
        ]
        assert _accumulate(events) == _fold(events)

    def test_node_complete_then_more_tokens(self):
        events = [
            ("thinking", {"node": "A", "token": "x"}),
            ("node_complete", {"node": "A"}),
            ("thinking", {"node": "A", "token": "y"}),
            ("node_complete", {"node": "B"}),  # 无 B 时序：纯函数原样返回
            ("thinking", {"node": "B", "token": "z"}),
        ]
        assert _accumulate(events) == _fold(events)

    def test_empty_accumulator(self):
        assert NodeTimelineAccumulator().materialize() == {}


class TestAccumulatorShards:
    """O(1) 行为代理：token 追加不整串拼接（parts 分片），物化后无 parts 残留。"""

    def test_thinking_item_uses_parts_internally(self):
        acc = NodeTimelineAccumulator()
        for i in range(10):
            acc.add_thinking_token("A", f"t{i}")
        internal = acc._items["A"][-1]
        assert "parts" in internal, "thinking item 应以 parts 分片暂存而非整串 content"
        assert len(internal["parts"]) == 10
        assert "content" not in internal

    def test_materialize_strips_parts(self):
        acc = NodeTimelineAccumulator()
        acc.add_thinking_token("A", "hello ")
        acc.add_thinking_token("A", "world")
        out = acc.materialize()
        assert out["A"][-1] == {"type": "thinking", "content": "hello world", "done": False}

    def test_materialize_does_not_mutate_internal_state(self):
        acc = NodeTimelineAccumulator()
        acc.add_thinking_token("A", "x")
        first = acc.materialize()
        acc.add_thinking_token("A", "y")
        second = acc.materialize()
        # 物化不消耗缓冲：两次物化独立，第一次结果不被后续追加污染
        assert first["A"][-1]["content"] == "x"
        assert second["A"][-1]["content"] == "xy"


class TestTimelinePersistInterval:
    """自适应间隔：clamp(payload / 256KB/s, 0.5s, 30s)。"""

    def test_zero_payload_uses_floor(self):
        assert timeline_persist_interval(0) == pytest.approx(0.5)

    def test_small_payload_uses_floor(self):
        assert timeline_persist_interval(128 * 1024) == pytest.approx(0.5)

    def test_one_mb_scales_to_four_seconds(self):
        assert timeline_persist_interval(1024 * 1024) == pytest.approx(4.0)

    def test_five_mb_scales_to_twenty_seconds(self):
        assert timeline_persist_interval(5 * 1024 * 1024) == pytest.approx(20.0)

    def test_huge_payload_capped_at_ceiling(self):
        assert timeline_persist_interval(64 * 1024 * 1024) == pytest.approx(30.0)

    def test_monotonic_non_decreasing(self):
        sizes = [0, 64 * 1024, 256 * 1024, 1024 * 1024, 8 * 1024 * 1024, 128 * 1024 * 1024]
        intervals = [timeline_persist_interval(s) for s in sizes]
        assert all(a <= b for a, b in zip(intervals, intervals[1:], strict=False))


class TestAccumulatorPerf:
    """性能实证（tasks 4.3）：纯函数 fold 是 O(n²)（每 token 全串拼接），
    累加器 O(1) 摊销。断言方向性（累加器更快）+ 打印比率供台账引用。
    """

    def test_flood_accumulation_not_quadratic(self):
        import time

        n = 20000  # 生产洪峰 6 万+ token 的缩影；再大纯函数臂耗时不可接受
        token = "x" * 128

        t0 = time.perf_counter()
        timelines: dict[str, list[dict]] = {}
        for i in range(n):
            timelines = apply_pipeline_thinking_token(timelines, "trader", f"{token}{i}")
        fold_elapsed = time.perf_counter() - t0

        t0 = time.perf_counter()
        acc = NodeTimelineAccumulator()
        for i in range(n):
            acc.add_thinking_token("trader", f"{token}{i}")
        acc_elapsed = time.perf_counter() - t0

        # 物化结果同构（正确性顺带复核）
        assert acc.materialize() == timelines
        ratio = fold_elapsed / max(acc_elapsed, 1e-6)
        print(
            f"\n[perf] 纯函数 fold {fold_elapsed:.3f}s vs 累加器 {acc_elapsed:.3f}s "
            f"（{n} tokens × 128B），比率 {ratio:.1f}×"
        )
        assert acc_elapsed < fold_elapsed, (
            f"累加器 SHALL 快于纯函数 fold：fold={fold_elapsed:.3f}s acc={acc_elapsed:.3f}s"
        )
