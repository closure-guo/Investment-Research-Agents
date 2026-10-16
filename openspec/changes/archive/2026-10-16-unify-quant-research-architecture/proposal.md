# Proposal: unify-quant-research-architecture

## Why

对标分析（`docs/evals/2026-10-15-量化投研设计文档对标与差距分析.md` + 目标架构图）确定量化研发层的架构契约：**LLM 做研发（多供给、可插拔）、统计做裁判（五道闸，裁决权唯一）、纪律做执行**。当前实现与该契约有三处缺口，且已被真实数据实测暴露（2026-10-15 晚）：

1. **准入面板深度结构性不足**：`_PANEL_LOOKBACK_DAYS = DECAY_WINDOW+30 = 90` 交易日复用为准入面板——20 日窗因子的热身期缺失（19/90≈21% + 北交所缺席≈6% > `MAX_MISSING_RATE=0.2`）**结构性恒拒于闸①**（builtin 0/5 入库的直接根因；170 日深面板实测 turnover_proxy \|ICIR\|=0.341 过闸②）。
2. **回填取数缺「成交额」列**：`fetch_symbol_history_raw_qfq` 五列契约无 amount → 回填历史 amount 恒空 → `size_proxy`（ts_mean(amount,20)）在回填区 100% 不可计算（实测）。
3. **外部研发供给（RD-Agent/Qlib 生态）无接入契约**：目标架构的第三条供给线（外聘研究员）没有进闸通道与真源约束——外部因子库（其 SOTA 库）不得成为第二因子真源、外部回测结论不得替代闸口读数，这些边界需要落成规格。

## What Changes

- **factor-proposal**（ADDED「外部研发供给接入」）：外部候选以合法 DSL 表达式进入与本地提案**完全相同**的五道闸，`source=external` 标注；不可表达拒收留痕；因子库保持唯一生产真源，外部结论不替代闸口读数。
- **factor-library**（ADDED「准入面板深度下限」）：准入面板独立常量、下限 150 交易日（不影响衰减监控面板口径）；快照历史不足下限时准入任务 fail loud。
- **market-snapshot**（ADDED「历史回填补齐成交额」）：回填补取数输出并落库 amount 列（新浪日线原生含 amount），缺失视为取数缺口不落 NULL 直通。

Deferred（**不在本 delta**，另行立项，见 design.md）：财务披露日 PIT、回测用含退市股池、文本 available_at 快照库、G2 分层回测闸、G4 扣费回测闸、决策层消融实验。

## Capabilities

- **Modified Capabilities**: `factor-proposal`（+1 requirement）、`factor-library`（+1 requirement）、`market-snapshot`（+1 requirement）

## Impact

- 代码：`factors/monitor.py`（面板构造拆分准入/监控两口径或引入 `ADMISSION_PANEL_LOOKBACK_DAYS`）、`data/akshare_client.py::fetch_symbol_history_raw_qfq`（+amount 列与单位归一）、外部候选导入入口（ops 任务或脚本，接受 DSL 表达式候选）。
- 数据运维：扩展回填补齐（断点续跑）+ 既有回填区补 amount；回填完成前准入任务按新深度口径 fail loud（**上线顺序：先回填后切深度**）。
- 验证：RD-Agent spike（WSL2 环境 + `fin_factor` 单循环 + 表达式翻译 + 过闸读数）作为外部供给线投用前的实证关。
