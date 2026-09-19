# 评估指标台账

唯一查询入口：口径定义看这里，历史看时间线表，机器可读摘要看 [`metrics/runs.jsonl`](runs.jsonl)。
分工：Langfuse Scores = 逐 trace 明细真源；`reports/evals/*.json` = 单次实验全量产物（本地）；本文件 = 跨 run 台账。每次实验收口时更新一行，并注明当时的 git HEAD。

**基线切点**：incident 026（`docs/incidents/026-citation-gate-misattribution-20260911.md`）之前，`citation_pass` 把校验器误报算在分析师头上（r2 终裁：FAIL 桶 100% 误报），不可与 r4 起的拆报指标直接比较。

---

## 1. 口径定义

### 1.1 judge 维度（LLM-as-judge，rubric 见 `evals/judges.py`，人工对照见 `evals/judge_calibration/`）

| 指标 | 口径 | rubric 版 | 备注 |
|---|---|---|---|
| report_relevance | 切题 = 回答了用户的问题（非主题相关） | v3 | quick/deep 均评 |
| debate_quality | 辩论交锋质量（含交锋覆盖率对照锚点） | v2 | deep only |
| decision_grounding | 决策论据逐条核对 evidence_refs ↔ source | v5 | deep only；材料 v6 含风控指标+风险辩论 |
| consistency | 各层方向一致性（approve 语义：批准对象是 Risk Judge 方案） | v3 | deep only |
| judge confidence | judge 对评分依据充分性的自报把握；材料不足 MUST <0.5 | — | 以 `[conf=x.xx]` 前缀落 Scores.comment |

### 1.2 确定性指标（零 token，`evals/evaluators.py` / `evals/sections.py`）

| 指标 | 口径 |
|---|---|
| ticker_match | 标的解析正确性 |
| section_coverage | 报告必备章节覆盖（must_cover；「风险提示」章口径待决策 #6） |
| citation_coverage | 报告正文数字被 claim 认领比例（警告线 0.90） |
| argument_anchor_coverage | 辩论论点锚点覆盖率（anchored/total；拆项 unanchored_inference/unresolved/missing_required/unspecified；无论点 None；零 LLM） |

### 1.3 citation 门禁拆报（r4 起，delta `rework-citation-gate-attribution`；定义=incident 026）

| 指标 | 层 | 口径 |
|---|---|---|
| citation_blocked | 阻断 | 归一后残余 FAIL > 0 |
| citation_analyst_true_fail | 阻断/拆报 | 残余 FAIL + 单点修复回填数（修复会把真错藏进 PASS，必须计入） |
| citation_verifier_normalized | 跟踪 | 归一后由 FAIL 转 PASS 的计数（unit/percent/echo）＝校验器此前解析债的量化 |
| citation_unverifiable_text | 跟踪 | 文本 claim（entity/regulatory/event）分型排除，不计阻断分母 |
| citation_unverifiable_unregistered | 跟踪 | 未注册/空值 UNVERIFIABLE（目标：注册后趋零） |
| citation_unverifiable_comparative_delta | 跟踪 | 比较型差值申报（comparative 的 stated_value 非三枚举，如「MA5 较 MA20 低约 2.3%」的 2.3）；不计阻断分母，与 unregistered 互斥不重复计数 |
| citation_surgical_repaired | 跟踪 | 单点修复成功回填条数（稀疏 value_mismatch，同一分析师同轮 <3 处触发）；已计入 `analyst_true_fail`，单独计数用于观测修复触发/成功率——修复失败时无重试回退（自动重试已关），失败即标记阻断 |
| citation_coverage_warn | 警告 | coverage < 0.90 报警不阻断 |
| auto_claims | 跟踪 | 正文数字唯一匹配 state 条目自动合成的 claim 数 |

### 1.4 校准指标（`evals/judge_calibration/measure.py`）

Spearman / MAE / 方向一致率（>3 分界）/ Cohen's κ；阈值 Spearman≥0.5、MAE≤1.0、方向一致率≥0.7。judge 分零方差的维度 Spearman 不可计算，以 MAE/方向一致率为主。

### 1.5 消融层增量（`evals/ablation.py::aggregate_results`）

| 项 | 口径 |
|---|---|
| 点估计字段 | `judge_<dim>.diff_mean`——**均值差** `mean(cur) − mean(prev)`（2026-09-15 前名为 `diff_median`，与实际统计量不符，已改名；旧名前产出的产物 JSON 复算需按新名读） |
| 置信区间 | `paired_bootstrap_ci`，B=10,000，同为 mean-diff 口径 |
| 配对单元 | **标的**（`pairing_unit: "ticker"`）：同标的同变体重复先取中位数，再以标的为配对点重采样；报告携 `pairing_units` |
| 有效 n | = 标的数，**不是** run 数（3 标的 × 10 重复 → 有效 n=3，CI 宽度主要由该分辨率决定） |
| 维度适用性 | 变体不存在的层不评（`_applicable_dims` 唯一实现）；被过滤维度记 `None` 且不产生 judge 调用（#112） |
| 快照一致性 | 每条 run 的快照 digest 须与本次跑批登记值一致；不一致显式失败（不静默混批） |
| 报告状态头 | `evals/ablation/results/*.md` 头部须带生命周期字段（`status`：`active` 或 `superseded-by: <路径>`，字面格式见 §1.7④），指针须可解析（`tests/evals/test_report_status.py`） |

### 1.6 报告引用纪律

对外材料（README / 简历素材等）引用消融数字时须引用 `status: active` 的报告；「未获统计支持」不得转写为「据此可裁剪」（统计不支持 ≠ 无价值，裁剪需独立证据）。被推翻的报告须就地标注（原文保留 + 撤回说明 + 指向权威版本），不得只改数字。

### 1.7 因果消融 v2 口径（delta `revamp-ablation-v2-causal-claims`，2026-09-16 切点；P1 正式批已跑）

准入判据 = **因果下游检验**：指标的失败模式须因果地依赖被消融对象，才可作层增量主指标（切题度这类通用正确性指标不依赖任何层 → 出局；v1 的 judge 四维不再作层增量裁决依据，判定本身保留为辅助观测）。

| 项 | 口径 |
|---|---|
| ① 因果下游指标与判定方式 | 进矩阵对象须先在因果主张登记表登记（族 A 反幻觉 A1–A7 / 族 B 编排 B1–B6：因果主张 + 失败模式 + 主指标 + 判定方式 + 效应量预期；`evals/causal_ablation/claims.py`），且持有效预登记（主指标 / MDE / 阈值换算依据 / 样本量依据 / 停止规则 / rubric 版本锁定，`preregister.py`）。判定方式 `code｜nli｜judge`，逐单元落库 `unit_id/ticker/run/variant/unit_type/judgment/method/confidence`（`units.py`）。族 A 主指标=逃逸率族（A3 注入污染逃逸率为代表）+ 计算型 claim 数值错误率 / 负索引·期次错位率 / 修复前后真 FAIL 率差 / 事件型 claim 不可回声率 / stale 引用率等；族 B 主指标=B1 风险点增量率（nli）/ B2 交锋修正率（judge）/ B3 风控数字出处率（code）/ B4 sanity 打回率（code）/ B5 pairwise 盲评胜率（judge）/ B6 事后结算胜率（code，不作层间归因，`family_b.py`）。推断单元=**标的**（簇 bootstrap B=10,000 + ICC 折算有效 n 披露；`aggregate.py`） |
| ② 校准门控 | 凡 `method ∈ {nli, judge}` 的指标（B1/B2/B5 及族 A 的 nli/judge 腿）rubric 与人工标注一致率 **≥0.80** 才允许进消融结论；每批抽 ≥20% 单元人工复核，一致率 <0.80 该批判定作废并重校准；`code` 判定不适用（`calibration_gate.py`） |
| ③ 指标三栏（2026-09-16 owner 裁决②，`traffic_columns`） | **拦截率（主）** = 开态被拦 / 开态到达机制面前（机制只对「它见到的」负责）；**暴露率（辅）** = 开态到达产物 / 全部注入单元（void 的去向在此交代）；**关态穿透率（对照）** = 关态到达 / 注入（阳性对照）；**跑批口径降附录**（关态逃逸 / 注入，混合暴露与拦截，SHALL NOT 当主指标）。输入侧面型（A7）不进任一栏。旧口径（逃逸 = 污染值原样出现在终态产物**且未被告警**、且经人工终裁确认为真逃逸；**分母 = 已终裁单元数**；未终裁单列 `pending`；无终裁时 `rate = None`，**不得报 0%**）保留为「逃逸终裁」纪律，用于附录栏与四桶拆报。校验器误报走四桶拆报（blocked / analyst_true_fail / surgical_repaired / verifier_normalized，桶名以 `escape.py::VERIFIER_BUCKETS` 为准；§1.3 为 citation 四桶同族拆报），**不进逃逸率分母**；四桶拆报进消融聚合，`citation_pass` 标量退出层增量比较路径（只留产出、不作层增量裁决，见 §2 切点段） |
| ④ 结论句式与注册表 | 合法结论仅两句式：**真阴性**（CI 整体低于成本阈值 →「在本实验分辨率（MDE=X）下…建议降级/裁剪」）｜**分辨率不足**（须写出 MDE 与 CI 并给扩样方向）。**裸「未获统计支持」非法**——结论句必带 MDE（`conclusion.py`）；阳性对照测不出显著差异 → 本轮全部阴性作废（灵敏度未证实不得进注册表）。报告头部须带生命周期字段（`status`：`active` 或 `superseded-by: <路径>`，字面格式与校验见 `evals/causal_ablation/report_status.py`，取代者指针须真实存在）；索引 [`README.md`](README.md) 渲染状态与「未标注生命周期」清单，引用纪律见 §1.6 |

**P1 正式批（2026-09-16，离线四型 20 标的 × 4 实例，零 LLM）——结论封口**：**拦截率（主）= 1.000**（316/316），簇 bootstrap（按标的重抽样 B=10,000）95% CI **[1.000, 1.000]**——20 簇**无一例外**（簇间零方差，区间塌为点）；单型同样各 1.000（簇 19–20）→ 按预登记判据（拦截率 <50% 判薄防线）**四机制均在阈值之上，机制必要性成立**（合法结论句：阳性成立，非「真阴性」亦非「分辨率不足」；两式仅适用于裁剪方向的阴性结论）。**合计不一致比 = 0.880**（b=278, c=0）95% CI [0.869, 0.894]；阳性对照 b=156 c=0 p=2.2e-47 显著。辅栏：暴露率 316/320 = 0.988（4 单元无注入靶点，601012 方向型）；关态穿透率 0.988；逃逸终裁（附录）= **278/278 真逃逸**（78 案例：40 由 round-3 继承 + 39 本轮人工终裁，pending=0）。stale_macro 走输入侧面（80/80 有告警）；成本：材料生成 10 新标的 ≈200 次调用，离线腿 0 次。口径注：合计不一致比人口 = 可终裁人口（排除输入侧面型）——此前 0.702 的差异是把 stale_macro 80 个无拦截语义单元算进了分母。统计口径（`diff_mean` 点估计 / 标的配对 / 维度适用性 / 快照 digest / 报告状态头契约）见 §1.5，本小节不重复；judge 材料口径切点（`debate_history` 锚点骨架行）见 §2，跨该切点的 debate_quality 不可直接比较。

**族 A 补测批（2026-09-17）**：
- **A5（illegal_price）实测**：拦截率 **1.000**（16/16，4 可执行标的；b=16 c=0）——A5 是价位幻觉的唯一防线（关态即全放行）。前置修复：材料快照漏存 `price_levels`（sanity 消费面）→ 4 个「ok」实为「没测到」，已判 void 并重跑（commit aa17358）。
- **A4（单点修复回路）实测**（新建正文一致的稀疏 value_mismatch 单元，20 标的注入腿 + 2 自然命中单元，实耗 22 次调用）：**读数须并列三栏**——① 终稿留错 **0/20**（改写值经单条校验 20/20 改对）；② 流水线记账（`value_mismatch_repaired` 口径，即登记主指标「修复前后真 FAIL 率差」）**4/22**，delta 0.182，**低于预登记 80% 阈值**；③ 全库范围仲裁 **0/22**（观测装置缺陷，生产口径是按分析师，`run_citation_chain(claim_agents=…)`）。差额 16/22 的归因 =「同分析师另有其它 FAIL → `all_passed=False` → 不记账」→ **incident 029**（记账口径与收益脱钩；处置候选待 owner）。自然腿 2/2 定位落空（正文值型 ≠ 申报值型）→ 待人工终裁（`tests/validation/2026-09-17-a4-natural-cases.csv`）。**据此「A4 数值改对 20/20」与「流水线记账 4/22」是两件事，不得混读。**

- **A1（确定性指标注入）实测（2026-09-17，10 标的 × 2 态，102 次调用）**：主指标（计算型 claim 数值错误率差）**不可测**——OFF 态可重算根 claim 塌缩到 4 条（ON 319 条）、10 标的中 6 个分母为 0 判 void，触发预登记停止规则②（先归因后读数）。**归因结果**：塌缩成因是「LLM 不自算派生指标」（改回落原始报表路径）→ 登记主张的「自算算错」本批无样本（未被证伪，是没发生）。**机制价值改读产出面**：compute 根 claim 占比 **58.6% vs 2.3%**、claim 总量 **606 vs 388（-36%）**、路径不可解析率 **0.2% vs 13.4%**（缺席时数字失去可核对锚，47/52 条为 `financial_indicators.<年>.<指标名>` 形态解析不出）。前置修复：材料快照缺 compute 输出键 115 个（**incident 030**，白名单漂移第二次）→ 排除式 + 完整性守卫 + 零 LLM 回填。族 A 至此 **A1–A7 全部有实测读数**。

**P2 族 B 首批读数（2026-09-17；§19/§19.1）**：材料腿 = full × 20 标的（19 次调用/标的，累计 380 次）+ analysts 对照臂；**B3 风控数字出处率 0.952（20/21）**、**B4 sanity 首判打回 1/7**（000333 target 越参考带）——两腿 n=7 属**无分辨率**，只作描述性读数（owner 裁决 2026-09-18：维持描述性、不扩样，读数永久带 n=7 脚注）。**结构发现**：消融变体图不含 sanity 节点 → B4 读数由生产同一实现补算（单次校验、不含打回回路）。判定腿（**均 provisional，未过校准门控**）：**B1 吸收率 0.786**（196 行）、**B2 修正率 0.198**（111 行）、**B5 盲评 full 20 : analysts 0**——B5 按「整齐得可疑先解释」判定为**同义反复口径**（analysts 臂无决策章节而判据是「更有助于决策」），不得读作层价值，两臂须都含决策层（plus_debate vs full）。校准材料 `tests/validation/2026-09-17-p2-calibration-{b1,b2,b5}.csv` 待人工标注（≥0.80）。

**修复记账口径切点（2026-09-18，delta eval-driven-contract-fixes 任务 3，incident 029 处置）**：
`value_mismatch_repaired_claims`（按 claim：重校验后目标 claim PASS 即计）上线并接管
`citation_analyst_true_fail`；旧 `value_mismatch_repaired`（all_passed 口径）保留一轮作
跨口径对照、标 deprecated。**A4 主指标「修复前后真 FAIL 率差」跨此切点不可直接比较**——
此前实测 4/22（all_passed 漏计），新口径下同批应为 ≈20/22。消融 A4 读数引用旧批次时须带此脚注。

**P2 校准收口（2026-09-18，§19.2）**：B1 一致率 **0.875**（40/40=预填稿全文照录，≥0.80）→ **有条件转正**（owner = 口径共建 + 8 行逐条裁决 + 终版全量照预填，非独立标注，一致率含机器-机器成分；下腿按采样协议 v2 补独立抽查）；B2 **0.906**（32/32，判词 v3）→ **转正**：owner 就全部 4 条分歧行逐条裁决（3 站预填、1 站机器），其余照预填；终读修正率 **0.22** vs 机器 0.125（机器口径偏严经裁决确认）。**B1 字面新增阈值判死**（全区间最佳 0.05 仅 0.65 一致率，预测新增 10 vs 人工 22）→ **「是否新增」并入判定腿，B1 主指标 = 判定腿双问（新增 + 吸收）**，上表「B1 风险点增量率（nli）」自此失效；字面相似度仅留排序/采样辅助。人工终裁（终版=预填稿照录）：新增率 **0.45**（owner 首轮独立判读 0.55——报区间 0.45–0.55）、**新增点吸收率 0.61 vs 回收点 0.91**（决策层偏接旧内容）。judge prompt v2 已试并**阴性收口**（§19.3，160 次调用）：双问+通道分账+顶撞判例一次全上未过门（0.825/0.750/0.690），单变量探针 v2a 证明**新增维度封顶 ~0.75**（边界行固有模糊，人工亦翻案）→ 新增率以人工区间 0.45–0.55 为准，机器新增判定仅作辅助观测（0.75 披露）；吸收/修正继续用 v1（0.875/0.906）。无源增量首证：000858「机构资金持续撤离」= 推断冒充 `kind=data` 且无锚、与上游唯一资金流发现方向相反（grounding 扫描已收口：bg-v1 校准一致率 **1.000**（14/14 终裁），无源断言率 **8/64 = 0.125 转正**——r1 kind=data 论点中每 8 条有 1 条含无源断言，共性为推断冒充数据；8 条清单 owner 终裁确认，bear prompt 处置候选已登记，§19.5）。

---

## 2. 时间线（每轮一行，收口时追加）

| run | HEAD@启动 | 关键变更 | report_rel | debate_q | dg | consistency | citation_pass¹ | citation_cov | blocked/真错/归一² | judge_fail |
|---|---|---|---|---|---|---|---|---|---|---|
| r1 09-10 13:13Z | e92129a+judge端点 | delta 实施后首轮 | 5.0 | 4.38 | 2.75 | 3.63 | 0.25 | 0.861 | 未拆报 | 0 |
| r2 09-11 00:16Z | d621acb | FM 审批对象+重试校验+d_g v6+JSON 完整 | 4.86 | 4.33 | 3.89 | 4.67 | 0.111 | 0.919 | 未拆报 | 0 |
| r3 09-11 02:08Z | 4f0db3f | 覆盖率并行语义+预算放宽+evidence 逐条 | 5.0 | 5.0 | 4.22 | 4.78 | 0.111 | 0.918 | 未拆报 | 0 |
| r4 09-11 07:36Z | 322ce18+容忍补丁 | 阶段 0–5 全量：重试停用+校验器归一+分型回声+auto-claim+三层拆报 | 4.93 | 5.0 | 4.0 | 4.89 | 0.333（blocked 6/9） | 0.837¹ | 6/9｜1.89/条｜5.22/条 | 0 |
| r5 09-12 03:17Z | fa9f72b | 6.2 重评全量重跑（fa9f72b 渲染修复生效：辩论预算 24000）；东财数据源当日大面积不可用（百度+腾讯回退），对比含环境混淆 | 5.0 | 5.0 | 4.22 | 5.0 | 0.556（blocked 4/9） | 0.879 | 4/9｜0.78/条｜8.0/条 | 0 |

¹ r1–r3 的 `citation_pass` 是旧口径（FAIL=0，含校验器误报），与 r4 起 `citation_blocked` 不可直接比较。
² 拆报四项自 r4 起记录：verifier_normalized 5.22/条（echo 34+percent 13，校验器解析债）、unverifiable text 3.11 + unregistered 4.0（每条 deep）。r4 收口详见 [2026-09-11-citation门禁整改r4收口报告.md](2026-09-11-citation门禁整改r4收口报告.md)。
¹ r4 coverage 0.837 为重试停用后的真实首轮值（r2/r3 的 0.92 含 2–3 轮补 claim 重跑），不可直接对比。

人工校准：round5（53 行）全线未达标（整体 Spearman -0.36 / MAE 1.81 / 方向一致率 32%）→ 材料与 rubric 修复多轮 → **round7（41 对终裁后终值，r3）全维度达标：整体 MAE 0.342 / 方向一致率 97.6%，judge 自 round7 起可用**（终裁：4 行 judge 的 source 归属扣分成立改 4、1 行 judge 误判维持人工 5、1 行灰区维持 5；详见 [2026-09-13-round7-judge校准报告.md](2026-09-13-round7-judge校准报告.md)。rubric v7 四项改动已定稿待 bump：debate 5 分收紧 / source 归属三层判法 / 组合 claim 归属规则 / 解读失当强化）。

**消融口径切点（2026-09-15，delta `update-ablation-driver-parity-and-report-status`，未跑批）**：① `aggregate_results` 点估计字段 `diff_median` → `diff_mean`（口径本就为均值差，见 §1.5），历史产物 JSON 复算须按新名读；② 跑批驱动 `ablation_pilot.py` 的维度过滤改调库侧 `_applicable_dims`（此前自带硬编码分支 → plus_debate 的 grounding/consistency 照评，即 n10 批次 #112 伪影的来源），并在每条 run 前核验快照 digest；③ `evals/ablation/results/pilot.md` 标 `superseded-by` 指向 n10 报告并就地撤回简历素材段结论；④ n10 报告（09-03）补四处适用口径披露——**该批次 judge 未校准**（早于 round7 达标 10 天）、#112 伪影存活（其 full−plus_debate 的 grounding/consistency 两条 Δ 标为无效比较）、有效 n=3、citation_pass 不作层增量解读。**据此，n10 批次的可成立结论收缩为「未获统计支持」，其成本数字（辩论层 +7.9% / 决策+风控层 +30.1% / 裁到 analysts 省 28.7%）仍有效但不足以支撑裁剪决策。**
**judge 材料口径切点（2026-09-16，delta `add-debate-argument-anchors`，未跑批）**：debate_quality 的 `debate_history` 材料在收敛骨架行后新增「【锚点覆盖】bull a/b｜bear c/d｜风控 e/f（含拆项）」骨架行，每条论点行加 `[kind 状态]` 前缀（状态取自 `debate_anchor_checks`）。**跨此切点的 debate_quality 分数不可直接比较**；rubric 版本不变（v6），`points` 契约不变。确定性指标 `argument_anchor_coverage`（§1.2）进评估器/实验报告/消融 run 记录（value + 拆项）。首轮真实链路实测（600519 ×2）：覆盖 16.3% / 22.2%，`unresolved`（申报了锚但不可解析）占 51%/44%，`unspecified` 0、`missing_required` 0–1；逐条归因为**路径格式/引导问题**（自造 `fundamental./macro./technical.` 前缀、描述混入锚点、event 整句改写），**无编造**——prompt 迭代候选已登记（见 `tests/validation/2026-09-16-add-debate-argument-anchors-validation.md`）。
**消融 v2 口径切点（2026-09-16，delta `revamp-ablation-v2-causal-claims`，未跑批）**：① 消融聚合的 citation 腿接入四桶拆报，`citation_pass` 标量退出层增量比较路径（标量仍产出、不再进层增量裁决）；② 驱动薄壳化——`ablation_pilot.py` 的 judge 判分与 `judge_vars` 材料落盘移入库侧，驱动只留续跑/记账/coverage 包装；③ 口径先行登记（§1.7）：因果下游指标准入 + 判定方式 code｜nli｜judge、校准门控 0.80 覆盖全部 nli/judge 指标、逃逸率分母=已终裁单元（未终裁 `rate=None` 不报 0%）、结论两句式 + MDE 强制；④ 结论注册表落地——报告头部生命周期字段 + `docs/evals` 索引（[`README.md`](README.md)）渲染状态；首批注册对象：`evals/ablation/results/pilot.md` 标 `superseded-by` 指向 n10 报告、n10 报告标 `active`，其余存量文档列入索引「未标注生命周期」（未标注 ≠ 作废）。**跨此切点的消融层增量结论须按新口径读**：v1 的 judge 四维层增量与 `citation_pass` 标量读法不再作为层增量裁决依据。

---

## 2.1 round7 预登记分析计划（标注前锁定，防事后口径漂移）

- 数据：`judge-sample-round7-blind.xlsx`（人工 human_score + confidence）× `judge-sample-round7-judge.jsonl`（judge 分，r3 运行时落库，已冻结）
- 每维计算：MAE、方向一致率（>3 分界）、judge 分有方差的维度另算 Spearman（report_relevance / debate_quality judge 全 5，Spearman 不计）；整体 MAE / 方向一致率
- 判定：MAE ≤ 1.0 且方向一致率 ≥ 0.7 视为该维 judge 可用；Spearman ≥ 0.5 为加强项。n=9 的维度**不单独下结论**，与 round8 合并后判
- 附加：人工 confidence 与 judge confidence 的相关；人工低置信行单独列出复核
- 与 round5 对照：只比方向（是否从「judge 系统性高 1–2.6 分」收敛），不比绝对值（材料/rubric 不同口径）

| r8 09-13 21:32Z | cdca5b1+027+v7 | 基线重建轮（三变量：v7 rubric / 派生指标喂回 / 027 价位校验回路激活）；dg 3.56 系 v7 归属+解读失当扣分生效（契约抽验 4 条低分全中 v7 规则）；blocked 4/9 | 5.0 | 4.22 | 3.56 | 4.89 | 0.556（blocked 4/9） | 0.835 | 4/9｜1.22/条｜5.33/条 | 0 |
| r9 09-14 00:37Z | afd9d1f+v8 | 混合变量轮（材料升级：consistency 补 Trader 方案节 + debate 收敛骨架行；rubric v8 三版本两判例）；审计：dg 归属扣分全为真错安（判例无误放）、Trader→RJ 静默推翻核对首次可判、debate 5 分档仍漏判 ≥3 行（v9 候选：强制枚举）；blocked 6/9 | 5.0 | 4.67 | 3.44 | 4.67 | 0.333（blocked 6/9） | 0.843 | 6/9｜1.44/条｜6.33/条 | 0 |

人工校准：round8 盲标环节改为**维护者代裁**（owner 拒绝再投入标注时间；非盲审计口径，不能当独立 MAE）——41 行逐条核对：36 行一致、5 行分歧全部 ±1 档且方向均为 judge 偏严/规则执行不一致、无虚高，维护者口径 MAE 0.122 / 方向一致率 100%。**judge 自 round7 起可用结论维持**；两条 v8 候选改动见下方待决策。详见 [2026-09-13-round8-维护者代裁报告.md](2026-09-13-round8-维护者代裁报告.md)。
## 2.2 round8 预登记（基线重建轮，2026-09-13 启动）

- 实验：`round8-v7-rubric`（dataset a-share-analysis-v1），HEAD 含 rubric v7 + 两个 delta 实现 + incident 027 修复
- **三变量混合轮（不可纯归因 v7）**：① rubric v7（dg 三层判法归属/组合claim规则/解读失当强制核对 + debate 5 分收紧）；② 派生指标喂回风险辩论 context（deterministic-derived-metrics）；③ incident 027 修复激活价位校验 fail→打回回路（trader 行为可能变化）。对比 round7 时按桶归因，不把任何差异直接记到 v7 头上
- 收口动作：健康检查 → 契约抽验（v7 生效：judge 是否开始扣归属层/解读失当）→ 导出盲标表 → ~~owner 标注 → measure 合并~~（**实际路径**：owner 拒绝标注，改为维护者代裁审计，见上）
- 附加观察：trader 价位申报率（E2E 3/3 次 None，价位为 buy/sell 承重参数，考虑必填化评估；**round9 追加证据**：宁德 04baff5c 为 buy 决策仍 entry/stop/target 三价全 null，止损仅以散文「近期前低下方」描述——连续 4 轮 0 申报，report-render-operational-params 4.2 真实报告核对持续被堵，价位必填化建议升级为独立 delta）；derived_metrics 真实数据路径补核（E2E 未覆盖）

## 2.3 round9 预登记（混合变量轮，2026-09-13 立项，delta upgrade-judge-material-v8-rubric）

- **变量声明（混合轮，按桶归因，不把差异单一归因）**：① judge 材料升级——consistency 加【Trader 方案】节（trader_plan，Trader 原始方案 ≠ final_trade_decision）、debate 材料加收敛信号骨架行（确定性统计，标注「供参考」）；② rubric v8 三版本递增（debate 3→4 论点标头定性判例 / dg 7→8 多来源归属判例 / consistency 3→4 静默推翻核对）
- 对照基线：round8（judge 分 + 代裁 41 行审计，非盲口径见代裁报告）
- **定向验证点（round8 代裁误判样本是否被 v8 纠正）**：① 比亚迪 3e316c01 型「多来源同判归属」不再误扣（dg 4 分档是否上移）；② 美的 41a90c99 / 宁德 6b3d5e05 型「论点标头纯定性论点」是否被降 4（debate 5 分是否收敛至 ≤1 条/9）
- 预期方向：dg 均值小幅上移（4 分档收窄）、debate 均值小幅下移（5 分档收紧）、consistency 材料完整性提升后均值不变或微调——三者均为 rubric 判例的预期效果，非能力变化
- 收口动作：健康检查 → 契约抽验（判例生效证据）→ runs.jsonl + 时间线 → 代裁审计（重点两条判例）→ metrics.md 记录
- 排除项：材料骨架行升级（辩论收敛 + Trader 方案）与 rubric v8 不改变 5 层流水线行为，citation 链路与 golden gate 不受影响
- **收口结果（2026-09-14）**：17/17 行齐、judge_failures=0。定向验证：① dg 多来源归属判例达标（比亚迪 dg=3 三处扣分逐条复核全为真错安，判例无误放；平安 dg=5 干净证据链正常给满）；② consistency Trader 方案节达标（茅台 d46e0942 首次核出「RJ 将 Trader light 修正为 none 且已说明」，宁德/中芯参数演进被正确定性）；③ debate 5 分档判例**未达标**——4 分档严格引用判例语言，但 5 分档抽查 3 行（宁德/美的/平安银行）论点标头均含纯定性论点被漏判，与 round8 同构 → v9 候选。详见 [2026-09-14-round9-v8审计.md](2026-09-14-round9-v8审计.md)。citation_pass 0.556→0.333（blocked 6/9）为新生成方差（citation 链路无变更），follow-up 观察不处置

## 2.4 round10 预登记（debate rubric v5 单变量离线重判，2026-09-14）

- **单变量**：仅 debate_quality rubric v4→v5（加强制枚举动作：评分前逐条标注论点标头「数据/事实」或「纯定性」，任一纯定性即封顶 4）。dg/consistency/report_relevance rubric 与全部 judge 材料、5 层流水线均不变
- **方法（纯归因，不重跑管线）**：对 round9 的 9 条 deep trace，从 Langfuse 取落库的 debate judge 输入材料（同一批辩论内容），用 v5 rubric 离线 run_judge 重评（tests/scripts/rejudge_debate_v5.py）。同内容 + 仅 rubric 变 = 分数差异可纯归因到 v5
- **定向验证点（round9 审计漏判样本是否被 v5 纠正）**：宁德 04baff5c / 美的 20712f2a / 平安银行 a163494e 三行 v4=5（标头含纯定性论点被漏判）→ v5 应降 4；其余 6 行 v4 分应基本稳定（4 分档已正确执行判例，不应因 v5 进一步下压）
- 判定：v5 满分行数 ≤1/9 且三条漏判样本全部降 4 → 判例达标；若仍漏判 → 强制枚举模式对 debate 无效，需换机制（如 judge 输出结构化枚举字段后程序校验）
- 收口：重判结果落 jsonl + 本节追加结果 + runs.jsonl（标记 offline-rejudge，非全量实验）
- 排除项：不重跑 pipeline、不写业务 score（dry-run），Langfuse 仅可选 --write 落 v5 对照分
- **收口结果（2026-09-14，8/9 行）**：茅台现金流 5f179e87 因 Docker Desktop 停机未能拉取材料（其 v4=4，不影响判定）。**未达预登记达标线**（满分 2 行 >1；宁德 04baff5c v5 仍 5——「价格战加速产能出清利好龙头份额集中」漏判），但净效果显著正向：对照审计 ground truth（8 行全应得 4），准确率 v4 2/8 → v5 6/8（美的/比亚迪/平安银行/中芯/茅台 5 行 5→4 纠正），均值 4.75→4.25（方向=收紧）。**一个回归**：招行 53448e4b v4=4（曾正确抓住「银行股破位速度快」定性论断）→ v5=5 漏判——LLM 自我枚举是随机的，不是确定性的。**结论**：prompt 内强制枚举降低但不消除 5 分边界漏判，触发预登记 fallback——后续候选=judge 输出结构化枚举字段 + 程序封顶（代码强制 cap，不依赖 LLM 自律），已登记待决策。v5 保留（当前最优版），残留漏判如实记录

## 2.5 round11 预登记（debate rubric v6 程序封顶单变量离线重判，2026-09-14）

- **单变量**：仅 debate_quality rubric v5→v6（输出契约加 `points` 结构化枚举字段 + `run_judge` 按枚举由代码封顶 ≤4）。dg/consistency/report_relevance rubric、judge 材料、5 层流水线均不变；实现随 delta `judge-enumeration-cap-and-ablation-materials` 落地
- **方法（纯归因，不重跑管线）**：对 round9 的同一批 deep trace（round10 已重判的 8 行 + 若可拉取则补 5f179e87），用**同一份 debate 材料**（Langfuse 反解，与 round10 同源）以 v6 离线重判（tests/scripts/rejudge_debate_v6.py），与 round10 落库的 v5 分逐行对比
- **定向验证点**：① round10 漏判样本宁德 04baff5c（v5=5）→ v6 应因枚举出纯定性论点被程序封顶 4；② round10 回归样本招行 53448e4b（v4=4 → v5=5）→ v6 分数与封顶证据须可解释（若枚举出纯定性标头则应 ≤4）；③ 其余 4 分档行不因封顶继续下压（封顶只压 >4，`min(score,4)`）
- 判定：满分（5 分）行数 ≤1/8 且宁德降 4 → **机制达标**（判据由代码承担）；若 `enumeration_missing` 行数 ≥2（judge 未遵 points 契约）→ 机制未生效，需换实现（强制 JSON schema / 两段调用），如实记录
- **追加协议（实测发现后定）**：单次调用在 5/4 边界**双峰翻转**（宁德同材料 n=13 次：4 分 7 次 / 5 分 6 次）→ 重判与消融判分统一改为 **K 次均值**（`run_judge_mean`，K 经 CLI 传入；均值而非中位：p≈0.5 双峰下中位不降翻转概率），每次分数与极差随行落盘
- **收口结果（2026-09-14，8/8 行，K=3 均值）**：**预登记判定达标**——① 满分（5 分）行数 v5 2 行 → v6 **0 行**（≤1/8 ✓）；② 漏判样本宁德 04baff5c v5=5 → **v6=4.333**（scores [4,5,4]），理由原文引用「价格战加速产能出清利好龙头份额集中」并判为纯定性（与 round9 审计 ground truth 一致）✓；③ `enumeration_missing` **0/8**（judge 遵守 points 契约，机制生效）✓。8 行 v6 分布：4.333×5 / 4.0×3（对照审计 ground truth「8 行全应得 4」，平均偏差 ≈0.17）。**两个副作用如实记录**：(a) **取值域压缩**——v6 后 debate 分数全部落在 4.0–4.333，5 分档在实际样本中近乎不可达（真实辩论几乎总有个别纯定性标头）→ 该维度对消融的**分辨力受限**，debate 层增量预期≈0，读数须与「纯定性条数 + 封顶理由」并读；(b) **调用级噪声普遍**——K=3 中极差>0 的行 **5/8**（不止宁德），即单次调用抽样在 5/4 边界普遍会翻，K 次均值是必要而非保险。产物 `evals/judge_calibration/data/judge-sample-round11-debate-v6.jsonl`（逐行含 scores/spread/纯定性标头）+ 验证报告 `tests/validation/2026-09-14-judge-enumeration-cap-and-ablation-materials-validation.md`
- 收口：重判结果落 `evals/judge_calibration/data/judge-sample-round11-debate-v6.jsonl` + 本节追加结果 + runs.jsonl（标记 offline-rejudge，非全量实验）
- 排除项：不重跑 pipeline、不写业务 score（dry-run 默认）；`--write` 仅在需要 Langfuse 对照分时使用

## 3. 待终裁 / 待决策

**r2 非 PASS 137 条归因**：~~待终裁~~ **已闭合（2026-09-13，维护者代裁，证据链齐备）**。对照表 `tests/validation/citation-r2-nonpass-归因对照表.md` 机器分桶：81 结构不可验→分型 + 34 校验器误报 + 22 待终裁；22 条决策单 `tests/validation/citation-22条待终裁决策单.md`（✅ 终裁完成）：**全部「非幻觉」**——13 行 r4 同族全 PASS（新规则吸收）、8 行混合族残余全落已知结构不可验形态（比较/解读句 + quarterly_trend 期段路径）、2 条列名单位后缀缺口已修复（metric_vocab 补「加权每股收益(元)」别名）。owner 如需可对决策单抽查；副产品 follow-up 两条见下段。

**待 owner 终裁（round7 校准，2026-09-13）**：~~已完成~~——4 行 judge source 归属扣分成立（人工改 4）、1 行 judge 误判（维持人工 5）、1 行灰区（维持 5）、1caf1f7b 单向解读成立（维持 3）、7e6bc8bc 改 4、5f41a49a 补填 5。终值：整体 MAE 0.342 / 方向一致率 97.6%。rubric v7 四项改动清单已定稿（见校准报告）。

**校验器 follow-up（2026-09-13，22 条终裁副产品）**：① 比较型差值重算——~~开放~~ **已落地（2026-09-14）**：数值差值申报走双端重算 + 符号校验（方向未申报显式计覆盖缺口）；② quarterly_trend 期段定位补全——**已落地**（ff26b71）。派生键注册补齐 8 键 + 两条门禁（引用覆盖 `test_recompute_registry_covers_all_compute_outputs`、图通道 `TestNodeOutputChannels`）随 delta `close-citation-coverage-gaps` 落地（见 `tests/validation/2026-09-14-close-citation-coverage-gaps-validation.md`）。

**待决策**：
- ~~#2 surgical 单点修复（<3 处失败真值回填）与重试准入的关系~~ **已落地（2026-09-14）**：真错口径含修复回填（`citation_analyst_true_fail = 残余 FAIL + value_mismatch_repaired`，`citation_node.py:411`）；修复数单独入拆报 `citation_surgical_repaired`；授权与三条护栏固化进规范（`citation-verification`「单点修复的自动处置授权与边界」，delta `2026-09-14-document-surgical-repair-policy`）
- #4 置信度锚定（RM/RJ/FM 扎堆示例值 0.50–0.60；r3 FM 动作已有多样性，确认后改示例或加校准指令）
- #6 「风险提示」章：报告加顶层章 vs 改 must_cover 期望
- 分析师节点独立 span（根 span 元数据覆盖的根治方案，当前以 `degradation.{agent}.r{n}` 键止血）
- 覆盖率指标在「逐条回应成常态」后无区分度（r3 全 4/4、5/5），是否保留进 judge 材料
- consistency / decision_grounding 材料缺【Trader 方案】节——Trader→Risk Judge 的转向是否静默推翻无法核对（round8 材料版本落地，本轮 round7 口径：只评 RM→RJ→FM→报告四层）
- **v8 候选（round8 代裁发现，2026-09-13）**：① debate 5 分档执行不稳定——「个别定性论点降 4」在 4 分档执行严格，但美的/宁德两行漏判纯定性论点给 5，建议 5 分判例进 rubric few-shot；② dg 归属层对「同一评判在多来源出现」判定偏机械——claim 在 debate_bear 与 research_manager 均有原话时 judge 只认单源（比亚迪 ref7 误扣），v8 补「任一真实来源即合法」判例
- **debate 5 分边界可靠性（round9 审计 → round10 v5 → round11 v6）**：~~待决策（v6 机制换装）~~ **已落地（2026-09-14）**：delta `judge-enumeration-cap-and-ablation-materials` —— ① judge 输出契约加 `points` 结构化枚举（data/qualitative）+ 代码按枚举封顶（不依赖 LLM 自律），枚举缺失 fail-open 但标 `enumeration_missing` 落库；② 判分协议改 **K 次均值**（`run_judge_mean`，K 经 CLI 声明），每次分数与极差随 run/重判行落盘；③ 消融材料（judge_vars）按 run 落盘 + 标的/重复次数参数化 → rubric 变更可离线重判。round11 判定达标（见 §2.5 收口结果）。**遗留（新登记）**：v6 取值域压缩到 4.0–4.333 → 该维度对消融层增量的分辨力受限；如需恢复区分度，候选=（a）把「纯定性论点条数」作为连续指标入消融（当前枚举粒度在「子句 vs 整行」间不稳，需先在 rubric 里钉死粒度），（b）改用成对比较（pairwise）替代 1-5 绝对刻度，（c）把定性/定量判定前移到辩论节点（结构化输出 key_arguments 时自带 data 锚点，确定性输入信号）——三者均需新 delta 与人工标注对照
- **消融跑批前的两处小账（2026-09-14 盘点发现）**：① `tests/scripts/ablation_aggregate_90.py` 仍指向旧工作树路径 `.worktrees/evals-boot/reports/ablation/resume.json`（新驱动默认写 `reports/ablation/resume.json`）且硬编码「< 90 条即退出」——跑新批次前需参数化（`--resume/--min-runs`）或改默认路径；② 该脚本未读新增的 `judge_detail`（scores/score_spread/qualitative_points）——90 条权威报告建议附「judge 极差与纯定性条数」分布，使 debate 维度取值域压缩的影响可见
- **hosted 实验路径仍是单次判分**：`python -m evals.run`（`reports/evals/*.json` 与跨实验对比的数据源）的 judge 仍单次调用，带 ±1 调用级噪声（本轮只把**消融**路径改成 K 次均值）。口径一致性候选：也给该路径加 K（成本 ×K、且会改变与历史报告的绝对分可比性）——需 owner 定；在此之前跨实验比较请按「同噪声下的相对差异」解读
- mypy 全仓 75 个既有错误（本次触碰文件为 0），是否立清理任务
