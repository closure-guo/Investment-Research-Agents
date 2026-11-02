# Design: add-microcap-pool-ui

## Context

小票通道写侧已完备：16:45 `microcap_screening_daily` → `run_microcap_lane_daily`（pipeline.py:660-714）落 `pool='microcap'` 批次行 + 池行，批次行带 `frontier_gate`（`ready` | `observation_only:missing=<delist_pool,regime_data>`，缺失要点码逗号分隔）与 `exec_constraints`（`{"suggested_single_cap": 0.05, "slippage_tier_bump": 1}`）。**store 层已全量档位参数化**：`list_selection_pool` / `latest_successful_batch` / `latest_screening_batch` / `list_batches` 均已收 `pool` 参数并校验 `pool ∈ POOLS`（store.py `list_batches` docstring 甚至预留「小票通道读侧显式传 pool='microcap'」）。缺口只在两处：① API 层两个端点不暴露 `pool` 参数、`latest_successful_pool`（pipeline.py:729-738）硬编码主档；② 前端 `/screening` 页无档位入口。

前端现状：`ScreeningPage.tsx`（403 行）手写标题栏 + 滚动区，精选池区块（`data-testid="screening-pool-card"`）为主体；`GET /api/screening/pool` 一次取数，队列徽标 2s 轮询自停；无 react-query，页面内 `fetch` + useState/useEffect。设计系统：`design/tokens.ts`（五态 BADGE_TOKENS）+ `components/shells/`；本页是旧式手写布局，不强行迁移到 PageShell。

## Goals / Non-Goals

**Goals**
- 两个读端点暴露 `pool` 参数（缺省 main、非法 422、microcap 空态不报错）；池响应透传 `frontier_gate` / `exec_constraints`。
- 选股页档位切换（主档/小市值），小市值视图 = 同构池表 + 门禁横幅（两态可区分、缺失要点映射可读文案）+ 执行约束披露 + 「仅观察、不产生订单」声明 + 空态。
- v1 只展示：不渲染小市值深度分析按钮，不动 `pool_analysis` / `AnalyzeQueue`。

**Non-Goals**
- 不改写侧落库、调度、`evaluate_frontier_gate` 语义。
- 不把 microcap 池纳入自动池分析（16:22 job）、研报触发、运营日报。
- 不新建独立页面/导航入口（观察通道不加导航负担）。
- 不迁移 ScreeningPage 到 PageShell（既有页面样式保持）。

## Decisions

### D1：`pool` 参数用 FastAPI `Literal["main","microcap"]` 查询参数
FastAPI 对 Literal 违例自动 422（`{"detail":[...]}`），与 spec「非法值 422」直接对齐；不手写校验分支。既有端点风格是手写钳制（如 batches 的 limit），但那是不报错的钳制语义；档位是枚举语义，422 才符合 spec。缺省值 `main` 保证既有消费者与既有测试零变化。

### D2：`latest_successful_pool` 加 `pool` kwarg 而非新函数
`latest_successful_pool(*, db_path)` → `latest_successful_pool(*, db_path, pool=POOL_MAIN)`，内部透传给 `latest_successful_batch` 与 `list_selection_pool`（两者 store 层已支持）。`_screening_pool_payload(queue_snapshot)` 同步加 `pool` 参数；缺省 main 时所有返回值与现状逐字一致。**不选**新开 `/api/screening/microcap/pool` 端点：参数化复用同一载荷组装与 queue 快照逻辑，避免两份 payload 代码漂移（fallback notice / factor_signal 块逻辑只有一份）。

### D3：`frontier_gate` / `exec_constraints` 从「最近成功批次行」取，挂在池响应顶层
`latest_successful_batch` 的 SELECT 已含两字段，`_screening_pool_payload` 直接从 `successful["batch"]` 取值挂顶层（无成功批次 → `null`）。**不选**逐池行携带：门禁与约束是批次级语义（spec 明言「记录字段」），行级冗余会制造假聚合问题。

### D4：前端档位切换用受控 state + 序号防竞态重取 + 双视图复用同一池表组件
`poolTier: 'main' | 'microcap'` useState，切档即以新参数重取 `/api/screening/pool?pool=<tier>`（沿用页面既有 fetch 模式；`loadSeqRef` 自增序号丢弃迟到的旧档响应，防快速切换乱序覆盖；`setData(null)` 走既有加载态，不引入每档缓存）。切档即 `stopPolling()`（队列徽标轮询属主档分析流）。小市值特有披露为 microcap 档专属区块：门禁横幅（按 `frontier_gate` 前缀分流 `ready` / `observation_only:missing=`，缺失要点码 → 可读文案映射 `delist_pool→退市股池未就绪`、`regime_data→拥挤度数据未就绪`，未知码原样列出）、约束行（标注「记录字段，非生效约束」）、观察声明。池表列同主档（复用现有表格渲染），testid 以档位中缀区分：main 保持既有逐字（`screening-row-*` / `screening-queue-badge-*` / `screening-stock-name-*` / `screening-score-*`），microcap 为 `screening-microcap-row-*` 等——既有 E2E 断言零漂移，新档选择器无歧义。**不选**路由级 `/screening?pool=microcap`：页面无 query 同步先例，state 更贴合现有代码；URL 可分享性对观察通道非目标。

### D5：类型契约单点扩展
`types.ts` 的 `ScreeningPoolResponse` 加 `frontier_gate: string | null` / `exec_constraints: Record<string, number> | null`；新增 `MicrocapGateInfo`（要点码→文案映射）放页面同目录，与 `design/tokens.ts` 的 BADGE_TOKENS 对接门禁徽标态（ready=active、observation_only=degraded）。

## Risks / Trade-offs

- **[既有契约漂移]** 缺省路径任何字段增删都会破坏「逐字一致」→ 后端测试先锁定 main 档响应快照（既有 `tests/test_api_screening.py` 断言 + 新增缺省不变用例），TDD 红先绿后。
- **[frontier_gate 是自由串]** 解析依赖前缀约定（`observation_only:missing=`）→ 前端只做前缀分流 + 未知码原样列出（spec 已写明），不猜语义；常量 `FRONTIER_GATE_*` 从 pipeline 导出复用，不复制字面量。
- **[microcap 空态数据难造]** E2E 需要 microcap 批次种子 → 复用 `reset_screening_db`（store.py 已有，注释明言「E2E screening_reset 造数用」）经 TESTING 种子通道落 microcap 批次行 + 池行。
- **[观察通道误读为可交易]** 门禁横幅与声明文案按 spec 逐字落，E2E 断言「仅观察、不产生订单」文案可见；约束标注「建议约束」。

## Migration Plan

纯增量：新增查询参数 + 新增响应字段（null 兼容）+ 新增 UI 区块。无迁移、无回滚风险——回退即还原两个端点签名与页面区块。部署顺序无约束（前后端可独立发布，缺省路径互不依赖新字段）。

## Open Questions

（无——UI 形态与 v1 范围已按用户会话确认：页内切换 + 只展示。）
