# Proposal: add-horizon-semantics-to-decision-layer

## Why

系统的正式考核口径是 **T+20 个交易日相对沪深300 超额收益（±2% 中性带）**（decision-outcome spec「决策落库/事后行情追踪」、`docs/evals/metrics.md` §1.9），但周期语义只存在于结算/评估层——决策层 prompt（trader/risk_judge/fund_manager）与分析师 prompt 均未声明考核窗口，Agent 不知道自己在为什么期限下注。后果：基本面层的一年期长线论据（估值回归、产能释放）可直接充当买入主因，与 T+20 考核系统性错位；分析师论据没有兑现期元数据，Trader 只能靠即兴判断做论据-周期匹配。

## What Changes

- trader / risk_judge / fund_manager 提示词注入**周期契约段**（共享片段、三层同源）：决策按 T+20 个交易日考核的语义；buy 决策 MUST 给出 20 个交易日内可兑现的催化剂/兑现路径；兑现期 >20 交易日的长线论据 MUST NOT 作为买入主因，仅可作安全垫或否决项；watch/hold/sell 语义不变
- 分析师 claim 登记增加 **`horizon_hint` 可选元数据**（short=≤5 交易日 / mid=6–20 交易日 / long=>20 交易日）：未申报置 null，计覆盖缺口不判 FAIL（沿用 metric_name/direction 既有降级先例）；`citation.py` Claim 模型同步增加同名可选字段（pydantic 默认丢弃未建模字段，不加则申报静默丢失）
- 提示词契约测试扩展：断言决策层三 prompt 含周期契约段、分析师 prompt 含 horizon_hint 申报说明
- prompt 合入后执行 `scripts/deploy_prompts.py` 发布（prompt-deploy-consistency 既有门禁不变）

明确不做（后续独立 delta）：反转防护热度指标与降档规则（P1）、short 展示语义收敛（P2）、T+60 观测口径登记（P3）、多周期观点输出（P4，依赖 P3 数据裁决）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-prompt-contracts`：ADDED 两个 Requirement——「决策层周期语义契约」（trader/risk_judge/fund_manager 三层同源周期语义）与「分析师论据周期标注」（claim horizon_hint 申报纪律与 Claim 模型字段）

## Impact

- **prompts**：`trader.md`、`risk_judge.md`、`fund_manager.md`（注入共享周期契约段）；`fundamental_analyst.md`、`technical_analyst.md`、`sentiment_analyst.md` 及辩论者 prompt 的 claim 登记章节（horizon_hint 申报说明，覆盖范围沿用 direction 申报纪律）
- **代码**：`src/finance_agent/citation.py` Claim 模型加一个可选字段（向后兼容，存量 null 申报行为不变）；无 API/前端变更
- **测试**：`tests/` 提示词契约测试扩展 + Claim 解析兼容性测试
- **部署**：`uv run python scripts/deploy_prompts.py` 发布；eval 门禁指纹一致性验证
- **非交互类变更**：不涉及前端 UI/SSE/会话状态，不适用 E2E 门禁
