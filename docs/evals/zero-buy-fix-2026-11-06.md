# 零买入修复 · 进度台账（2026-11-06）

> 计划：[docs/superpowers/plans/2026-11-06-zero-buy-decision-fix.md](../superpowers/plans/2026-11-06-zero-buy-decision-fix.md)
> 本文件按小时更新，记录「做了什么 / 得到什么 / 下一步」。

## 诊断结论（已定稿）

| 事实 | 数值 |
|---|---|
| 全部分析会话中买入次数 | 0 / 581 |
| 研究经理「看多」占比 | 12 / 201（6%） |
| 看多之后变成买入的次数 | 0（12 条全部 watch） |
| 12 条看多中卡在「无催化剂/已定价」 | 12 / 12 |
| 12 条看多中提及「±2%/带宽/分辨率」 | 6 / 12 |
| 107 条 watch 中提及带宽/σ | 63（59%） |
| 已盯市 60 条看空的方向性收益 | -2.68%（超额 -3.56%，胜率 12%） |

**机制**：买入需证明「20 个交易日内有可兑现催化剂」＋ 财报真空期（11 月三季报已披露、下个窗口次年 1 月）＝ 结构性禁止买入；风险辩论用「止损窄于日波动」可否决任何方案；±2% 判分带宽被当成风险预算。

## 状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| 计划文档 | ✅ 完成 | `docs/superpowers/plans/2026-11-06-zero-buy-decision-fix.md` |
| Task 0 决策分布读数 | ✅ 完成 | `f754c109`；`tests/outcome/test_decision_mix.py` 3 passed |
| Task 1 日报告警 | ✅ 完成 | `436521fb`；6 新测试 + 6 既有测试全绿（12 passed） |
| Task 2 变体提示词 | ✅ 完成 | `6b67c5ed`；grep 校验 A 保留 ±2%/删催化剂、B 删契约（117→108 行） |
| Task 3 隔离运行器 | ✅ 完成 | `743d6309` + `77d29bcf`；6 测试绿；**--variant 改可重复（修 B 臂双变体缺口）** |
| Task 3 冒烟 | ✅ 通过 | 002916 watch(completed)，9 分钟，隔离库落库、生产库未动 |
| Task 4 消融实验 | 🏃 A 臂运行中 | 01:40 启动，8 只 × trader_no_catalyst，隔离库 tmp/ablation-A.db |
| Task 5 trader 提示词 | ⬜ 依赖 Task 4 结论 | — |
| Task 6 risk_judge 提示词 | ⬜ 依赖 Task 4 结论 | — |
| Task 7 回归验证 | ⬜ 依赖 Task 5/6 | — |
| Task 8 账本语义提案 | ✅ 提案已交（待 owner 拍板） | `4c828d05`；`openspec validate --strict` 通过 |
| Task 9 评分口径提案 | ✅ 提案已交（待 owner 拍板） | `be500ec2`；metrics.md §1.24 + openspec validate 通过 |

## 执行偏差记录（相对计划）

1. **Task 0 测试路径**：`tests/outcome/test_decision_mix.py`（flat，符合仓库 tests/outcome 惯例）而非计划的 `tests/outcome/ops/`；实现为加固版（缺表容错，多一个测试）。
2. **Task 1 接入点**：daily_brief 的 `warnings` 实为数据缺口专用 `list[dict]`——决策分布改为独立事实段 `facts["decision_mix"]`（含告警句），LLM 日报与降级渲染两条路都能看见；`zero_buy_warning` 契约与计划一致。
3. **Task 3 运行器**：`--variant` 改可重复 + `parse_variant_mapping` 独立函数——计划的草案无法表达 B 臂（trader+risk_judge 同时注入）；另补 scripts 引导（ops_api 依赖顶层 evals 包，`monitor_unverifiable_ratio.py` 同款先例）。
4. **Task 4 样本替换**：计划点名的 4 只看空票（002852/600780/600496/000019）在生产库无会话无 prediction（幽灵引用）；按「同批基线配对」意图替换为 10-30 批次的 000428(sell)、600916(sell)、002648(watch)、600015(watch)。**「本日 20 条决策」实为 10-30 批次**（生产库 predictions 按日恰好 20 条，11 月后新会话在容器卷，与本计划宿主侧库无关）。

## 停止点（触发即停，不硬做）

1. 生产提示词切换后、观察第一个交易日前 —— 需 owner 知晓回滚路径。
2. `metrics.md` §1 与结算代码改动 —— 只交提案。
3. 消融出现「两臂都 0 buy」—— 规则不是唯一原因，重新归因。
4. 需要判断「买入建议质量」—— 交人工终裁。

## 日志

- 2026-11-06 01:0x 完成机制诊断，落计划文档与台账。
- 2026-11-06 01:0x-01:40 Task 0-3 全绿 + 冒烟通过；Task 8/9 提案交付。分支 `fix/zero-buy-decision-20261106`。
- 2026-11-06 01:40 A 臂启动（8 只，顺序执行，预计 ~80 分钟）；B 臂候跑。
