# Design: update-harvest-factor-category

## Approach

**确定性关键词映射**（不用 LLM 分类）：RD-Agent 候选的 hypothesis 首段带方括号机理标签（如 `[Volume/Liquidity Factor]`、`[Volatility/News-Burst Factor]`、`[Price-Volume Co-movement Factor]`、`[Intraday Range Factor]`），名称亦有 `vol`/`volume`/`range`/`corr` 等词根。桶集五桶与关键词表（初版，按存量 12 条因子实测标签校准）：

- **量与流动性**：volume / liquidity / vol ratio / amount / amihud / 换手
- **波动率**：volatility / range / std / dispersion / 波动
- **动量反转**：momentum / reversal / reversal / 反转 / ret
- **量价交互**：price-volume / price volume / corr(price / pv / co-movement
- **外部-其他**：兜底

匹配顺序按桶序首中即停（映射表是有序结构，匹配结果可审计）；`hypothesis` 机理标签优先、因子名次之。映射表放 `harvest.py` 模块级常量（与 `DEFAULT_CATEGORY` 同址），测试对存量 12 条的期望桶归属钉死。

**存量迁移**：一次性脚本（`tests/scripts/` 或 `scripts/`）对 `category="外部"` 行按同一映射函数重写 + `factor_events` 落 `status_change` 迁移事件（from/to category 记 detail）；幂等（仅重写仍为"外部"的行）。

**打分池扩大**：外部 active 上限 10 → 至多 50。闸④冗余闸（|ρ|>0.7）继续拦同族近重复；L2 权重按 Σ|ic_mean| 归一自适应。`_bucket_stats` 回喂零改动自动显示分桶余量，生成侧获得跨族均衡信号。

## Alternatives Considered

- **LLM 在转译调用里顺带分类**：增加载荷合同面（分类失败影响转译成功路径）、不可复现、每次收割多付 token；机理标签是结构化输入，确定性表可全覆盖存量形态。被否。
- **桶集开放（沿用月批自由 category）**：月批 LLM 的自由 category 已存在碎片化风险（「动量」vs「动量反转」即两桶）；收割侧固定闭集保证桶数可控、top-k 语义稳定。月批侧的自由 category 收敛是另一个议题，不在本 delta。
- **只给外部桶加族内配额（不动 category）**：等于在外部桶内自建一套次级 top-k，与分桶保种既有机制重复且不透出（页面/回喂只见单桶）。改 category 复用全部既有机制。

## Risks

- **关键词误分类**：机理标签自由文本可能描述跨族因子（如 volume-weighted reversal）。兜底桶保底 + 分类只影响桶归属（不影响准入/打分），错置代价有限；错置可经 retired+重提出清（与因子生命周期同构）。
- **池上限扩大的过拟合面**：50 上限下同族仍受桶内 top-k 与闸④约束；且 add-factor-post-admission-review 的入库后复核提供后验出清。两个 delta 互补。
- **迁移后两次既有 `BUCKET_QUOTA` 淘汰的历史事件与新桶语义不一致**：历史事件是事实记录不改写（ADR 纪律：不静默改写历史）。
