# Incident 042: 消费滞后丢落库时序——#266 压缩分支与 #304「flush 全量」的规范矛盾（CI flake 取证揪出）

**日期**: 2026-10-11
**状态**: 已修复（PR #307 附带 commit 871a31be，滞后回归测试钉死）
**关联**: [039（同链路前案）](039-relay-lag-prereport-backlog-20261008.md)、issue #265（子项 1 的写放大治理 #304 引入的耦合暴露面）、spec `session-persistence`（fix-timeline-write-amplification delta）× `session-streaming`「图完成后工具消费端压缩缓冲明细」

## 症状

CI 纯取证发现，无生产报障（生产侧时序丢数据静默发生，读侧无从察觉）：

1. PR #307 CI lint-and-test 首跑「Unit tests」步骤 pytest summary 打印后进程挂起 6 小时（job timeout 强杀）——挂起掩盖了真实失败；
2. 同 commit 重跑：`tests/test_timeline_write_bounds.py::test_react_flood_interim_writes_bounded` 红——**结束 flush 只写了 1743/3000 个 thinking token**（3569664 = 1743 × 2048，精确非随机）；
3. 本地 10/10 全过、CI 同 commit 一次全过（首轮 4532 passed）一次红——CI 专属时序 flake。

## 根因（数字精确吻合的三段推导）

**机制**：`graph_done` 是 `threading.Event`，生产者线程排完流（`run_coroutine_threadsafe` 全部调度完）即在 finally 中置位——**置位时刻不代表消费者已消费**。消费者滞后时（CI 慢机/to_thread 写 await 窗口），积压 thinking chunk 在 `graph_done` 后到达消费循环，走 #266 的 incident-039 压缩分支：

```
1743 保留 = 消费位 1231 + TAIL_KEEP_MAX 512
1257 丢弃 = graph_done 时刻积压 1769 − TAIL_KEEP_MAX 512
```

**规范矛盾**：#266 的实现把「不入 event_queue（终态有界送达，spec `session-streaming` 授权）」与「不做 timeline 累积」（**无 spec 授权**）耦合一处——而 `session-persistence`（#304 delta）钉「管线结束 flush SHALL 即时全量写入」。压缩丢事件是对的，丢累积是违反 spec 的：**落库时序（pipeline_timelines，会话详情/前端时序图的数据源）在消费滞后场景静默缺一段**。

**耦合的历史成因**：#266 写于 #304 之前——当时累积是 O(n) 拷贝+持久化 O(n²) 写放大（incident 039 的病灶），压缩时跳过累积是性能自卫；#304 改为 O(1) parts 追加 + 自适应间隔限流后，「跳过累积」失去了性能依据，只剩数据丢失副作用。属**重构后未回访的历史耦合**。

## 修复（871a31be）

压缩分支保留 `_timelineAcc.add_thinking_token`（仅跳过事件转发）——时间线完整性与终态有界送达解耦，两条 spec 各自满足。

**回归测试**（`test_react_lag_backlog_keeps_full_timeline`）：滞后需确定性制造——
- ❌ 消费者内 `time.sleep`：阻塞事件循环把 `run_coroutine_threadsafe` 的 put 调度一起堵死，chunk_queue 根本积不起压（put 也是 loop 回调）；
- ✅ spy 写库在 **worker 线程内** sleep 50ms：`to_thread` await 是循环空闲窗口，生产者把剩余 chunk 批量入队，消费者跨 `graph_done` 边界时积压 > TAIL_KEEP_MAX——旧代码红 `512×2048` 精确复现，新代码绿。

## 教训

1. **线程 Event 跨边界 ≠ 队列已空**：`graph_done` 语义是「生产者发完」不是「消费者读完」——凡按 `graph_done` 分流的逻辑必须同时回答「此刻积压里是什么」。
2. **spec 冲突靠 CI 取证撞出来**：两条各自合理的 spec 授权（有界送达 × flush 全量）在实现层耦合后互相矛盾，测试在真实语序下才红。两 spec 的交叉语义（压缩对持久化的作用域）现由 delta `fix-timeline-write-amplification` 场景与回归测试共同钉死。
3. **flake 分级**：同 commit 多跑结果不同 + 本地恒绿 + 失败数字精确非随机（1743 = 1231+512）——数字反推机制是时序类取证最快的路。同期对照：timeline 组 E2E 的负载敏感 flake（issue #312，五跑三红、docs-only 同红）是另一族，靠重跑指纹区分。
