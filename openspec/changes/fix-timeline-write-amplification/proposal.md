# Proposal: fix-timeline-write-amplification

## Why

incident 039 / issue #265 子项 1：两条管线消费路径（ReAct `_background_consume` 与 fast path `PipelineRunner`）的管线时序持久化存在双重 O(n²) 放大，是 thinking 积压的根本放大器：

1. **CPU 平方**：每个 thinking token 都经 `apply_pipeline_thinking_token` 做全 dict 复制 + 全节点 `close_last_thinking` 扫描 + `content + token` 整串拼接（O(L)/token，L 为已累积内容长度）。6 万+ token 洪峰下累计数十 GB 级字符串复制。
2. **写放大**：固定 0.5s 节流窗对**全量** `_nodeTimelines` 做 `json.dumps` + SQLite 整行 UPDATE。内容涨到 MB 级后单次写阻塞消费循环数百毫秒、每 0.5s 一次——生产实测把泵限速到 ~1.3 事件/s（5.76 万条积压的直接成因）。

主项（graph_done 积压压缩，PR #266）治的是「终态送达」，本子项治「积压形成」。

## What Changes

- **可变累加器**（`timeline_builder.py` 新增 `NodeTimelineAccumulator`）：thinking token 改为 per-node 分片缓冲 O(1) 追加；tool/search/node_complete 等低频事件在物化结构上走既有纯函数后回填缓冲；`materialize()` 产出与纯函数 fold **逐字节同构**的 `{node: [TimelineItem]}`（等价性由对照测试钉死）。
- **自适应中间写间隔**（新增 `timeline_persist_interval(payload_bytes)`）：洪峰期中间写间隔按上次序列化字节数伸缩——`interval = clamp(payload / TIMELINE_PERSIST_BW_CAP(256KB/s), 下限 0.5s, 上限 30s)`，写带宽有界；节点边界事件（node_complete/node_timing）**保持即时冲刷**（spec「与 pipeline_snapshot 同节奏」锚点不变）；结束 flush 全量补写（不变）。
- 两条消费路径（agent_factory / pipeline_runner）统一切换到累加器 + 自适应间隔。

**不变的语义**：持久化 JSON 格式不变；每次写入恒为当前**全量**结构（不截断、不引入增量块表）；前端恢复路径零改动；中间写仅调节奏——refresh 恢复可见的 thinking 内容 staleness 上界从 0.5s 放宽到 30s（洪峰极端），非洪峰期保持 0.5s 下限。

## Capabilities

- `session-persistence`：「管线时序持久化」Requirement 节奏语义细化（MODIFIED）——明确中间写自适应间隔与带宽上限、节点边界即时冲刷、结束 flush 全量三档语义。

## Impact

- `src/finance_agent/timeline_builder.py`（新增累加器 + 间隔函数）
- `src/finance_agent/agent_factory.py`（`_background_consume` 切换）
- `src/finance_agent/pipeline_runner.py`（fast path 切换）
- 测试：新增 `tests/test_timeline_accumulator.py`（等价性/边界）+ 既有 `test_pipeline_write_blocking.py` / `test_pipeline_watchdog_graph_done.py` 回归
- 风险：累加器与纯函数语义漂移 → 由随机交错序列等价性测试 + 既有 115+ 时序测试双重拦截；间隔上限 30s 对 refresh 中会话的观感影响已在提案显式接受。
