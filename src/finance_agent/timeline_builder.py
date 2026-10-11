"""后端 agentTimeline 构建器 —— 逐行镜像 frontend/src/timeline.ts 的 applyChatStreamEvent。

设计见 openspec/changes/persist-full-session-timeline/design.md（D2）：
后端在流式消费 SSE 事件时构建与前端同构的结构化时序，持久化到 chat_history
的 agentTimeline 字段。所有函数均为纯函数（不可变更新，返回新 list），
元素为 TimelineItem 同构 dict：

- thinking:  {"type": "thinking", "content": str, "done": bool, "title"?: str | None}
- search:    {"type": "search", "query": str, "status": "searching"|"done"|"error", "results"?: list}
- tool_call: {"type": "tool_call", "name": str, "args": str, "result"?: str, "done": bool}
"""

# 项目规范要求变量命名使用 camelCase，与 pep8-naming 的 snake_case 冲突，模块级豁免
# ruff: noqa: N806

from __future__ import annotations

import json
import re
from typing import Any

# 搜索类工具集合（与前端 SEARCH_TOOL_NAMES 保持一致；web_search / batch_web_search
# 走 search item，不进 tool_call item —— design.md 决策 8）
SEARCH_TOOL_NAMES = {"web_search", "batch_web_search"}

# 提取思考标题的正则（与前端 /^\s*##\s+(.+?)\s*$/m 等价）
_THINKING_TITLE_RE = re.compile(r"^\s*##\s+(.+?)\s*$", re.MULTILINE)


def append_thinking_token(timeline: list[dict], token: str) -> list[dict]:
    """向 timeline 追加/累加一个 thinking token：末尾是 thinking item 则累加，否则新建。

    镜像前端 appendThinkingToken：只判断末尾 type === 'thinking'，不看 done。
    """
    last = timeline[-1] if timeline else None
    if last and last.get("type") == "thinking":
        nextTimeline = list(timeline)
        nextTimeline[-1] = {**last, "content": last["content"] + token}
        return nextTimeline
    return [*timeline, {"type": "thinking", "content": token, "done": False}]


def close_last_thinking(timeline: list[dict]) -> list[dict]:
    """将末尾未完成（done 不为 True）的 thinking item 置为完成态；否则原样返回。

    镜像前端 closeLastThinking（无变化时返回同引用）。
    """
    last = timeline[-1] if timeline else None
    if last and last.get("type") == "thinking" and last.get("done") is not True:
        nextTimeline = list(timeline)
        nextTimeline[-1] = {**last, "done": True}
        return nextTimeline
    return timeline


def close_all_thinking(timeline: list[dict]) -> list[dict]:
    """将所有未完成 thinking item 置为完成态（chat_done / error 收口用）。"""
    return [
        {**item, "done": True}
        if item.get("type") == "thinking" and item.get("done") is not True
        else item
        for item in timeline
    ]


def extract_thinking_title(content: str) -> str | None:
    """从思考内容中提取首个 ## 二级标题作为横幅标题（与前端 extractThinkingTitleLocal 同策略）。"""
    if not content:
        return None
    match = _THINKING_TITLE_RE.search(content)
    return match.group(1) if match else None


def _json_dumps(value: Any) -> str:
    """JSON 序列化（ensure_ascii=False，与前端 JSON.stringify 的 UTF-16 长度语义对齐）。"""
    return json.dumps(value, ensure_ascii=False)


def summarize_tool_result(result: Any) -> str:
    """将工具结果浓缩为简短文本（与前端 summarizeToolResultLocal 同策略）。

    - str 取前 150 字符
    - list 取前 3 项的 title/name/code，或该项 JSON 前 50 字符，用「、」连接
    - dict 转 JSON 取前 150 字符
    - 其他类型返回 ''
    """
    if isinstance(result, str):
        return result[:150]
    if isinstance(result, list):
        items: list[str] = []
        for entry in result[:3]:
            # 前端 (r as Record)?.title 对非对象返回 undefined，走 JSON.stringify 分支
            if isinstance(entry, dict):
                value = entry.get("title") or entry.get("name") or entry.get("code")
                if value:
                    items.append(str(value))
                    continue
            items.append(_json_dumps(entry)[:50])
        return "、".join(items)
    if isinstance(result, dict):
        return _json_dumps(result)[:150]
    return ""


def summarize_tool_args(args: dict | None) -> str:
    """将工具调用参数浓缩为展示文本（query / queries 优先，其余非空 dict 转 JSON）。

    镜像前端 summarizeToolArgs。
    """
    if not args:
        return ""
    query = args.get("query")
    if isinstance(query, str):
        return query
    queries = args.get("queries")
    if isinstance(queries, list):
        return "、".join(str(q) for q in queries)
    return _json_dumps(args) if args else ""


# ── 管线分组件（nodeTimelines，镜像 applyPipelineThinkingToken / applyPipelineNodeComplete）──


def apply_pipeline_thinking_token(
    node_timelines: dict[str, list[dict]], node: str | None, token: str
) -> dict[str, list[dict]]:
    """管线模式：thinking_token 按 node 写入对应节点的 timeline（不可变更新）。

    镜像前端 applyPipelineThinkingToken：
    - node 缺失/空串归入 '' 键（与历史未分组思考兼容）
    - 其他节点末尾未完成的 thinking item 防御性收口（close_last_thinking）
    - 当前节点 append_thinking_token
    """
    nodeKey = node or ""
    nextTimelines: dict[str, list[dict]] = {}
    # 防御性收口：其他节点末尾未完成的 thinking item 置为完成态
    for key, timeline in node_timelines.items():
        nextTimelines[key] = timeline if key == nodeKey else close_last_thinking(timeline)
    current = nextTimelines.get(nodeKey, [])
    nextTimelines[nodeKey] = append_thinking_token(current, token)
    return nextTimelines


def apply_pipeline_node_complete(
    node_timelines: dict[str, list[dict]], node: str
) -> dict[str, list[dict]]:
    """管线模式：node_complete 将该节点末尾未完成的 thinking item 显式收口。

    镜像前端 applyPipelineNodeComplete：无该节点则原样返回同引用。
    """
    if node not in node_timelines:
        return node_timelines
    nextTimelines = dict(node_timelines)
    nextTimelines[node] = close_last_thinking(nextTimelines[node])
    return nextTimelines


def apply_pipeline_search_event(
    node_timelines: dict[str, list[dict]], node: str | None, event: dict
) -> dict[str, list[dict]]:
    """管线模式：search_start/search_result/search_error 归属当前运行节点的 timeline。

    事件本身不带 node 字段，由调用方解析「当前运行节点」传入；
    node 缺失/空串归入 '' 键（与 thinking_token 的历史未分组兼容）。
    三态语义复用 apply_chat_event（search_start append searching；
    search_result 更新最近 searching→done+results；search_error→error）。
    """
    nodeKey = node or ""
    nextTimelines = dict(node_timelines)
    nextTimelines[nodeKey] = apply_chat_event(nextTimelines.get(nodeKey, []), event)
    return nextTimelines


def apply_pipeline_tool_event(
    node_timelines: dict[str, list[dict]], node: str | None, event: dict
) -> dict[str, list[dict]]:
    """管线模式：tool_call/tool_result 归属当前运行节点的 timeline。

    语义复用 apply_chat_event（搜索类工具名跳过 tool_call item，由 search 事件承载；
    tool_call 收口末段 thinking 后 append；tool_result 同名回填/回退/仅结果项）。
    node 缺失/空串归入 '' 键。
    """
    nodeKey = node or ""
    nextTimelines = dict(node_timelines)
    nextTimelines[nodeKey] = apply_chat_event(nextTimelines.get(nodeKey, []), event)
    return nextTimelines


# ── 写放大治理（fix-timeline-write-amplification，issue #265 子项1，incident 039）──

# thinking 洪峰期中间写自适应间隔三件套：间隔 = clamp(上次序列化字节数 ÷ 带宽上限,
# 下限, 上限)。下限保持非洪峰期现状行为；上限封顶 refresh 恢复可见 staleness。
TIMELINE_PERSIST_INTERVAL = 0.5  # 秒，下限
TIMELINE_PERSIST_INTERVAL_MAX = 30.0  # 秒，上限
TIMELINE_PERSIST_BW_CAP = 256 * 1024  # 写带宽上限（字节/秒）


def timeline_persist_interval(payload_bytes: int) -> float:
    """自适应中间写间隔：payload 越大间隔越长，写带宽有界（O(n²) 写放大根治）。"""
    scaled = payload_bytes / TIMELINE_PERSIST_BW_CAP
    return min(TIMELINE_PERSIST_INTERVAL_MAX, max(TIMELINE_PERSIST_INTERVAL, scaled))


class NodeTimelineAccumulator:
    """apply_pipeline_* 纯函数族的可变 O(1) 摊销实现（语义以纯函数为准）。

    逐 token 调纯函数的代价是 O(n²)：每次全 dict 复制 + 全节点收口扫描 +
    content 整串拼接。本类把 thinking 内容改为 parts 分片缓冲（append O(1)），
    低频 tool/search/node_complete 事件物化后走既有纯函数再回填；
    materialize() 产出与纯函数 fold 逐字节同构的结构（等价性测试钉死）。

    不变量：任一时刻至多一个节点（最近收到 thinking token 的活动节点）存在
    未收口 thinking 末段——纯函数「每 token 对其他所有节点 close_last_thinking」
    与此等价（已 done 者幂等），故只需在节点切换时收口先前活动节点。
    """

    def __init__(self) -> None:
        self._items: dict[str, list[dict]] = {}
        self._active_node: str | None = None

    def __bool__(self) -> bool:
        """空值守卫：结束 flush 的 `if acc:` 判据（等价旧 `if nodeTimelines:`）。"""
        return bool(self._items)

    @staticmethod
    def _close_last_thinking_mut(timeline: list[dict]) -> None:
        last = timeline[-1] if timeline else None
        if last and last.get("type") == "thinking" and last.get("done") is not True:
            last["done"] = True

    def add_thinking_token(self, node: str | None, token: str) -> None:
        """O(1) 摊销追加：末项为 thinking（不看 done，镜像纯函数）则 parts.append。"""
        nodeKey = node or ""
        if self._active_node is not None and nodeKey != self._active_node:
            prev = self._items.get(self._active_node)
            if prev:
                self._close_last_thinking_mut(prev)
        self._active_node = nodeKey
        current = self._items.setdefault(nodeKey, [])
        last = current[-1] if current else None
        if last and last.get("type") == "thinking":
            last["parts"].append(token)
        else:
            current.append({"type": "thinking", "parts": [token], "done": False})

    def apply_node_complete(self, node: str) -> None:
        timeline = self._items.get(node)
        if timeline is None:
            return  # 镜像 apply_pipeline_node_complete：无该节点原样返回
        self._close_last_thinking_mut(timeline)

    def _apply_rare_event(self, node: str | None, event: dict) -> None:
        """低频 tool/search 事件：物化该节点 → 纯函数 apply_chat_event → 回填缓冲。"""
        nodeKey = node or ""
        materialized = self._materialize_timeline(self._items.get(nodeKey, []))
        updated = apply_chat_event(materialized, event)
        self._items[nodeKey] = [self._rebuffer(item) for item in updated]

    def apply_search_event(self, node: str | None, event: dict) -> None:
        self._apply_rare_event(node, event)

    def apply_tool_event(self, node: str | None, event: dict) -> None:
        self._apply_rare_event(node, event)

    @staticmethod
    def _rebuffer(item: dict) -> dict:
        if item.get("type") == "thinking":
            rest = {k: v for k, v in item.items() if k not in ("type", "content")}
            return {"type": "thinking", "parts": [item.get("content", "")], **rest}
        return dict(item)

    @staticmethod
    def _materialize_timeline(timeline: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in timeline:
            if item.get("type") == "thinking" and "parts" in item:
                rest = {k: v for k, v in item.items() if k not in ("type", "parts", "content")}
                out.append({"type": "thinking", "content": "".join(item["parts"]), **rest})
            else:
                out.append(dict(item))
        return out

    def materialize(self) -> dict[str, list[dict]]:
        """产出与纯函数 fold 同构的 {node: [TimelineItem]}；不消耗内部缓冲。"""
        return {node: self._materialize_timeline(tl) for node, tl in self._items.items()}


def apply_chat_event(timeline: list[dict], event: dict) -> list[dict]:
    """将对话流 SSE 事件应用到 agentTimeline，返回新 list（不可变更新）。

    镜像前端 applyChatStreamEvent 的 timeline 部分；chatResponse / streaming
    等消息级字段不在本函数职责内（由调用方处理）。未知事件原样返回同引用。
    """
    eventType = event.get("type")

    if eventType == "thinking_token":
        return append_thinking_token(timeline, event.get("token", ""))

    if eventType == "thinking_replace":
        # DSML 清理等后处理：整体替换末尾 thinking item 内容
        last = timeline[-1] if timeline else None
        if last and last.get("type") == "thinking":
            nextTimeline = list(timeline)
            nextTimeline[-1] = {**last, "content": event.get("token", "")}
            return nextTimeline
        return timeline

    if eventType == "thinking_to_answer":
        # 流末判定为最终回答：将末尾 thinking item 与 answer 匹配的部分移出（置 done）
        last = timeline[-1] if timeline else None
        answer = event.get("answer")
        if last and last.get("type") == "thinking" and answer:
            idx = last["content"].rfind(answer)
            if idx >= 0:
                nextTimeline = list(timeline)
                nextTimeline[-1] = {**last, "content": last["content"][:idx], "done": True}
                return nextTimeline
        return timeline

    if eventType == "search_start":
        return [
            *timeline,
            {"type": "search", "query": event.get("query"), "status": "searching"},
        ]

    if eventType == "search_result":
        # 更新最近的 searching 状态 search item 为 done 并写入结果
        nextTimeline = list(timeline)
        for i in range(len(nextTimeline) - 1, -1, -1):
            item = nextTimeline[i]
            if item.get("type") == "search" and item.get("status") == "searching":
                nextTimeline[i] = {**item, "status": "done", "results": event.get("results") or []}
                return nextTimeline
        # 无 searching item（容错）：新建 done item
        return [
            *timeline,
            {
                "type": "search",
                "query": event.get("query"),
                "status": "done",
                "results": event.get("results") or [],
            },
        ]

    if eventType == "search_error":
        nextTimeline = list(timeline)
        for i in range(len(nextTimeline) - 1, -1, -1):
            item = nextTimeline[i]
            if item.get("type") == "search" and item.get("status") == "searching":
                nextTimeline[i] = {**item, "status": "error"}
                return nextTimeline
        return timeline

    if eventType == "tool_call":
        # 搜索类工具由 search_* 事件驱动 SearchBanner，不生成 tool_call item
        if event.get("name") in SEARCH_TOOL_NAMES:
            return timeline
        # 思考后接工具调用：末尾未完成 thinking item 显式收口
        return [
            *close_last_thinking(timeline),
            {
                "type": "tool_call",
                "name": event.get("name"),
                "args": summarize_tool_args(event.get("args")),
                "done": False,
            },
        ]

    if eventType == "tool_result":
        # 搜索类工具结果由 search_result 事件驱动，不进入 tool_call item
        if event.get("name") in SEARCH_TOOL_NAMES:
            return timeline
        resultSummary = summarize_tool_result(event.get("result"))
        nextTimeline = list(timeline)
        # 优先：同名且 done 为假值的最近 item
        idx = -1
        for i in range(len(nextTimeline) - 1, -1, -1):
            item = nextTimeline[i]
            if (
                item.get("type") == "tool_call"
                and item.get("name") == event.get("name")
                and not item.get("done")
            ):
                idx = i
                break
        # 回退：最近未完成的任意 tool_call item
        if idx == -1:
            for i in range(len(nextTimeline) - 1, -1, -1):
                item = nextTimeline[i]
                if item.get("type") == "tool_call" and not item.get("done"):
                    idx = i
                    break
        if idx >= 0:
            item = nextTimeline[idx]
            if item.get("type") == "tool_call":
                nextTimeline[idx] = {**item, "result": resultSummary, "done": True}
            return nextTimeline
        # 无匹配且结果非空：新建仅含结果的 item
        if resultSummary:
            return [
                *timeline,
                {
                    "type": "tool_call",
                    "name": event.get("name"),
                    "args": "",
                    "result": resultSummary,
                    "done": True,
                },
            ]
        return timeline

    if eventType == "chat_token":
        # 思考后接回答：末尾未完成 thinking item 显式收口
        return close_last_thinking(timeline)

    if eventType == "chat_done":
        # 流式结束：所有 thinking item 收口；无 title 时提取 ## 标题写入 title
        return [
            {**item, "title": extract_thinking_title(item.get("content", ""))}
            if item.get("type") == "thinking" and "title" not in item
            else item
            for item in close_all_thinking(timeline)
        ]

    if eventType == "error":
        return close_all_thinking(timeline)

    return timeline
