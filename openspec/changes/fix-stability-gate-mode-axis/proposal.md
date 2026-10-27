# Proposal: fix-stability-gate-mode-axis

## Why

「同模式滚动轴」在日批路径结构性落空（2026-10-27 生产实证）：精选池资格判定发生在**当日批次行
落库之前**（批次行与池行同事务最后落库），`days_in_top_recent` 以「读当日批次行的
`selection_mode`」定轴——该行此时必然不存在，于是每天都走「`as_of` 批次行缺模式 → 退回全轴
滚动」兜底，轴实际是**跨口径混合 5 日轴**（factor 日与 fallback 日混计）。

用 10-26 / 10-27 两天生产数据精确复现：两天池成员（000938+688521 / 600150+600176）与混合轴
口径逐一吻合，同模式口径反而对不上。当天（10-27）Top-100 中有 5 只因子口径命中 3 次的标的
（002033/600916/601868/601901/603568）因混合轴只数出 ≤2 次被挡在池外，而混合轴命中的
600150/600176 入池——因子股的门槛被流动性榜天数稀释，与 `update-stability-gate-rolling-window`
的「同模式」意图相反。该兜底条款被写进了既有 delta 文本，故须以本 delta 修正契约本身。

## What Changes

- **契约修正**：日批路径的精选池资格判定 SHALL 以批次自身已知的 `selection_mode` 定轴
  （该值在 L2 打分前即已确定），MUST NOT 依赖「读取当日批次行」、MUST NOT 因当日批次行
  尚未落库而退化为混合轴；全轴滚动降级仅保留给无法确定模式的带外调用（存量数据查询）。
- **实现修复**：`days_in_top_recent` / `eligible_for_selection` 增加显式 `mode` 入参；
  `pipeline.run_screening_batch` 精选池计算处传入当日 `selection_mode`。缺省（不传）保持
  既有「批次行查找 → 全轴降级」路径，带外兼容不变。
- 不做：批次行先落 running 态带模式（撞「僵尸 running 行」家族问题 #455 与「批次 ok 与池
  同生共死」单事务不变量）；当日已落库池行的历史重述（次批自动按正确口径重算，可选手动补跑）。

## Capabilities

### Modified Capabilities

- `stock-screening`：MODIFIED「在榜稳定性门槛」——新增轴模式来源条款（日批路径显式定轴，
  混合轴降级在日批路径 MUST NOT 发生）；新增 2 个场景（日批同模式轴生效 / 带外降级保留）。

## Impact

- 代码：`src/finance_agent/screening/store.py`（两函数签名加参）、
  `src/finance_agent/screening/pipeline.py:459`（调用点传当日 `selection_mode`）；单一调用方，
  影响面收敛。
- 测试：`tests/screening/test_screening_store.py` 新增红测（批次行未落库 + 显式模式 → 同模式
  轴；混合轴稀释回归）；既有「无批次行降级」用例语义不变（默认路径保留）。
- 行为：修复部署后次一交易日池成员自动按正确同模式口径（10-27 形态下应为 5 只因子票而非
  600150/600176）；无需回填，池按 `(trade_date, pool)` 原子替换天然自愈。
- **归档顺序依赖**：`update-stability-gate-rolling-window`（同 requirement，已合并待归档）
  须先归档；本 delta 归档时按 sync 后主规范全文重写 MODIFIED 块（既有 drift 陷阱处置法）。
