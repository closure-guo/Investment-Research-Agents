# 因子收割链路 UI（add-factor-harvest-ui）

## Why

收割批自 2026-10-22 起改为「每天 15:55、单次内部串行多批直到清空待处理」，但页面只能看到研发侧会话与看护状态：
队列积压、当前批次、本轮入库、各闸拒收分布、最近入库/拒收全部不可见，排障只能命令行翻
`harvest_state.json`、`candidates.json` 与准入账本（本轮归因实操即如此）。需要一个只读视图回答三件事：
**今天这轮跑到哪了 / 损耗在哪 / 最近发生了什么**。

## What Changes

- **新增 capability `factor-harvest-ui`**：
  - 只读端点 `GET /api/factor-research/harvest`：`job`（状态/调度/下次触发）、`progress`（批次/已处理/
    剩余/本轮入库与拒收/ETA）、`funnel`（候选→已处理→到闸→入库 + 按闸拒收分布）、`recent_admitted` /
    `recent_rejected`；数据源为状态文件、候选文件、因子库账本与 ops 运行行，**零写入、零业务复制**；
  - 缺失/损坏 → `available=false`，各块降级为 `null`，MUST NOT 编造数字。
- **frontend ADDED「因子收割链路」区块**（`/factor-research` 页内）：进度条 + 四段漏斗 + 拒收分布 +
  最近入库/拒收台账；复用页面 10s 轮询；区块级失败/空态不拖垮整页。
- **非目标**：任何写操作或批次手动控制；历史趋势图/多日对比；替换 `/ops` 日批页。

## Impact

- 代码：`src/finance_agent/api.py`（端点 + 派生）、`frontend/src/pages/factors/FactorResearchPage.tsx`、
  `frontend/src/types.ts`、`frontend/src/lib/factorResearch.ts`、对应测试与 E2E spec。
- 数据：只读 `data/factor_research/harvest_state.json`、`candidates.json`、`data/factor_library.db`
  账本、`factor_harvest_daily` 运行行；不新增库表、不写文件。
- 行为：只读展示；无控制入口；页面轮询频率不变（10s）。
- 设计依据：`docs/superpowers/specs/2026-10-22-factor-harvest-ui-design.md`。
