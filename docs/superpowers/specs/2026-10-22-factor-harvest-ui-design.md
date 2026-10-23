# 因子收割链路 UI（/factor-research 扩展）设计

- 日期：2026-10-22
- 状态：方案 A 已确认（实时进度 + 漏斗 + 台账）
- 关联：`openspec/changes/add-factor-harvest-ui/`（待建 delta）、`docs/project-workflow.md` §3

## 1. 目标

在现有 `/factor-research`（研发监控）页下方增加「收割链路」区块，回答三个问题：

1. **今天这轮跑到哪了**：第几批 / 已处理 / 剩余 / 预计完成；
2. **链路各环节损耗在哪**：候选 → 已处理 → 到闸 → 入库的漏斗，以及按闸分布的拒收；
3. **最近发生了什么**：最近入库（表达式 / ICIR / 时间）与最近拒收（闸 / 理由）。

## 2. 非目标

- 不做任何写操作、批次手动控制（研发侧 restart 保持原样）；
- 不做历史趋势图/多日对比（本轮只当前快照 + 最近明细）；
- 不替换 `/ops` 日批页，不新增路由与侧栏入口。

## 3. 后端契约：`GET /api/factor-research/harvest`（只读、零写）

```json
{
  "available": true,
  "job": {
    "status": "running|ok|failed|null",
    "started_at": "ISO|null",
    "finished_at": "ISO|null",
    "schedule": {"day_of_week": "*", "hour": 15, "minute": 55, "timezone": "Asia/Shanghai"},
    "next_fire_time": "ISO|null"
  },
  "progress": {
    "batches_done": 2,
    "batch_limit": 12,
    "processed": 440,
    "pending": 380,
    "admitted_in_run": 3,
    "rejected_in_run": 65,
    "started_at": "ISO|null",
    "eta_s": 12600
  },
  "funnel": {
    "candidates": 820,
    "processed": 440,
    "untranslatable": 225,
    "transcription_error": 7,
    "reached_gates": 208,
    "admitted": 7,
    "rejected_by_gate": {"0": 4, "1": 5, "2": 105, "3": 0, "4": 16, "5": 6}
  },
  "recent_admitted": [
    {"expression": "ts_corr(close,volume,10)", "factor_id": 7, "created_at": "ISO", "icir_60d": 0.0}
  ],
  "recent_rejected": [
    {"expression": "sub(div(close,ref(close,20)),1)", "gate": 2, "reason": "WEAK_ICIR", "at": "ISO"}
  ]
}
```

口径与来源：

- `funnel.*` 来自 `harvest_state.json`（`processed`）与 `candidates.json`（`candidates`）；`reached_gates = admitted + rejected`；`rejected_by_gate` 只统计 `status=rejected`；
- `recent_admitted` 来自因子库 `factors`（按 `created_at` 倒序 5 条）；
- `recent_rejected` 来自状态文件（按 `at` 倒序 10 条，`detail` 不返回，前端只展示闸/理由）；
- `job` 来自 ops 任务注册与运行历史（`factor_harvest_daily`）；
- `progress.batches_done` = 本次运行（`job.started_at` 之后）账本中不同 `batch_id` 数；
- `progress.admitted_in_run` / `rejected_in_run` = 本次运行开始后的账本入库/拒收条数（同一时间窗口径）；
- `eta_s` = `pending / (processed / elapsed_s)`，不足一个批次或 elapsed=0 → `null`；
- 文件缺失/损坏 → `available=false`，各块降级为 `null`，**不编造数字**。

## 4. 前端（`/factor-research` 页内新区块）

三个子块，自带 `data-testid`：

1. `harvest-progress`：队列消化进度条（`funnel.processed / funnel.candidates`，**仅运行中**）、
   批次进度（`batches_done/batch_limit`，仅运行中）、已处理/剩余、预计完成时刻、任务状态徽标；
2. `harvest-funnel`：横向漏斗（候选 → 已处理 → 到闸 → 入库），每段显示数值与相对候选的百分比；
3. `harvest-ledger`：最近入库与最近拒收两列（窄屏堆叠）。

交互与状态：

- 复用页面已有 10s 轮询节拍，单独发请求；区块内失败只显示本区块错误并可重试，不拖垮整页；
- `available=false` → 区块级「收割数据未接入」空态；
- 任务从未运行（`job=null` 且 `processed=0`）→ 「收割尚未运行」空态；
- 已收尾（`job.status=ok`/`failed`）→ 不渲染进度条与 `/batch_limit` 分母，改本轮汇总（批数 · 入库 ·
  拒收）+ 队列剩余归属「待下次 …自动跑」：候选文件由 RD-Agent 持续追加，全局消化率在收尾后仍会回落，
  渲染即读成「没跑完」（2026-10-23 T8）；
- 所有数字来自后端，前端不做任何估算（除百分比展示）。

## 5. 测试

- 后端 `pytest`：空态 / 运行中 / 已完成 / 状态文件损坏四类 fixture，断言键与降级行为；
- 前端 `vitest`：三块渲染、空态、错误态、百分比计算；
- E2E：真实后端只读断言（**不 mock 业务接口**），容忍数字变化，只断言区块存在与结构。

## 6. 验收

- `curl /api/factor-research/harvest` 返回上述键；
- 页面三块显示的数字与 `harvest_state.json` / 因子库一致；
- 端点与页面均无写操作（不触碰 data/ 与库）。

## 7. 风险

- 10s 轮询读 JSON + SQLite：数据量小（状态 ~135KB、账本 155 行），可接受；
- 页面变长：区块默认展开、可折叠，避免挤压现有监控内容；
- 任务运行中 `pending` 会随 RD-Agent 产出增长，属真实读数而非 bug。
