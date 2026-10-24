## Context

输入框下方当前只有一行静态免责声明。DeepSeek Harness（MIT）在相同位置放了三枚计量控件，数据来自其会话投影（`sessionStats` / `tokenUsage` / `contextPressure`）。本项目没有这些投影：交互式深度分析的 provider usage 只在 cohort 记账时经 `usage_collector` 收集（`nodes/_llm_utils.py`），会话表不落库 token；五层管线也不经过 `ContextBudget`，没有运行期上下文用量。因此本次除了 UI，还必须补一条最小记账链路。

## Goals / Non-Goals

**Goals:**
- 三个控件的显示与交互对齐 DSH 的信息密度与尺寸（不照搬其 CSS token，复用本项目设计变量）。
- Token 与会话统计是**真实值**；上下文已用是基于真实峰值 prompt tokens 的占用率。
- 数据缺失时各自隐藏，绝不显示 0 冒充。
- 免责声明从输入栏移除后，报告页脚与导出产物仍保留。

**Non-Goals:**
- 不做 DSH 的缓存命中率（provider 是否返回 cache usage 未统一，先不展示）。
- 不做逐轮 token 用量行、装配期上下文分段（系统提示/工具/消息三段）——我们拿不到可靠分段。
- 不改变五层管线的节点契约与 SSE 事件结构。
- 不引入 DSH 的 `@deepseek-ai/*` 依赖。

## Decisions

### D1: 独立 stats 端点，不复用会话详情

新增 `GET /api/sessions/{id}/stats`，返回轮次、步骤、耗时、token、上下文峰值。会话详情体积大（报告、图表、时间线），不适合运行中轮询；stats 是轻量只读查询。

### D2: usage 记账必须在生产线程内激活

`usage_collector` 基于 `contextvars`，而 deep 分析的 `_stream_graph` 通过 `_pipeline_executor` 在独立线程运行——在 async 函数里 `with usage_collector()` 不会传播进线程。因此把收集器设在 `_run_graph()` 内部（该线程正是 LangGraph 执行 LLM 调用的线程），线程结束时落库。LangGraph 的并行分支会 `copy_context`，累加器实例级锁保证并发 fan-out 不丢计数（既有 `UsageAccumulator` 已具备）。

**quick 模式例外**：quick 经 AG-UI 跑 harness `Agent`，LLM 调用不经过 `call_llm_streaming`，`usage_collector` 收不到任何事件。用量改由 Agent 在每次调用后累计到 `usage_*` 属性（loop 内已有 `chunk.usage`），AG-UI 与 SSE 两条收尾路径调用 `_persist_agent_usage` 落库；`usage_calls == 0`（stub / 无 usage）不写库。deep 模式下 ReAct 编排层与管线层各自累加，互不覆盖。

### D3: 两个新累加语义

- 会话 token：多次分析**累加**（`update_session_usage` 做 `+=`），因为「会话累计用量」是用户要的。
- 上下文峰值：全会话取**最大值**（`context_peak_tokens = max(旧值, 本次峰值)`），表示「这个会话曾经把窗口用满到什么程度」。单次峰值由 `UsageAccumulator` 新增 `max_prompt_tokens` 记录。

### D4: 上下文窗口来源

前端取激活 profile 的 `contextLength`；未设置时回退 128000（后端 registry 静态默认）。窗口与峰值任一缺失则隐藏控件。百分比钳制 100%，面板显示真实峰值（可超过窗口，避免把溢出藏起来）。

### D5: 隐藏规则沿用 DSH

控件各自渲染门禁：`steps===0` 隐藏会话统计、`tokens_total===0` 隐藏 Token、`context_peak===0` 隐藏上下文。旧会话（无新列）自动落到「无 token」分支，不需要回填。

### D6: 免责声明迁移

从 `App.tsx` 的 `ChatInputBar` 删除声明行；报告消息页脚已有同一句（`frontend` spec「免责声明」场景），导出服务也统一追加（`report-export` spec）。不新增文案。

## Risks / Trade-offs

- [轮询开销] 运行中每 2s 拉 stats → 仅 `running` 状态轮询，空闲时只在会话切换/分析结束各拉一次；stats 是单表 SELECT + 事件计数，代价可忽略。
- [steps 口径] 用事件日志 `node_start` 去重计数，重试会重复计同一节点？→ 去重按 `node_id`，重试同节点仍算 1 步，符合「步骤」语义。
- [旧会话数据缺失] 三个控件可能只剩一两个 → 符合 D5；不做估算填充。
- [contextvar 传播盲区] 若未来有调用绕过 `_run_graph` 直接起线程，记账会静默为 0 → stats 端点返回 0，前端隐藏，不谎报；在代码注释中标注约束。
- [测试环境] `TESTING=1` 的 stub 不产生 usage，记账恒 0 → E2E/单测断言走「无数据隐藏」，真实数值由后端单测以注入 usage 覆盖。

## Migration Plan

- SQLite 幂等迁移：`ALTER TABLE sessions ADD COLUMN ...`（沿用 `init_db` 既有 `contextlib.suppress(OperationalError)` 模式），旧库启动自动补齐，默认 0。
- 回滚：删列不必要；代码回退后新列被忽略，无副作用。
- 发布顺序：后端先上（旧前端无感），前端随后读新端点；端点 404/500 时前端整体隐藏计量条（不阻断聊天）。

## Open Questions

- 是否在后续迭代把「缓存命中」补进 Token 面板，取决于 gateway 是否统一透出 `prompt_cache_hit_tokens`。本次不做。
