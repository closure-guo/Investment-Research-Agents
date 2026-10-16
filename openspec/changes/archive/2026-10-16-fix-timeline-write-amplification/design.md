# Design: fix-timeline-write-amplification

## D1 可变累加器的语义等价边界

`NodeTimelineAccumulator` 是 `apply_pipeline_*` 纯函数族的可变高效实现，**语义以纯函数为准**：

- 内部结构：`dict[str, list[dict]]`，thinking item 以 `parts: list[str]` 分片暂存（O(1) append），其余 item（search/tool_call）原样存 dict；
- `add_thinking_token(node, token)`：维护 `_active_node`；切换节点时对先前活动节点末段未收口 thinking 置 done（等价纯函数「对其他节点 close_last_thinking」——已 done 者幂等，故只需追踪活动节点）；当前节点末项为 thinking（不论 done，镜像 `append_thinking_token` 只看 type）则 `parts.append`，否则新建 thinking item；
- `apply_search_event` / `apply_tool_event` / `apply_node_complete`：低频事件——先物化该节点 timeline，调既有纯函数，再把 thinking item 的 content 拆回 `parts=[content]` 回填；
- `materialize()`：遍历 join `parts` → `content`，产出结构与纯函数 fold 同构（键序、字段、done/title 语义一致）。

等价性测试（TDD 先红）：脚本化交错序列（多节点洪峰 + 切回 + tool/search 穿插 + node_complete + 洪峰后再 token）逐事件对照 `accumulator.materialize() == fold(纯函数)`。

## D2 自适应间隔参数

```python
TIMELINE_PERSIST_INTERVAL = 0.5      # 下限（秒），非洪峰期保持现状
TIMELINE_PERSIST_INTERVAL_MAX = 30.0 # 上限（秒），refresh 可见 staleness 封顶
TIMELINE_PERSIST_BW_CAP = 256 * 1024 # 写带宽上限（字节/秒）
```

`timeline_persist_interval(payload_bytes) = clamp(payload / BW_CAP, 0.5, 30.0)`：

- ≤128KB → 0.5s（现状行为不变）；
- 1MB → 4s；5MB → 20s；≥7.5MB → 30s 封顶。

选 256KB/s 的依据：incident 039 实测泵崩在「每 0.5s 一次 MB 级写」（占空比 ~90%）；256KB/s 把单次 5MB 写的占空比压到 ~2%，同时保留 refresh 恢复粒度的实用价值。上限 30s 是 refresh staleness 的可接受上界（refresh 是稀有路径，SSE 在线期间不受影响）。

间隔函数的输入取**上次实际序列化的字节数**（写后记录），首次写入前用 0 → 下限 0.5s，冷启动行为与现状一致。

## D3 节点边界即时冲刷不变

spec「与 pipeline_snapshot 同节奏」的锚点是节点事件。现状 node_complete 分支即时写库（agent_factory:962-966）保留；fast path 节点边界分支同样保留即时写。自适应间隔**只作用于 thinking 洪峰期的定时中间写**。

## D4 结束 flush 不变

正常结束/异常/超时分支的全量补写（两条路径均已存在）保持原样——终态内容完整性由 flush 保证，中间写的节奏调整不影响终态。

## D5 不选截断/增量表的原因

- 截断中间写：改变中间态内容（需引入截断标记语义），前端虽能渲染但恢复语义出现「中间态 ≠ 终态」分叉，测试矩阵翻倍；
- 增量块表（chunks table + 末次拼装）：schema 迁移 + 恢复路径重写，收益与自适应间隔相当但侵入性高一个量级；
- 自适应间隔只调节奏、内容恒全量，是满足「写带宽有界」的最小变更。
