# Proposal: update-decision-buy-gates

## Why

2026-11-06~07 三轮消融实验（零买入诊断：581 次会话 0 buy）实证决策链存在两个**独立**的买入闸门，均由规范与提示词合谋锁死：闸门 A=入场时点（周期契约段把「20 日内催化剂证明」设为 buy 义务、分析师 horizon_hint 括注压制长线论据）；闸门 B=止损几何（trader 在 ATR 参考带内自由选边 + 风控以「止损窄于噪声」否决方向且无 (止损,仓位) 联合修正路径）。B' 全拆变体在冷却版完美案例出 buy、窄影线案例回 watch，证明拆净两闸门后系统「该买的能买、不该买的仍不买」。本变更把实验变体中的解锁语义按实验证据改写为生产契约（保留 T+20 结算口径本身，只拆除其决策门槛用法）。

## What Changes

- 周期契约段改为纯结算口径声明：废除 buy 的催化剂证明义务与「长线论据禁作买入主因」禁令，改为窗口错配披露义务（**BREAKING**：决策层提示词行为契约反转）
- 新增决策层否决纪律：方向否决仅限致命白名单（造假/退市/流动性枯竭/无法交易/关键数据不可信）；执行参数缺陷 MUST 走「放宽止损 × 等比缩仓」联合修正或显式「可执行参数缺口」，禁止默认 watch
- trader 新增止损锚定规则：止损 = max(2×日波动, 结构位) + 仓位预算化，禁止带内无视噪声自由选边
- 证据均衡带收窄：增量申报义务仅约束方向翻转，首次建仓/维持前向以价位逻辑为充分依据（`execution/hysteresis.py` 同步收窄）
- 分析师 horizon_hint 括注去除决策判语（「long 档论据不能支撑买入主因」），保留申报义务本身
- confidence 分档去锚定：中等档废除单一示例值 0.55，改区间化 + 反锚定纪律

**不在本变更范围**：四态决策枚举（INSUFFICIENT_DATA）——涉及前后端 schema 与交互面，另立 delta；±2% 结算口径本身（decision-outcome 结算语义不变）。

## Capabilities

- **Modified Capabilities**: `agent-prompt-contracts`（4 个 requirement）、`decision-hysteresis`（1 个 requirement）

## Impact

- `src/finance_agent/prompts/contracts.py`（HORIZON_CONTRACT 单一来源）+ trader/risk_judge/fund_manager 三份 md 同步
- 4 份分析师 md（括注）
- `src/finance_agent/execution/hysteresis.py`（均衡带收窄）
- `tests/test_prompt_contracts.py`、滞回相关测试
- 提示词发布走 `scripts/deploy_prompts.py`（prompt-deploy-consistency 契约不变）
- 验收门：合成回归四案例（v3.1 完美 / v4 冷却 / v5 窄影线 / defect）× 新生产提示词——v4 必 buy、v5 必 buy（或 buy+显式缺口）、defect 必 sell、v3.1 不得引用催化剂/均衡/止损不可用类理由
