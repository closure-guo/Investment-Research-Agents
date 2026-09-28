# Design: update-decision-integrity-gates

## Context

2026-09-28 跑批审查确认了三类决策层缺口：(1) 决策自由文本中的价位（触发条件）无任何校验，trader/risk_judge 幻觉出的 18.8（真实 MA60=18.066，引用校验已验证）直达终审报告；(2) 渲染层对非法档位字面量（`position_size: "none"`）无归一规则；(3) FM 层缺乏对审批对象完整性的可见性约束与 confidence 语义定义，导致「执行安排完备」式失实论断（601818）与无解释置信度漂移（600515 FM 0.8 vs 裁决 0.55）。项目已有可复用的机制：citation-verification 的 anomalies 通道与「数值型相对容差」核对基建、非执行动作理由契约的「打回一次后放行+如实标注」语义、report-decision-rendering 的「未提供」渲染与派生指标代码计算先例。

## Goals / Non-Goals

**Goals:**
- 决策文本价位幻觉与空洞触发条件可被确定性检测并可见（anomaly + 报告标注），不中断管线。
- 非法仓位档位字面量渲染归一为「未提供」，落库保留原值。
- FM 审批时能看到完整性标注；报告并排渲染「方案事实 vs FM 论断」。
- FM confidence 语义、漂移披露义务、复述保真约束落 spec + prompt，改动经 deploy_prompts 发布。

**Non-Goals:**
- 置信度 0.55 聚类的归因与处置（incident 026 流程，owner 终裁）。
- sell 语义（减仓退出 vs 建立空头）澄清——owner 裁决后另行立项。
- 语义级复述失真（指标名改写）的代码硬判定——只做 prompt 约束 + 可观测性，不做 NLP 级校验器。
- FM approve 于不完整方案时强制 reject/return——仲裁权保留，可见性优先。

## Decisions

- **价位交叉校验做成确定性后处理 + anomaly 通道，不做 LLM 自检**：从 `reeval_triggers`/`inaction_reason`/`reasoning` 文本提取数值（正则 + 量纲归类：价格 vs 百分比/比值），仅与 state 中已验证技术指标（technical_indicators、price_levels、close）做归属匹配，偏差 > 2% 或空洞触发（上破触发价 ≤ 现价 / 下破触发价 ≥ 现价）登记 anomaly。选择可观测而非打回：触发条件是 watch/hold 的辅助信息，硬中断会把辅助信息的失真放大成管线事故；与项目「放行 + 如实标注」先例一致。
- **档位归一只作用于渲染，不回写决策对象**：TradeDecision/落库/trace 保留 `"none"` 原值，渲染层归一。理由：上游产物可观测性优先，改写上游会掩盖 risk_judge 的输出质量问题，使归因失真。
- **FM 完整性可见性走上下文注入 + 报告并排渲染，不新增 FM 校验拦截**：FM 上下文已含 final_trade_decision 完整字段（既有 spec），补注入完整性检查 note 即可；报告端在 FM 意见前渲染标注。不强制 FM 对不完整方案 return，因为 return 在图中无重试回路，强制会造成死路。
- **置信度漂移用「>0.15 且未解释 → 报告标注」而非模型校验拦截**：漂移可能是合法仲裁（FM 独立判断），硬拦截会误伤；标注使 judge 与标注人可见，归因交给评估侧。
- **prompt 修改最小化**：fund_manager.md 增 confidence 语义与保真复述两条纪律；risk_judge.md 增「confidence 字段与 reasoning 陈述一致」一条。均走 deploy_prompts 发布。

## Risks / Trade-offs

- **数值提取误报/漏报**：自由文本数字提取天然不完美。对策：量纲归类过滤（百分比/比值/日期不进价位核对）+ 阈值放行 + anomaly 只标注不拦截；上线后按 incident 026 分桶审视误报，再调阈值。
- **报告版面噪音**：标注只在异常时出现（空洞/偏差/漂移/不完整），正常批次零增量。
- **prompt 漂移**：改动后跑 deploy_prompts 并在 metrics 台账观察下一轮 live 批次 FM reasoning 的复述保真情况，只观测不处置。
