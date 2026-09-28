# Proposal: update-decision-integrity-gates

## Why

2026-09-28 18:00 cohort 跑批 10 份报告的逐份审查发现：决策层的结构化缺口会以「报告内容失真」的形式直达终审产物——触发价幻觉（600845 写 18.8，引用校验过的真实 MA60=18.066）、空洞触发条件（601066「站上 22.61」在现价 23.03 下已满足）、FM 在方案结构不完整（reeval_triggers 缺失）时仍批准并宣称「执行安排完备」（601818）、非法仓位档位字面量 `none` 原样透传渲染（600515）、FM 复述上游指标失真（002916「KDJ/DCF柱」、600015 把价格 6.12 混进降幅阈值）与置信度无解释漂移（600515 FM 0.8 vs 裁决 0.55）。现有校验只覆盖分析师 claim 与 buy/sell 价位关系，决策层自身的参数与 FM 复述层没有任何校验兜底。

## What Changes

- **决策文本价位交叉校验**（price-level-tooling 新增需求）：`reeval_triggers` 与决策理由文本中出现的价位数值，SHALL 与 state 中已验证的技术指标值（MA/收盘/高低点/布林轨道）交叉核对；偏差超阈值或触发条件在现价下已满足（空洞触发）时，SHALL 登记 anomaly 并在报告标注，SHALL NOT 硬中断管线。
- **非法仓位档位归一**（report-decision-rendering「参数缺失时诚实标注」扩展）：`position_size` 不在档位词表（light/moderate/heavy）内的字面量（如 `"none"`）SHALL 按缺失处理渲染「未提供」，MUST NOT 原样透传。
- **buy/sell 终稿再评估触发条件必填化**（agent-node-contracts「非执行动作结构化理由契约」扩展）：601818 实证 sell 终稿无任何 reeval_triggers、两轮风控辩论点名缺陷而 FM 仍以「执行安排完备」批准——终稿 buy/sell 缺触发条件 SHALL 打回 risk_judge 一次，仍缺放行 + `final_reeval_check` note「已打回仍未申报」；报告 buy/sell 决策节 SHALL 渲染「再评估触发条件」行（缺失「未申报」）。Trader 侧 buy/sell 维持现状直通。
- **FM 审批对象完整性可见性**（agent-node-contracts 新增需求）：FM 的 LLM 上下文 SHALL 包含终稿完整性检查标注；报告 FM 节 SHALL 并排渲染结构不完整标注与 FM 审批意见，MUST NOT 只呈现完备性论断。
- **FM 复述保真与置信度语义**（agent-node-contracts「Fund Manager 操作性结论字段」扩展 + prompt）：FM `confidence` 语义定义为「对本次裁决正确性的置信度」；与 risk_judge 终稿置信度偏差 >0.15 时报告渲染「置信度漂移」标注（含两值，供标注人判读）；FM 复述上游指标名与数值 MUST 保真。同步修改 `fund_manager.md`、`risk_judge.md` 并执行 `scripts/deploy_prompts.py` 发布。
- **非目标（另行处置，不在本 delta）**：置信度 0.55 聚类归因（须走 incident 026 分桶终裁流程）；sell 语义澄清（减仓退出 vs 建立空头，owner 裁决）；语义级复述失真（指标名改写）不做代码硬判定（可观测 + prompt 约束）。

## Capabilities

### New Capabilities

（无——全部落入既有 capability）

### Modified Capabilities

- `price-level-tooling`: 新增「决策文本价位与已验证技术指标交叉校验」需求——覆盖 reeval_triggers/理由文本中价位数值的幻觉检测（600845）与空洞触发条件检测（601066），采取 anomaly + 报告标注的可观测语义，不硬中断。
- `report-decision-rendering`: 「参数缺失时诚实标注」扩展——非法档位字面量（不在 light/moderate/heavy 词表）归一为缺失，渲染「未提供」（600515 `none` 透传问题，#169 修复未覆盖的枚举形态）。
- `agent-node-contracts`: ①新增「FM 审批对象完整性可见性」需求（601818 FM 完备性论断与方案事实矛盾）；②扩展「Fund Manager 操作性结论字段」——confidence 语义、漂移披露义务、复述保真约束（002916/600015/600515）。

## Impact

- **代码**：`src/finance_agent/` 决策后处理链（trader/risk_judge 输出清洗）、FM 节点上下文构建、报告渲染节点（report 拼装）、anomaly 登记路径（复用 citation-verification 的 anomalies 通道）；`src/finance_agent/prompts/fund_manager.md`、`risk_judge.md`。
- **部署**：prompt 修改后必须执行 `uv run python scripts/deploy_prompts.py`（prompt-deploy-consistency 门禁）。
- **观测**：新增校验产出的 anomaly 进 Langfuse trace 与 judge 材料，评估侧需在 metrics 台账关注新信号（只观测，不进处置）。
- **测试**：`tests/` 新增各需求的单元/集成复现测试；A 类 bug 走独立复现测试（不 mock 被测系统红线不变）。
