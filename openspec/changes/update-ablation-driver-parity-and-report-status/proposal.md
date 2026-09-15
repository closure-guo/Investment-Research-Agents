# Proposal: update-ablation-driver-parity-and-report-status

## Why

2026-09-15 对「消融结论链」逐条取证，发现两类问题，且**互相放大**：

**① 对外材料仍在陈述已被推翻的结论。** `evals/ablation/results/pilot.md` 的「简历素材对照」段至今原文在线：「风险辩论+基金经理层使 decision_grounding 显著退步（95% CI [-1,-1]）；据此裁剪管线可节省约 41% token」。该结论已被 n=10 报告全盘推翻（伪影 #109/#111/#112），且成本口径也不同（41% 是 pilot 的 full-vs-analysts 口径，n=10 为 29%）。README 消融节写「据此裁剪管线省 ≈29% token」，与同一文档「诚实边界」的「通路验证级初步证据」自相矛盾——通路验证级证据不能支撑裁剪决策，且「未获统计支持」≠「无价值」。

**② 权威报告未披露自身的测量口径缺陷。** `docs/evals/2026-09-03-消融n10权威结果.md` 自称「修复后 n=10 权威版」，但：表内 plus_debate 行有 grounding 4.0 / consistency 4.0——该变体**没有** Trader/风控/FM 层，「有分」即 #112 伪影存活进 90 批次的直接证据；由此 full−plus_debate 的 grounding Δ1.3333 / consistency Δ0.6667 是「宽容评虚层 vs 挑剔评真层」的**无效比较**；报告未披露该 n10 跑批（09-03）早于 judge 校准 round7 达标（09-13）；报告标了 citation_pass「契约噪声仍存在」，结论区却仍把它当层增量指标列出。

**③ 造成 ② 的代码缺陷仍在 main 上，下次跑批判分口径会漂移。** 库侧 `evals/ablation.py::run_ablation` 走 `_applicable_dims(variant)` 过滤维度（#112 的修复），但真正烧 token 的入口 `tests/scripts/ablation_pilot.py` 仍是硬编码 `if variant == "analysts" and dim != "report_relevance"`——**plus_debate 的 grounding/consistency 照评不误，下次跑批 #112 原样复现**。同时驱动每条 run 前 `build_snapshot(ticker)` 重建快照，digest 只在标的首次登记、之后不核验——跨交易日续跑会吃到不同快照，「差异只可归因于编排」在驱动路径上只是注释保证。

**④ 聚合字段名与实际计算不符。** `aggregate_results` 的 `diff_median` 实际算的是 `sum(seq_cur)/len - sum(seq_prev)/len`，即**均值差**（CI 同为 mean-diff 口径），字段名在「中位数配对」语境下误导读报告的人。

> 取证更正（重要）：最初的盘点称「ablation_pilot.py 最后一次被触碰是 09-02，#108 之后再未改动，K 次均值与材料落盘在驱动侧不存在」——**不成立**。a86986a（09-14）已写入 `parse_args`/`persist_materials`/`run_judge_mean`，驱动侧确实已走 K 次均值、也已落盘 judge_vars。真实缺口只有「维度适用性过滤」与「快照 digest 核验」两条。本 delta 的取证范围按实际代码为准。

**共性根因**：结论没有生命周期管理——文档是静态的，结论会被推翻，但没有机制让旧结论自动失效，也没有机制保证「库侧修好的口径」真的落到跑批入口。

## What Changes

- **驱动与库侧口径合一**：`ablation_pilot.py` SHALL 复用 `evals.ablation._applicable_dims`，SHALL NOT 各自硬编码变体→维度映射（唯一实现，杜绝两处判分逻辑并存）。
- **快照一致性核验**：驱动的每 run 快照 digest SHALL 与本次跑批登记值一致；不一致 SHALL 显式失败（跨交易日续跑不得静默混批），使「差异只可归因于编排」成为**代码保证**而非注释保证。
- **聚合字段正名**：`diff_median` → `diff_mean`（点估计为均值差），配对单元（同标的同变体重复的中位数）在报告与口径台账写明；`tests/scripts/ablation_aggregate_90.py` 消费点同步。
- **报告状态与适用口径（新契约）**：消融结果报告头部 SHALL 声明 `**status**:`（`active` 或 `superseded-by: <path>`，且路径 SHALL 可解析到真实文件）；对已被推翻的结论 SHALL 就地标注并指向权威版本。**本 delta 只覆盖 `evals/ablation/results/*.md` 与结论性报告头部——完整的结论注册表 / 索引页状态徽章 / 全库扫描留待后续 delta**（见 design「不做」段）。
- **文档就地修正**：pilot.md 加 superseded 头部并撤回简历素材段；n10 报告补三处适用口径披露（#112 存活、judge 未校准、citation_pass 不作为层增量）+ 有效 n=3 的分辨率披露；README 消融节与诚实边界重写。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation`：
  - MODIFIED「数据对齐消融实验」——新增维度适用性过滤唯一实现、每 run 快照 digest 核验、层增量点估计字段名与配对单元披露三项约束及对应 Scenario。
  - ADDED「评估报告结论状态与适用口径标注」——报告头部 `status` 契约、被推翻结论的就地标注与指向、权威报告的测量口径披露义务（judge 校准状态 / 变体适用性 / 配对单元规模 / 契约噪声）。

## Impact

- **代码**：`tests/scripts/ablation_pilot.py`（过滤改调库侧 + digest 核验）、`evals/ablation.py`（字段改名）、`tests/scripts/ablation_aggregate_90.py`（消费点改名）。
- **文档**：`evals/ablation/results/pilot.md`、`docs/evals/2026-09-03-消融n10权威结果.md`、`README.md`、`docs/evals/metrics.md`（口径登记：字段名 / 配对单元 / 报告状态契约）。
- **测试**：`tests/evals/test_ablation_pilot.py`（假件补 `_applicable_dims`；新增过滤与 digest 核验红灯用例）、`tests/evals/test_ablation.py`（字段改名 + 配对单元披露）、新增 `tests/evals/test_report_status.py`（状态契约 + 指针可解析 + 被推翻报告须带撤回说明）。
- **类型债**：`ablation_pilot.py` 原有 15 处 mypy `union-attr`/`unused-ignore` 债（`Optional` 参数 + 函数内重绑定导致窄化失效），本次触碰该文件顺手清零（改用显式 `Any` 别名 + 显式标注 + `_load_resume` 的返回值收窄），改动文件 mypy 全绿。
- **不改的行为**：消融编排图、变体定义、judge rubric 版本、K 次均值协议、材料落盘格式、断点续跑键 `(variant, ticker, repeat)` 全部不变。**不重跑任何跑批**（本 delta 不烧 token）。
- **无 UI / SSE / 会话 / 状态流转变更 → 非交互类变更，不触发 E2E 门禁。**
- **风险**：字段改名会让历史聚合产物（`reports/ablation/pilot-*.json` 本地未入库）的字段名与新代码不一致——历史产物的数字不受影响，但复算脚本需按新名读；本 delta 在 `metrics.md` 登记该切点。
