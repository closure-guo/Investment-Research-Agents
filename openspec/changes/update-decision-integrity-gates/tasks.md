# Tasks: update-decision-integrity-gates

## 1. 决策文本价位交叉校验（price-level-tooling）

- [ ] 1.1 数值提取与量纲归类工具（价格 vs 百分比/比值，含单测）
- [ ] 1.2 交叉校验器：指标名归属（文本点名的 MA60 等优先）→ 偏差形态（>2%）与空洞形态（上破 ≤ 现价 / 下破 ≥ 现价）检测，anomaly 登记
- [ ] 1.3 管线接入 risk_judge 终稿后处理，anomaly 进 state；报告「再评估触发条件」旁渲染「价位待核实」标注
- [ ] 1.4 复现测试：600845 形态（18.8 vs MA60 18.066 → 偏差 anomaly）与 601066 形态（站上 22.61 vs 现价 23.03 → 空洞 anomaly）检出且管线不中断；600015 形态（18.35% 阈值）不误报

## 2. 非法仓位档位渲染归一（report-decision-rendering）

- [ ] 2.1 渲染层档位词表校验（大小写不敏感），`"none"` 等非法字面量渲染「未提供」，不回写决策对象（600515 形态复现测试）
- [ ] 2.2 合法档位（含大小写变体）渲染不变（回归测试）

## 3. buy/sell 终稿再评估触发条件必填化（agent-node-contracts）

- [ ] 3.1 risk.py 终稿完整性检查扩展：buy/sell 缺 `reeval_triggers` 打回一次（feedback 引用风险辩论共识线索），仍缺放行 + `final_reeval_check` note「已打回仍未申报」
- [ ] 3.2 报告 buy/sell 决策节渲染「再评估触发条件」行（缺失「未申报」；601818 形态复现测试）

## 4. FM 审批可见性与置信度语义（agent-node-contracts + prompt）

- [ ] 4.1 FM 上下文注入终稿完整性检查标注（final_price_check / final_inaction_check / final_reeval_check note）
- [ ] 4.2 报告 FM 节并排渲染结构不完整标注与 FM 审批意见（完整方案零增量回归测试）
- [ ] 4.3 置信度漂移检测：FM 与终稿 confidence 偏差 >0.15 → 报告「置信度漂移」标注（含两值；600515 形态复现测试；≤0.15 无标注回归）
- [ ] 4.4 `fund_manager.md` 增 confidence 语义、漂移说明义务、复述保真约束；`risk_judge.md` 增 confidence 与 reasoning 一致纪律
- [ ] 4.5 执行 `uv run python scripts/deploy_prompts.py` 发布，prompt-deploy-consistency 门禁通过

## 5. 收口

- [ ] 5.1 `uv run pytest` 全量通过、`uv run ruff check`、`uv run mypy` 通过
- [ ] 5.2 verification-before-completion：逐条对照 tasks 勾选，输出新鲜验证证据
- [ ] 5.3 评估侧同步：metrics.md 时间线备注新 anomaly 信号（只观测）；0.55 聚类与 sell 语义两项挂待 owner 裁决清单
