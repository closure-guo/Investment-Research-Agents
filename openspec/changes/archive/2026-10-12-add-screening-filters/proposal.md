# Proposal: add-screening-filters

## Why

- 自动选股的第一步是把 5000+ 标的过滤到可交易池，这批规则（ST/停牌/新股/流动性）若不先落成确定性纯函数，后续 LLM 粗筛与深度分析会在垃圾标的上浪费 token 并引入决策风险。
- 同样的约束（涨跌停、ST、停牌）即将出现在交易执行层的风控门禁中，两处各自定义必然漂移——需要单一来源。

## What Changes

- 新增 `screening/filters.py` 纯函数模块：输入某交易日快照全量记录，输出通过/剔除结果及逐标的剔除原因码。
- 过滤规则（默认值可配置）：剔除 ST/*ST、停牌、上市 < 60 交易日、一字涨跌停、近 20 日日均成交额低于阈值（默认 5000 万元）。
- 规则常量定义于单一模块 `screening/constraints.py`，后续 `risk_gate`（交易执行 delta）必须 import 复用，禁止复制。

非目标（Out of scope）：

- 不做因子打分排序（属 `add-screening-ranker`）。
- 不做 LLM 粗筛（属 `add-screening-candidate-llm`）。
- 不实现交易执行门禁本体。

## Capabilities

### New Capabilities
- `stock-screening`: 自动选股四层漏斗（本 change 落地 L1 硬过滤层与共享约束常量）。

## Impact

- **后端**：`src/finance_agent/screening/filters.py`、`screening/constraints.py`（新增）。
- **测试**：每条过滤规则正反用例；剔除原因码枚举完整性单测；边界值用例（上市第 59/60/61 天）。
- **验证**：对 2026-09 全部交易日跑过滤，人工抽 50 个被剔除标的核对原因码正确，报告落 `tests/validation/`。
