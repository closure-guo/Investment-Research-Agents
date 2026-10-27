# Proposal: update-harvest-factor-category

## Why

「分桶保种」机制（`BUCKET_TOP_K=10`，按 `category` 分桶独立保留 top-k）对月批提案链路生效，但**收割链路把所有 RD-Agent 候选硬编码 `category="外部"`**（`harvest.py` 的 `DEFAULT_CATEGORY`）。结果：外部因子的 10 条上限是**全局单桶**——量、波动、反转、量价交互全部挤一个桶互相末位淘汰（2026-10-22/10-25 两次 `BUCKET_QUOTA` 淘汰已是同桶互挤），分桶保种名存实亡，池多样性只靠闸④ |ρ|>0.7 兜底（能挡重复、挡不住同族不同式挤占）。月批侧专门做的分桶存量回喂（`_bucket_stats` 提示 LLM 均衡产出）对收割链路完全不起作用。

## What Changes

- 收割入库的 `category` 从硬编码 `"外部"` 改为按**候选机理**映射到固定桶集：**量与流动性 / 波动率 / 动量反转 / 量价交互 / 外部-其他**（兜底）。
- 映射为**确定性关键词表**（对 RD-Agent 候选的机理标签——hypothesis 中 `[Volume/Liquidity Factor]` 类标签——与因子名匹配），全部未命中落兜底桶；分类失败 MUST NOT 影响准入（分类只决定桶归属）。
- 存量 `category="外部"` 因子按同一映射一次性重分类（幂等可重跑）。
- `source="external"` 语义不变；闸④冗余闸、五道闸口径零改动。

## Capabilities

- **Modified Capabilities**: `factor-proposal`（MODIFIED 需求「外部研发供给接入」——收割通道 category 语义）

## Impact

- 影响披露：外部 active 上限从单桶 10 变为至多 5 桶 × 10 = 50（分桶保种的本意——同族内卷仍受桶内 top-k 约束，跨族不再互挤）；打分池随之扩大（L2 权重按 IC 均值归一自适应，无 ranker 改动）。
- `src/finance_agent/factors/harvest.py`（`DEFAULT_CATEGORY` → 桶集映射函数）
- 存量迁移脚本（12 条 `category="外部"` 因子重分类，幂等）
- `src/finance_agent/factors/propose.py` 的 `_bucket_stats` 回喂自动获得细分桶存量（零改动生效——生成侧回喂将显示分桶余量，引导跨族产出）
- 无前端交互变更（桶名经既有列表接口透出）
