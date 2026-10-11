# Incident 039: 事件中继滞后病——图已完成但终态被 thinking 前置积压推迟（037 同族复发）

**日期**: 2026-10-08
**状态**: 根因修复已提 PR #266（issue #265）；timeline O(n²) 写放大、中继猝死取证、运行中进度滞后观感三子项挂账
**关联**: [037（同族前案）](037-event-backlog-sqlite-transient-terminal-loss-20261004.md)、[021（假卡死）](021-deep-analysis-session-stuck.md)、[issue #265](https://github.com/closure-guo/finance_analysis_agent/issues/265)、spec `session-streaming`「图完成后工具消费端压缩缓冲明细」

## 症状（当日两实例，同为贵州茅台）

### 实例 A：17:39 会话 `d86e7742-97a`（终局 interrupted）

| 界面 | 显示 | 真相 |
| --- | --- | --- |
| Langfuse | 分析完成，报告已产出 | ✅ 图 18:07:45 完整跑完（trader 55 秒产出 watch 0.55 双向触发位决策、风控辩论、FM、报告） |
| 前端管线 UI / journal | 进度停滞 | ❌ 事件投递链 17:58 起塌方（1.4 万条/min → 64-96 条/min），trader 思维链 18:12-18:44 滴灌后 18:44:01 彻底静默；风控 R2 之后全部节点事件、报告、done 终态**永久缺失** |
| 会话列表 | interrupted | ⚠️ 误标——非当场取消。status 滞留 running，19:39:35 后端重建时启动 reconcile（`session_store.py:168` 批量 `running→interrupted`）补打的扫地标签 |

报告从未持久化（report_len=0），仅可从 Langfuse trace `3978115f` 捞回。

### 实例 B：19:39 会话 `5d38d701-ac5`（新代码容器复现，实时观测）

- 图 20:04:26 完整完成（含一次 FM 打回修订循环：risk_judge×2、report×2 代生成）。
- 后端日志「thinking 明细丢弃（队列满）」从会话第一分钟起累计 **57,600+**——`event_queue(maxsize=100)` 全程满。
- 截至 21:07（图完成后 63 分钟）：UI 仍卡「中性风控R1」，journal 以 ~72 条/min 滴灌风控辩论 thinking，node 事件停在 19:59:48 的 conservative_r1，终态仍埋在积压后面；按此速率排完 judge/FM/报告尾部约需 3-4 小时。
- 期间 UI 图视图另暴露布局 bug（风控层 R1→R2 边缺失致 R2 孤岛右列，issue #263 / PR #264 独立收口，与本 incident 无关）。

## 根因

**A 类（spec 对、代码错）**：`session-streaming` spec「图完成后工具消费端压缩缓冲明细」（场景文本：「图完成信号到达消费循环 → 队列中缓冲的 thinking_token 明细 SHALL 允许压缩丢弃……使终态 TOOL_RESULT 在图完成后的有界时间内产生」）在 #224 的实现里门槛写成了 `report_seen and graph_done`。但**真实语序下 thinking 洪峰全部在 final_report chunk 之前**（风控辩论/裁决/FM 的思考先于报告产出）——消费者要先排完数万条积压才轮到 report chunk 置位 `report_seen`，压缩恰好在本场景（消费滞后）永不生效。终态被推迟数十分钟到数小时，会话滞留 running 直到重启被 reconcile 补打 interrupted。

**测试盲区**：既有场景 D 的测试流把 final_report 放在积压**之前**，恰好绕开真实语序——绿了，但没测到 spec 描述的场景本身。

**积压放大器（子项挂账）**：`_background_consume` 每 0.5s 全量序列化累积的 `_nodeTimelines`（6 万+ chunk → MB 级 JSON，O(n²) 写放大），生产环境把泵限速到 ~1.3 事件/s，是 thinking 洪峰形成数万级积压的根本放大器。#224 治了「终态落库的可靠性」（瞬断重试），没治「排序」（终态排在 thinking 之后）与「吞吐」（量级失配）。

## 修复（PR #266）

graph_done 首次观测时**一次性快照**剩余积压（此刻生产者已停，qsize 即精确积压；只能快照一次——压缩路径无 await，泵一压缩就追平生产者使 qsize 回落，运行时反复读会振荡，实现过程中实测踩过），超出 `TAIL_KEEP_MAX=512` 的 thinking 立即压缩丢弃（`tail_thinking` 计数），保留有界尾部走慢路径；`report_seen` 后的尾部维持无条件压缩。新增场景 E：final_report 前压 3000 条积压 + sink 闸门注入慢消费时序（测试环境全链路内存速度、泵与生产者 1:1 交错使 qsize 永不自然累积）。

## 判别与运维

- **三源判别法再验证**：Langfuse 根 span = 图真实进度；session_events journal = 中继进度；`status=interrupted` 但 journal 无对应终态事件 = 重启 reconcile 补打（非当场取消）。
- **红线再验证**：重建/重启后端前必查 running 会话——实例 B 若在排空前重启，报告永久丢失（未落库）。本例处置：放任自然排空（预计午夜前后），或接受丢失从 Langfuse 捞回。
- fast path（PipelineRunner）不受此病影响：pending 缓冲有 512 上界 + trim，终态走 publish_terminal 独立路径。**只有 ReAct 路径的 chunk_queue 无界**。

## 教训

- 「图完成」依赖事件链端到端送达才对用户成立——037 修了落库可靠性，排序与吞吐没修，同族病第三次发作（021 → 037 → 039）。修复方向在 037 就写明（「终态事件旁路」），但实现时门槛条件与真实语序不匹配，且测试用了与生产相反的流序——**复现测试的输入语序必须取自生产 trace，不能手造顺排流**。
- 观测数据会撒谎的再例：实例 B 期间「队列满丢弃」计数持续增长看似系统活着，实际终态已注定排队数小时；必须对时三个观测源才能定位。
- 条件门槛的自反性检查：「以消费到 X 为前提做优化」类条件，在「消费滞后」这个优化目标场景下恰好失效——设计时要用目标场景的极端形态（积压最大时）走查一遍门槛是否可达。
