# 消融实验试跑记录：pilot.md

**status**: superseded-by: docs/evals/2026-09-03-消融n10权威结果.md
**日期**：2026-09-02
**目的**：`evals/ablation.py` 首跑，产出"辩论层价值"第一份真实数据（简历关键素材）
**产物 JSON**：`reports/ablation/pilot-20260902-152818.json`（+ 断点续跑台账 `reports/ablation/resume.json`）
**驱动脚本**：`tests/scripts/ablation_pilot.py`（自驱循环 + 断点续跑 + usage 记账，不改 evals/ablation.py）

## 配置（硬上限内）

| 项 | 值 |
|---|---|
| 标的 | 002412 汉森制药 / 600519 贵州茅台 / 300308 中际旭创（3 只） |
| 变体 | analysts（4 分析师直出）/ plus_debate（+Bull/Bear 两轮辩论 + research_manager）/ full（完整五层，含两轮风控辩论 + 基金经理） |
| 重复 | 3 次/（标的 × 变体），共 27 run |
| 分析模型 | **glm-5.3**（火山方舟；.env 生产默认——用户裁决：试跑与生产同模型，数字可迁移） |
| judge | deepseek-v4-flash（火山方舟；与分析模型分离，保持独立性） |
| 统计 | 同标的同变体 3 次取中位；层间差异用配对 bootstrap B=10,000（`evals/stats.py`）95% CI |
| 快照对齐 | 每标的三变体共用同一 fetch_data+compute_metrics 快照（snapshot_digests 落盘可验） |

## 结果总表

| 变体 | citation_pass | coverage | judge 中位（rel/辩论/grounding/一致） | token 成本 | 增量效果 vs 上一层（95% CI） |
|---|---|---|---|---|---|
| analysts | 3/9 = **33.3%** | 0.657 | 5.0 / — / — / — | 632,293（64 次调用） | 基线 |
| plus_debate | 2/9 = **22.2%** | 0.545 | 5.0 / 5.0 / 5.0 / 4.0 | 662,409（105 次调用） | 辩论层：report_relevance Δ0 CI[0,0]、citation_pass ΔCI[-0.333, 0] → **未获统计支持** |
| full | 0/9 = **0.0%** | 0.662 | 5.0 / 5.0 / 4.0 / 4.0 | 1,076,984（230 次调用） | 风控+PM 层：**decision_grounding Δ-1 CI[-1,-1] → 显著退步**；debate_quality CI[0,1]、consistency CI[-1,1]、citation_pass ΔCI[-0.333,0] → 未获统计支持 |

**usage 汇总（真值，0 条估算）**：482 次 LLM 调用 / 2,555,731 token（1,943,510 输入 + 611,081 输出）；单 run 均值约 17.9 次调用 / 94.7k token。

## 结论措辞（严格按 CI 纪律，无修饰）

1. **辩论层（analysts → plus_debate）**：judge 的 report_relevance 与 citation_pass 率增量的 95% CI 均含 0 → **该层价值未获统计支持**。
2. **完整层（plus_debate → full）**：decision_grounding 增量 CI = [-1, -1]（排 0）→ **显著退步**；其余维度 CI 含 0 → 未获统计支持。
3. **整体 citation_pass 偏低**（33%→22%→0%）：所有变体下 glm-5.3 产出的 claim 大量未通过校验器（FAIL 分桶未在本 pilot 展开归因）——**这是 pilot 暴露的现象，不构成"辩论层导致幻觉率上升"的因果结论**（样本小 + 未分桶），留作 follow-up issue。

## 成本实测

| 变体 | 调用次数 | token 合计 | 单 run 均值 |
|---|---|---|---|
| analysts | 64 | 632,293 | 70,255 |
| plus_debate | 105 | 662,409 | 73,601 |
| full | 230 | 1,076,984 | 119,665 |

（glm-5.3 无思考链，输出 token 显著小于此前 deepseek-v4-flash 误用轮的实测，见成本对照。）

## 环境偏差（诚实披露）

1. **分析模型**：glm-5.3（ark）。此前两轮曾误用 deepseek-v4-flash 作分析模型（deepseek-chat 官方 key 失效后的错误替代），**已弃用并重跑**；本文件数据全部为 glm-5.3。
2. **judge 端点**：zen → ark（zen 余额见底），judge 模型不变（deepseek-v4-flash）。
3. **数据源**：K 线/指数走新浪（东财 push2his 本机不可达，见 issue #102/#103）；新闻/实时行情按空降级。
4. **重试与断点**：pilot 驱动含瞬时故障指数退避重试（ark burst/半僵流）与逐 run 断点续跑（2026-09-02 补）——本组 27 run 全程 0 次限流/超时崩溃。

## 简历素材对照（素材 3 的真实数据替换）——**本段结论已撤回，勿使用**

> ⚠️ **撤回说明（2026-09-15）**：以下原文的**每一条量化结论都已被 n=10 报告推翻**，本段仅作留档，**不得**用于简历、对外材料或任何裁剪决策。
>
> 1. **「风险辩论+基金经理层使 decision_grounding 显著退步（95% CI [-1,-1]）」不成立**。该 CI 排 0 是本批次评估伪影的产物（#109/#111/#112：宽容评虚层 vs 挑剔评真层的比较不对称），不是架构效应。n=10 批次同样未能给出该层的有效读数——其 full−plus_debate 的 grounding/consistency 两条 Δ 因 `plus_debate` 无对应层而被判定为**无效比较**（见权威版本的「适用口径披露」）。结论：**这条层增量既未被证实也未被证伪，它建立在一个无效比较上**；「裁剪省 41% token」不能由此推出。权威版本见 [消融 n=10 权威结果](../../../docs/evals/2026-09-03-消融n10权威结果.md)。
> 2. **「裁剪管线可节省约 41% token」口径有误**。41% 是本 pilot 的 full-vs-analysts 口径（1,076,984 vs 632,293，即裁到基线层的节省比例）；n=10 同口径实测为 **28.7%**。两个数字不可混用，且该成本梯度**不能**支撑裁剪决策——层增量未获统计支持（有效 n=3，评估分辨率不足）。
> 3. 本段所在的整份报告已整体被 n=10 报告取代（本文件头部 `status: superseded-by`）。pilot 的**确定性指标**（citation_pass / coverage）与流程结论（断点续跑、计量口径）仍有效，被推翻的是 judge 维度上的层增量结论。

> 原文（留档，勿引用）：通过数据对齐消融实验（三变体 × 同 state 快照 × 3 次重复，配对 bootstrap B=10,000）量化多智能体辩论层的增量价值：Bull/Bear 辩论层增量未获统计支持（CI 含 0），风险辩论+基金经理层使 decision_grounding 显著退步（95% CI [-1,-1]）；据此裁剪管线可节省约 41% token（full vs analysts：1,076,984 vs 632,293）。
