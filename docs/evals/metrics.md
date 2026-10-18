# 评估指标台账

唯一查询入口：口径定义看这里，历史看时间线表，机器可读摘要看 [`metrics/runs.jsonl`](runs.jsonl)。
分工：Langfuse Scores = 逐 trace 明细真源；`reports/evals/*.json` = 单次实验全量产物（本地）；本文件 = 跨 run 台账。每次实验收口时更新一行，并注明当时的 git HEAD。

**基线切点**：incident 026（`docs/incidents/026-citation-gate-misattribution-20260911.md`）之前，`citation_pass` 把校验器误报算在分析师头上（r2 终裁：FAIL 桶 100% 误报），不可与 r4 起的拆报指标直接比较。

---

## 1. 口径定义

### 1.1 judge 维度（LLM-as-judge，rubric 见 `evals/judges.py`，人工对照见 `evals/judge_calibration/`）

| 指标 | 口径 | rubric 版 | 备注 |
|---|---|---|---|
| report_relevance | 切题 = 回答了用户的问题（非主题相关）；v4 起 5 分档锚点：查询显式子问题逐一回答才给 5 | v4 | **deep only**（quick 段零方差停评，2026-09-22 切点） |
| debate_quality | 辩论交锋质量（含交锋覆盖率对照锚点） | v2 | deep only |
| decision_grounding | 决策论据逐条核对 evidence_refs ↔ source | v5 | deep only；材料 v6 含风控指标+风险辩论 |
| consistency | 各层方向一致性（approve 语义：批准对象是 Risk Judge 方案） | v3 | deep only |
| judge confidence | judge 对评分依据充分性的自报把握；材料不足 MUST <0.5 | — | 以 `[conf=x.xx]` 前缀落 Scores.comment |

**判分协议（2026-09-22 切点，delta `switch-hosted-judge-to-k-mean`）**：hosted（`python -m evals.run`）与消融同协议——judge 点估计 = **K 次调用均值**（`run_judge_mean`，K 经 `--judge-repeats` 声明、默认 3，产物 config 记 `judge_repeats`）；每次分数与极差随 Scores.comment 落库（`[K=3 scores=[…] spread=N]`，部分失败附 `fail=N`）；debate v6 封顶/枚举遥测取最低分那次调用。切点前 hosted 为单次口径（±1 调用级噪声），跨切点绝对分不可直接比较。

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

### 1.8 常规观测电池（delta `add-causal-observation-battery`，2026-09-22 起）

| 项 | 口径 |
|---|---|
| 定义 | 固定 P1 快照（20 标的，digest 核验）× 当前生产栈（prompt/模型）→ 三读数：**grounding 无源率 / B1 吸收率 / B2 修正率**（读数定义同 §1.7 族 B，判定实现复用库侧 `bear_grounding` / `family_b_*`，代码位置 `evals/causal_ablation/observation.py`） |
| 边界 | **观测不裁决**：无两臂对照，不产出任何层间结论句；读数定位为跨轮趋势对照（与上轮对照、人工解读），异常读数按 SOP 先分桶归因再处置（incident 026 纪律） |
| 校准门控 | 沿用 §1.7②：判定行 rubric 查 `CALIBRATION_REGISTRY`（b1-v1 0.875 / b2-v1 0.906 / bg-v1 1.000），查不到 → 该读数标 `provisional`，不得与历史轮直接对照 |
| 快照 | digest 与上轮登记值（`reports/ablation/observation/snapshot-digests.json`）逐标的核验，不一致显式失败禁止混轮；快照升级时登记新 digest 作切点，跨切点不比较 |
| 判定口径 | 全量判定（不用每标的闸门——闸门是实验批成本旋钮）。与 09-20 基线轮的口径对应：grounding 82 行/79 行、B1 250 行/251 行**同为全量可直接对照**；**B2 09-20 为 80 行闸门口径**（4/标的），本电池取全量（1,487 行）→ **B2 全量基线自 2026-09-22 起，跨口径不可比** |
| 触发约定 | 生产 prompt（`deploy_prompts.py` 发布）或模型栈变更后运行；收口时 runs.jsonl 追加一行（llm_calls 分材料腿/判定腿申报）+ 本文件时间线登记 |
| 基线轮 | 2026-09-20 观测轮（p2-batch-20260920：grounding 0/79 / B1 0.833 / B2 0.138）为第 0 轮对照基线 |

**观测电池第 1 轮（2026-09-22，delta `add-causal-observation-battery` 首跑，HEAD ec3122b）**：固定 P1 快照 × v28 生产栈（20 标的全量材料重跑 + 三判定）。读数：**grounding 无源率 0.049（4/82）**、**B1 吸收率 0.848（212/250）**、**B2 修正率 0.082（122/1,487，全量口径首读）**；判定调用 **1,820 次**（B2 全量占 1,487），材料腿 usage meter 未接线（导入修复已随 ec3122b 落地，本轮如实记 unknown）。对照第 0 轮（09-20）：B1 0.833→0.848（同口径 +1.5pp）；**grounding 0/79→4/82 初判「回退」经归因撤回**（2026-09-23：Fisher 双侧 p≈0.12 不显著，合并率 4/161≈2.5%，属小样本波动非事件；bear prompt 两轮间未变更）——4 条逐行归因全落「推断冒充数据·定性升格」桶（定价判断/趋势性/强度升格三型），初判判定成立，处置候选 A/B 待 owner 终裁，对照表 `tests/validation/2026-09-23-observation-grounding-归因对照表.md`；B2 跨口径不可比（0.138 为 80 行闸门口径）。产物 `reports/ablation/observation/observation-20260922-225708.json`（本地）+ `snapshot-digests.json`（下轮核验基准）。
### 1.9 Outcome 收益指标（delta `add-outcome-profitability-protocol`，2026-09-23 登记；启用切点见 §2）

「赚钱能力」（outcome 收益）的评估口径。口径变更 SHALL 先改本节再动代码；本节不复制读数，读数在收口报告与 `runs.jsonl`。

| 项 | 口径 |
|---|---|
| ① 主指标 | 逐决策 **T+20 交易日相对沪深300（000300.SH）超额收益**：均值（`excess_return`）与胜率（resolved_win/(win+loss)，±2% 中性带，沿用 track-record 判定口径，不另造定义）。均值人口 = long/short 全部已判定观点（resolved_win/loss/neutral 三态，排除 unresolvable；避免 ±2% 带截断偏差）；置信区间按标的簇 bootstrap（B=10,000）；胜率为描述性指标，结论句由均值 CI 驱动；`BENCHMARK_CODE` 若被环境覆盖须重新预登记（本口径即失效） |
| ② 人口划分 | 主结论仅基于可执行决策（long/short）；neutral（watch/hold）以**回避正确率** = avoidance_win/(avoidance_win+avoidance_loss) 作辅助指标，SHALL NOT 进主结论（实现见 delta `update-decision-settlement-contract`） |
| ③ 辅助观测 | T+5 / T+10 窗口由 daily_marks 派生，只观测不判定；不参与胜率与结论句；派生例程与口径同 §1.5 的均值差纪律（点估计给口径、非点估计不冒充） |
| ④ 红线 | settled 可执行样本 <10：SHALL NOT 报任何胜率 / 平均超额 / 赚钱能力措辞，只报样本数与「样本积累中」（与 track-record 基础统计展示门槛一致，泛化为评估纪律） |
| ⑤ 收口纪律 | 健康检查（结算成功率 ≥0.90 / 不可判定率 ≤0.10；两率同一分母 `settleable` = **已判定（含回避判定）** = `resolved_win/loss/neutral` 三态 **+** `status='avoidance'` 且 `avoidance_status` 非空，**再加** `unresolvable`，结算成功率与不可判定率互补 / integrity 零不一致 / 记账完整率——读数不设阈值）→ 异常行人工终裁（对照表落 `tests/validation/`）→ 两句式结论（显著方向带 CI ｜ 分辨率不足带 MDE；裸「未获统计支持」非法）→ 收口报告带生命周期字段（字面格式见 §1.7④；本文档不复现该字面标记）→ 本表 §2 时间线追加 + `runs.jsonl` 追加（`type: outcome-forward` / `type: outcome-backtest`）。integrity 为全库扫描（不受 source_type/since 过滤），其余读数按过滤口径（`integrity_scope` 字段随报告披露）。回避终态计入「已判定」为 delta `update-decision-settlement-contract` 的落地口径（2026-09-23 实施期修订，见 §2 切点注记） |
| ⑥ 泄漏控制 | forward 腿为金标准（零泄漏假设）；回测腿须过干净窗口判定 + 泄漏探针披露（默认阈值 0.60，实现见 delta `add-backtest-leakage-controls`）；深历史批次永久定位通路验证；两腿分歧先归因（口径见 `evaluation`「Forward 与回测双腿互证」） |
| ⑦ MDE 换算 | 胜率（单样本 vs 50%，α=0.05 / power=0.8）：MDE = 1.4008/√n；均值超额（σ 假设 10pp）：MDE = 0.2802/√n。n=30 → 胜率 25.6pp / 均值 5.1pp；n=100 → 14.0pp / 2.8pp。推断单元按标的聚类，ICC 折算有效 n 随读数披露（同 §1.7 推断单元纪律） |
| ⑧ cohort 跑批读数（辅助观测，不进主结论） | attempt success rate = `success/(success+failure)`（`skipped` 不计分母；`planned`/`attempted` 随报告披露）；`opinions_persisted` 落库率；`unlinked` 拆两桶（`unlinked_trace_missing` trace 未持久化 / `unlinked_no_opinion` 分析成功但无观点行）。实现 = `evals/outcome/cohort_readings.py::collect_cohort_readings`（join 经 `langfuse_trace_id`）；口径变更先改本节 |

首个预登记：`evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md`（门禁字段：主指标 / MDE / 决策阈值 / 样本量依据 / 停止规则 / 成本分型 / 泄漏控制；解析见 `evals/causal_ablation/preregister.py::OUTCOME_REQUIRED_FIELDS`，与因果消融的差异 = 无 rubric、增成本分型与泄漏控制）。

### §1.9-v2 结算显著性与信号一致性口径（预登记 2026-10-06，首批 T+20 结算前冻结）

**样本口径**：全部读数仅消费日主观点（add-prediction-pool-integrity：同 (symbol, 决策归属日)
取 created_at 最晚者；duplicate_of_day 行永不进入任何分子分母）。归属日派生与
settle_entry_price 同源（收盘前→当日；收盘后/非交易日→次一交易日）。

**IC/ICIR**：IC(月) = 当月判定完成的日主 long/short 观点中 resolved_win 占
(resolved_win + resolved_loss) 比例（resolved_neutral/unresolvable 不进；回避类单独成列）。
单期可判定样本 < 10 → 该期「样本不足」，不进 ICIR 序列；序列期数 < 6 → ICIR 不展示
（仅展示逐期 IC 与样本数；按月积攒，预计 2027-04 满足）。
ICIR 的 std = 总体标准差（ddof=0；量化惯例，Grinold-Kahn）（澄清 2026-10-06，无读数前修订）。

**蒙特卡洛零模型（敞口对齐）**：对任何对外报告的组合区间超额读数，产出具分布定位——
随机化对象 = 同 universe 内随机替换选股；约束 = 每决策归属日每方向（long/short/neutral）
注数与真实组合完全一致；抽取 = 归属日当日起作用的池内均匀随机（无前视）；次数 10,000
（种子显式注入可复现）；报告 = 真实读数在零模型分布中的右尾分位与 p 值（含真实读数本身的
保守 p）。「跑赢/跑输」类结论必须附分位读数，单独出现的点估计 = 口径违规。
日主可判定样本 < 10 → 不产出（沿 settled<10 红线）。
「每方向注数一致」中 neutral 注 = 零敞口、不占抽样槽位（long/short 槽位对齐即等价）
（澄清 2026-10-06，无读数前修订）；同 (归属日, 方向) 内抽取为无放回（对齐日主唯一性，
每 symbol 每归属日至多一条日主观点）（澄清 2026-10-06，无读数前修订，随 Task 5 裁决落地）。

**分母口径切点**：胜率/平均超额/回避正确率/样本量的「全观点 → 日主」切换登记为口径切点
（2026-10-06，切点时无任何已结算读数——纯声明性登记，无历史读数需重算/分段）。净值正式
积累自 2026-10-08 重启，天然无跨切点混算。

### 1.10 健康度/红黄绿灯行业口径（delta `update-financial-freshness-and-valuation`，2026-09-29 登记）

| 项 | 口径 |
|---|---|
| 定义 | 红黄绿灯/健康度评分新增行业阈值覆盖机制的行业实例：半导体设备（存货周转率 (1.2, 0.5) / 速动比率 (1.5, 0.6) / 应付账款周转率 (4.5, 1.5)，higher_is_better），校准依据 = 5 家代表公司（北方华创/中微/拓荆/芯源微/华海清科）FY2025 指标分布（design.md 附表） |
| 切点 | 覆盖生效起，健康度评分输出携带 `industry_override`（行业名+覆盖指标清单）；无覆盖时标注通用口径。**跨切点健康度分数不可直接对照**（拓荆 40 分为旧口径值） |
| 影响范围 | 仅灯色评判定性，不改四维度指标数值与年报序列口径 |

### 1.11 quote 主源口径（update-quote-primary-source）

- 2026-10-01 起：行情 quote 主源由东财 stock_zh_a_spot_em（全市场翻页）切换为腾讯 qt.gtimg.cn 单标的直查，东财降为回退1。触发背景：东财行情域（push2/push2his）对本机 IP 实施封禁（2026-09-26 起，实测 IP 级、指纹伪装无效）。
- market_cap 单位契约不变（元；来源值亿 ×1e8）。PB 存在腾讯/百度双源口径差（2026-09-30 拓荆样本 14.55 vs 14.71，净资产快照口径不同），主源命中即以主源为准，不做跨源融合。
- 腾讯 PE 字段（TTM 口径）不消费：quote 无 PE 时 compute 恒走 derived_ttm 推导口径。东财主源时代的 static 口径 PE 在生产链路不再默认出现——涉及 static 口径 GARP 的 eval 断言按缺失桶解释。
- **eval 对照期警示**：跨 2026-10-01 切点的 run 对比需标注数据源（尤其 PB 与相对估值输入）；runs.jsonl 后续行建议在备注列登记 quote_source。

### 1.12 战绩组合盯市/净值口径（delta `update-track-record-data-integrity`，2026-09-29 登记）

历史战绩页组合指标（净值曲线/年化/波动/夏普/回撤/风险分）的口径。口径变更 SHALL 先改本节再动代码。落地时 derived 表（daily_marks/equity_curve/agent_metrics_daily）全量重建，重建前读数以 incident 032 归因为准，SHALL NOT 作为基线引用。

| 项 | 口径 |
|---|---|
| ① neutral 盯市符号 | neutral（hold/watch）的 `daily_marks.cum_return` 按多头口径记录（= 标的相对参考价走势，与回避判定引擎 `judgment` 同号），SHALL NOT 取反；long/short 维持现行方向符号（long 多头、short 取反） |
| ② 组合人口 | 组合日收益/净值/风险指标聚合 SHALL 排除 neutral 观点的盯市记录——回避决策不产生持仓损益；long/short 按观点等权 1/N |
| ③ 净值口径 | 观点在组合内的首盯市日贡献 0 收益（现金口径）；组合日收益仅由相邻盯市日 `cum_return` 差分构成；入场参考价 SHALL NOT 参与净值与指标计算（展示口径保留在 marks 与观点详情页）；agent 与基准双线均以首个盯市日为基日归一 1.0 |
| ④ 交易日历 | 净值序列以基准指数日 K 的交易日历为骨架，覆盖首个盯市日以来全部交易日；空仓/整体缺数据交易日 agent 日收益记 0；基准净值按同一日历推进（基准收盘取自基准日 K）；基准行情不可得时降级 marks-only 口径并记 WARN |
| ⑤ 年化/风险 | 年化 = `final_nav^(252/n) - 1`，n = 交易日数（含空仓日），SHALL NOT 按有盯市日期数外推；波动 = 全序列日收益总体标准差 ×√252；夏普 = (年化 − rf)/波动（rf 默认 2%，`TRACK_RISK_FREE_RATE` 可配）；最大回撤基于全序列 NAV；风险分公式不变（§同 track-record-metrics spec） |
| ⑥ 参考价护栏 | 落库：quote 价与最近 K 线收盘偏离 > `TRACK_ENTRY_PRICE_MAX_DEVIATION`（默认 0.30，覆盖各板块涨跌幅限制）→ 拒绝采信 quote、降级 K 线收盘 + WARN；盯市：entry_price 与 created 后首个交易日收盘偏离 > 阈值 → skipped + WARN 不写 marks（append-only 冻结语义下不修存量 entry_price，供人工甄别） |
| ⑦ as_of | 总览 `portfolio.as_of` = equity_curve 最新数据日期，SHALL NOT 用快照写入日期（metric_date）冒充数据日期 |

### 1.13 策略族 scorecard 口径（2026-10-19 登记；裁决自 2026-10-18 调研 §4.2 P0；实施 delta `add-strategy-family-scorecards`）

**先口径后代码**：本节为口径登记——指标定义在此钉死后才实施；实施 delta `add-strategy-family-scorecards`
落地后，四槽读数见下方实现口径，战绩页其余口径（§1.9/§1.12）不受影响。度量对象 = **策略族**
（同一信号机制 + 同一执行形态的策略集合；当前在产族仅 T+20 cohort 观点线一条，
事件 lane / 转债条款等族增设时并入本框架）。

| 指标 | 口径 | 备注 |
|---|---|---|
| 净成本期望值（族主指标） | 族内已结算交易扣除全部成本（佣金万 2.5 低 5 + 印花税 + 滑点四档）后的期望收益；样本 = 族内全部已结算 T+20 观点 | **处置决策挂此指标**；与 §1.9 decision_return 的关系 = 扣费前/扣费后 |
| 最大回撤（族级） | 族内观点等权组合净值曲线（§1.12 口径派生）的最大回撤 | 组合人口/净值口径沿用 §1.12 |
| 容量估计 | 以标的成交额分位 + 滑点四档模型反推的容量上限（粗估，标注方法） | 调研 §1.1 容量-冲击框架的落地 |
| 尾部事件清单 | 停牌/涨跌停不可成交、退市、转债违约/强赎的发生次数与损失贡献（逐条列名） | fail 清单式，非聚合分 |
| 胜率 | 族内已结算净收益 > 0 占比 | **降级为展示指标**——不挂任何处置决策（调研 §3.1 负偏度形态：高胜率可配负期望） |

**判读纪律**：任何 scorecard 读数进入处置（调仓/下线/扩容）前，仍走分桶归因 + 逐条人工终裁
（incident 026 纪律不变）；族间比较 SHALL 同口径同成本假设。基线切点 = 实施 delta 合并日冻结，
此后跨切点绝对值不可直接比较（同 §1 切点惯例）。
**族定义**：观点线 `predictions`（`status ∈ {resolved_win, resolved_loss, resolved_neutral}`，与既有 prediction_stats 分母一致）
／执行线 `execution`（`paper_equity` 净值派生，已含撮合费模型）。

**四槽口径**：
1. **期望值**：毛 = 样本收益均值（观点线 = `raw_return`；执行线 = 组合级日收益均值）；净 = 毛 − 往返成本估算率
   （= `max(commission_rate × 100_000, min_commission)/100_000 × 2 + stamp_tax_rate + 中档 slippage_rate × 2`，
   参数同源 `config/matching_engine.json`，默认 ≈ 0.30%）；执行线毛=净（已含费）。样本 < 10 → null + `insufficient_sample`。
2. **最大回撤**：按日期聚合样本等权收益（`resolved_at` 前 10 字符）→ 复利净值 → `MDD = 1 − min(nav/峰值)`（输出 ≤ 0）；
   执行线直接用净值序列。结算日样本 < 20 → null + `insufficient_history`。
3. **容量提示**：最近 20 个样本标的（观点线 = 最近判定 symbol；执行线 = 当前持仓）近 20 交易日日均成交额（快照 `amount`）
   × 参与率 1% 的最小值 → 建议资金上限；缺数据标的剔除计数，全缺 → null + `no_liquidity_data`。
4. **极端样本**：观点线 `raw_return ≤ -0.15`、执行线组合日收益 ≤ -0.05（具名常量）；清单含 ticker/日期/收益。

**胜率定位**：`win_rate` 保留为展示字段（四槽之后）；MUST NOT 作为任何自动处置/门禁/告警输入。
口径：观点线 `win_rate` = `resolved_win` 占比（分母沿用 `prediction_stats` 既有口径，仅 long/short 已判定，
`neutral`/`avoidance`/`duplicate_of_day` 等既有排除项不进分母）；执行线 `win_rate = null`（不适用——执行线无胜率口径）。

### 1.14 市场拥挤度与总仓位系数（delta `add-crowding-regime-positioning`，2026-10-18 登记）

**指标**：小市值组成交额占比（按流通市值升序前 30%，停牌/零成交剔除；流通市值取数链 = 快照同源 spot（EM 原始列）
优先 → 腾讯行情批量兜底（真实市值字段；采用兜底源时指标行 `cap_source=tencent` 留痕），两源皆无/覆盖不全即当日不产行 + `cap_data_missing`）/
涨跌家数（pct_chg 符号）/ 涨停跌停家数（limit_up_sealed / limit_down_sealed）/
全市场成交额。**分位**：当日值在最近 250 交易日窗口内的百分位（`#{x≤v}/n`）；样本 < 60 → null + `insufficient_history`。
**判定**：触发指标 = 小市值占比分位、成交额分位；任一 ≥ danger 阈值（默认 0.9）→ danger、≥ elevated（默认 0.8）→ elevated、
否则 normal；系数映射 1.0 / 0.5 / 0.0（全部可配置）；分位不可用不触发且在 reasons 注明。**陈旧**：数据日期距判定时刻超过 7 个自然日（可配置；与 spec/design 的「>7」口径一致）。
**执行链**：目标权重 × 系数；系数 ≠ 1.0 逐票 `REGIME_SCALE`，摘要记状态与系数；缺失/陈旧 → 1.0 + `no_regime_data`。

### 1.15 业绩预告事件线（delta `add-event-driven-lane`，2026-10-18 登记）

**数据源与字段口径**：akshare `stock_yjyg_em`（东方财富业绩预告，按报告期拉取；本机实测 1.18.94 可用，列名见实现映射表）；
取数口径 = `预测指标` 为「归属于上市公司股东的净利润」的行（归母口径，与全库报表口径一致；源多指标行只取净利润行）。
字段：代码 / 名称 / 报告期（`report_period` 归一 `YYYY-MM-DD`）/ 预告类型（源取值原样落库，不做二次归类）/
**变动幅度下限与上限**（优先解析 `业绩变动` 文本「同比上年 增长|变动|下降 A% 至 B%」区间，方向词定符号——`下降`/`减少` 取负、
`增长`/`变动` 原样；无区间 → 下限 = 上限 = 源 `业绩变动幅度` 单值；均缺 → NULL，不折算 0）/ 公告日期 / 来源标识（`source="eastmoney"`）。
**可得时点**：`available_at` = 公告日期（`announce_date`）——下游判断、分诊输入与回填一律 `available_at ≤ 判断日`（时点截断，MUST NOT 前视）。
唯一键 `(代码, 报告期, source_record_id)`；`source_record_id` = `预测指标|公告日期`（稳定复合键，重抓幂等覆盖为最新值）。
抓取失败/空 → 返回空列表 + ERROR 留痕，MUST NOT 以零新增冒充成功。

**分诊合同与降级**：LLM 输出 `{verdict ∈ {outperform, in_line, risk}, reason ≤ 50 字, evidence: [{field, value}]}`；
非法 verdict / 超长 reason / 缺证据 = 拒收；单条调用失败（typed LLM error）或拒收 → 降级 `in_line` + trace warning，
不阻断本批其余记录；分诊结果落库含模型标识与 prompt 版本指纹（`load_prompt_with_meta`）。
prompt 权威源 `src/finance_agent/prompts/event_triage.md`，发布走 `scripts/deploy_prompts.py`（prompt-deploy-consistency 门禁适用）。
分诊输入预算 ≤ 12 字段（预告要素全量 + 快照表**实际可得键** `close`/`pct_chg`/`amount`，常量集中定义）；快照要素**逐行时点化**注入——
每行取「≤ 该行 `available_at` 的最近快照日」的该股快照字段，该日无覆盖（老公告/空库）→ 该行不注入快照字段（仅预告要素）并在日批摘要
计数留痕 `snapshot_ctx_missing`（MUST NOT 对回填批次共用单一快照日/前视）。

**入场 / horizon / 结算口径**：入场 = 可得时点（`available_at`，缺省回落 `announce_date`）**次一交易日开盘价**
（`fetch_kline(adjust=SETTLEMENT_ADJUST)` 后复权 hfq，与执行链同口径）；记账人口 = `verdict='outperform'` 且未入场者
（`in_line`/`risk` 不记账；全候选反事实读数为非目标，如需另立 delta）。
不可成交判定读入场候选日快照 `read_universe(as_of=entry_date)`——`is_suspended=1` 或 `limit_up_sealed=1` 挡买顺延
（**买入侧门禁语义**，与执行链 `risk_gate`/`matching` 一致；跌停板可买，不挡）；停牌真实形态（该交易日无 kline 行）由交易日历识别
`available_at → 入场日` 间缺失交易日 = 停牌日，并入 `note=deferred:suspended@<日期>`（多日逗号合并）；
快照行缺失/快照库不可读 → 退化为 **kline OHLC 判板**降级（一字板口径 = 开=高=低=收 且 `收盘 / 前一交易日收盘 − 1 ≥ 4.8%`
（含边界，`sealed_flags_from_ohlc` 与快照 D3 同源；前收取该标的上一交易日收盘，缺失不判板；OHLC 不完整 → fail-closed 顺延），并日志注明口径降级。
horizon 默认 **5 个交易日**（可配置）；结算日 = 入场日之后第 horizon 个交易日，结算价 = **该日收盘价**，
`raw_return = 结算价 / 入场价 − 1`（round 6）；结算日无 K 线行（停牌）→ **递延至复牌首日收盘结算**并留痕
`note=deferred:<复牌日>@<原结算日>`（与「取数失败」区分）；kline 取数失败/窗口未覆盖（参考价缺失）→ 保持 `open` +
留痕 `note=settle_pending:<原因>` 重试，MUST NOT 以 0 或估算值结算；结算留痕与入场顺延注记**拼接不覆盖**
（`;` 分隔，重试不叠写同款 pending 段）；已结算读数按 `settle_date` **降序**保留近期（超读取上限丢最旧，不静默丢最新）。

**家族隔离声明**：事件读数注册为策略族成绩单 `family_id=event`（`data_source="events"`、`cost_bearing=True`、`min_samples=10`、
`extreme_threshold=-0.15`），样本 = 已结算 `event_journal` 行（ticker=`symbol`、日期=`settle_date`、收益=`raw_return`）；
horizon 默认 T+5 独立于观点线 T+20，MUST NOT 混入观点统计（predictions 族样本只取 `predictions` 表已判定行，两者互不包含对方样本）。
事件线 v1 为纯观察/研究通道：不接 regime 系数、不进执行链目标（避免与 paper 台账双重记账）。

### 1.16 含退市股池与小票通道（delta `add-microcap-lane-guardrails`，2026-10-18 登记）

**退市清单口径（来源与字段映射）**：两所清单合并 —— 沪 `ak.stock_info_sh_delist()`
（实测列 `公司代码 / 公司简称 / 上市日期 / 暂停上市日期`，2026-10-18 本机 159 行）+
深 `ak.stock_info_sz_delist()`（实测列 `证券代码 / 证券简称 / 上市日期 / 终止上市日期`，本机 209 行）；
统一字段 `code / name / list_date / delist_date / source`。
**语义注明（不得误读）**：沪清单的日期列是「**暂停上市日期**」而非交易所正式退市日，本口径将其映射为
`delist_date` 并保留 `source="sh_suspend"` 供审计（深市源列名「终止上市日期」→ `source="sz_delist"`）；
消费方以 `source` 区分两类日期语义，MUST NOT 把沪源 `delist_date` 读作交易所退市日。
抓取失败/空 → 空列表 + ERROR 留痕（明细类约定），MUST NOT 以空表冒充成功；
**列漂移**：缺**代码列**或缺**退市日列** → 该源判不可用（`[]` + ERROR，不产「一批全 None 日期」的
半口径行冒充成功）；缺**上市日期列** → 降级保留行（`list_date=None` + WARN，如实标注）。
落库：`security_master` 扩展列 `delist_date` 与 `delist_source`（ALTER 增量迁移，先例 `selection_mode`；
`delist_source` = 日期语义来源的**持久审计留痕**，消费方据库内值即可区分沪/深语义）；退市清单经
`upsert_delisted_masters` 幂等 upsert（`COALESCE` 保值，不因部分拉取失败清空已知字段）；
**生产触发点** = 快照日批主表刷新分支（名单为空或每周一），与上市清单同频，失败只 WARN 不失败整批。

**池可见性（防幸存者偏差）**：对时点 t，标的可见当且仅当 `上市日 ≤ t ≤ 退市日`（含边界）。
`delist_date` 缺失按「未退市」处理（`list_date` 缺失时下界不约束——不因缺数据静默剔除，
与「无价格历史的退市标的如实标注缺失」同纪律，MUST NOT 静默剔除）；
退市标的价格历史经 `scripts/backfill_delisted.py` 按 `list_date..delist_date` 窗口补采
（复用既有回填通道，回填行不含状态位/前收盘，勿接 rebase/停牌合成链）；
源无历史 → `history_missing` 计数 + 代码明细如实标注，不伪造。
**断点续跑 = 整窗覆盖判据**（不只是「窗口内有行」）：窗口内有行、字段齐备、且最早行触及窗口起点锚
（日历中首个 ≥ `list_date` 的交易日）才跳过；只判头部（退市整理期结束日 ≠ 退市日，尾部不判；停牌日本无行）；
仅尾部有行（如运行期日批行）→ 部分覆盖，进 `partial_overlap` 明细并重取（INSERT OR REPLACE 幂等），
MUST NOT 谎报覆盖。缺失/覆盖明细随非 dry-run 运行落 `snapshot_meta["delist_backfill_missing"]`（持久留痕）。
`security_master` 的既有**隐式**退市口径（快照 D2：不在 master 名单 → 不补行）保留不动；
两口径关系：显式 `delist_date` 供池可见性判定，隐式「不在名单」仅用于停牌合成（见 `snapshot.py` 注释）。

**小票通道档位（microcap，单一来源 `screening/constraints.py`）**：`MICROCAP_THRESHOLDS`
= `min_avg_amount_20d` **1000 万元**（`MICROCAP_MIN_AVG_AMOUNT_20D = 1e7`）、
`min_trading_days` / `min_liquidity_history_days` 同主档；建议执行约束（**记录字段**，v1 不下单）：
单票上限默认 **5%**（`MICROCAP_SUGGESTED_SINGLE_CAP = 0.05`）、滑点档位提升一档
（`MICROCAP_SLIPPAGE_TIER_BUMP = 1`），随批次落 `screening_batches.exec_constraints`
（JSON 对象 `{"suggested_single_cap":0.05,"slippage_tier_bump":1}`；读侧解析为 dict，主档恒 NULL）。主档（main）逐字不变（`DEFAULT_THRESHOLDS` 与既有调用路径）。
通道批次与主档批次并存于 `screening_batches`（列 `pool='microcap'`，主键 `(trade_date, pool)`；
读侧默认 `pool='main'`，既有页面/接口口径零变化）。

**前置门禁（两判据，纯代码检查）**：
- **退市池就绪** `delist_ready` = `security_master` 含 ≥1 条 `delist_date` 非空记录；
- **拥挤度数据就绪** `regime_ready` = `regime_daily` 最新行日期距判定日 ≤ **7 个自然日**
  （复用 `outcome/regime/model.py::latest_regime`；陈旧阈值同 §1.14）。

两判据齐备 → 批次记录 `frontier_gate="ready"`（**可执行候选**，仍不自动产生订单）；
缺任一 → `frontier_gate="observation_only:missing=<要点列表>"`（要点 ∈ `delist_pool` / `regime_data`，
逗号分隔）并披露缺失前置，MUST NOT 静默按可执行对待。
**v1 MUST NOT 向执行链产生订单**（不接 `execution/`、不进目标权重；执行接通属后续立项）。

### 1.17 可转债覆盖与条款事件（delta `add-convertible-bond-coverage`，2026-10-18 登记）

**数据源与字段口径**（2026-10-18 本机 akshare 1.18.94 实探；docs 口径以本小节 + 实现映射表为准）：

- **标的要素**：`ak.bond_zh_cov()`（东方财富可转债数据；本机 1063 行）。列 `债券代码 / 债券简称 / 正股代码 /
  转股价 / 转股溢价率 / 债现价 / 发行规模 / 上市时间 / 信用评级`。落 `cb_master(code PK, name, stock_code,
  convert_price, premium_rate, remain_scale, rating, updated_at)`（`updated_at` = 落库时刻）。
  **单位**：`转股溢价率` = **%**（原样落 `premium_rate`，不除 100）；`发行规模` 单位 亿元（本表不落，
  仅口径核对用）；价格类字段单位 元。**该源无「剩余规模」列 → `remain_scale` 置 NULL**，
  MUST NOT 以发行规模冒充剩余规模（缺值如实缺，§design「缺值一律 —」）。
  未上市标的（`上市时间` 缺失、转股价 NaN）照落主表，字段缺失如实 NULL、不估算、不剔除。
- **日行情**：`ak.bond_zh_hs_cov_spot()`（新浪财经沪深可转债实时行情；本机 320 行）→ `cb_quotes(trade_date,
  code)` 唯一。`close` ← `trade`（元）、`amount` ← `amount`（元，源单位原样落库，不换算万元/亿元）。
  `trade` 缺失或 ≤ 0（停牌/零成交）→ 跳过该行并计数（WARNING），MUST NOT 折算 0 冒充价格。
  **交易日注记**：`trade_date` = 日批 `as_of`，且必须落在快照库交易日历内；**两种跳过**：日历判非交易日 →
  当次跳过行情落库（摘要留痕 `skipped_reason=non_trading_day`）、**日历不可得（缺/空）→ 同样跳过**
  （`skipped_reason=calendar_unavailable`，且不发起抓取）——缺日历无法确认当日是交易日，
  MUST NOT 拿前一交易日价位冒充当日（评审 F4 口径对齐）。
- **条款事件（强赎）**：`ak.bond_cb_redeem_jsl()`（集思录强赎；本机 317 行）→ `cb_events`，
  `event_type='强赎'`。列映射 `强赎触发价→trigger_price` / `强赎触发比→trigger_ratio`（%口径原样，样本 130）/
  `强赎天计数→count_text`（原样文本）/ `最后交易日→last_trade_date`（归一 `YYYY-MM-DD`）/
  `剩余规模→remain_scale`（**亿元**，实探交叉核验：与 `bond_zh_cov.发行规模` 同口径同数量级——样本
  金田转债 规模 15.000 / 剩余规模 9.905；设计冻结 `remaining_size` 单位 = 亿元，本表同口径直落）/
  `强赎状态` 与其余源列入 `raw_json`（专用列不含状态；状态经 raw_json 提取供状态推导）。
- **公告日口径（可得时点）**：源**无公告日字段**（实探确认）→ `announce_date` = **首次观测日**，即日批首次
  见到该 `(code, event_type)` 的 `as_of`；重跑复用既有行的 `announce_date`（键稳定、幂等零重复）。
  语义 = 可得时点上界（真实公告日 ≤ 本值），保守不前视；MUST NOT 以「最后交易日」等倒推公告日。
  唯一键 `event_key = code|event_type|announce_date`。
- **条款事件（下修）**：`ak.bond_cb_adj_logs_jsl(symbol=…)`（集思录转股价调整记录；**逐代码**接口，
  无全市场批量模式，空 symbol 返回 0 行——本机实探）→ `cb_events`，`event_type='下修'`。
  `announce_date` = **股东大会日**（源真实日期，归一 ISO）；关键要素（下修前/后转股价、新转股价生效日期、
  下修底价）入 `raw_json`，专用列留空（不硬塞语义不符的列）。**接入方式 = 转股价变化触发**
  （日调用量有界，避免逐代码扫全市场的分钟级/封 IP 风险）：`sync_cb_master` 检出 `convert_price`
  相对上次落库发生变化的代码（每日 0~10 个量级；下修必致转股价变化），仅对这些代码逐代码查询，
  且只收 `股东大会日 ≥ as_of − 30 天` 的记录（历史条目一次性重放不入清单）。
  **首跑不回填历史下修**（无变化基线）；分派/送股等非下修引起的转股价变化由查询结果自然排除（空日志 → 不落事件）。
- **条款事件（回售）**：akshare **无回售公告接口**（本机实探：`dir(ak)` 无回售类接口、接口 docstring 无
  「回售」命中；仅 `bond_zh_cov_info` 逐代码详情含回售条款文本与回售触发价）→ **v1 事件类型 = 强赎 + 下修**。
  spec 事件类型清单含回售，是否修订待 owner 决策（回售公告源缺失，非本变实现缺口）。

**状态推导规则**（读时纯函数 `outcome/events/cb_status.py::derive_redeem_status`，零 LLM，同输入同输出）：

- 输入：事件行（`count_text` / `announce_date` / `last_trade_date` / 状态文本，状态取自 `raw_json` 的
  `强赎状态`）+ `as_of` + 交易日历。输出 `{state, days_done, days_needed, trading_days_to_last, note}`。
- `count_text` 解析：`已触发天数/所需天数 | 上限天数`（形如 `12/15 | 30`，兼容空格/全角竖线变体）→
  `days_done=12`、`days_needed=15`；**结构锚定**（评审 F1）：必须带 `| 上限天数` 段且数字两侧不粘数字/斜杠，
  另有合理性校验（`days_needed ≥ 1` 且 `days_done ≤ days_needed`）——越界/无结构文本一律不可解析
  （防把斜杠日期 `2026/10/27` 读成天计数而误判 triggered）；不可解析（如到期类文案「临近到期 …」）→
  两者 None，MUST NOT 猜测。
- 状态判定（生命周期序 counting → triggered → announced，故 announced 优先级最高）：
  - `强赎状态 == "已公告强赎"` → **announced**；**此状态要求 `last_trade_date` 存在且可解析，缺失 → undetermined**
    （spec 场景「缺少最后交易日的强赎公告 → 不可判定」）；`公告不强赎` 无专用状态位，沿用天计数判定
    （不强赎事实保留于 raw_json），口径写明不误读为「已公告强赎」。
  - 否则 `days_done >= days_needed` 且 `as_of >= announce_date` → **triggered**；天计数可解析但未达标 → **counting**。
  - 天计数不可解析且非「已公告强赎」→ **undetermined** + note 指明缺失/不可解析字段；`announce_date` 缺失/
    不可解析 → **undetermined**（任一路径）；MUST NOT 猜测数值。
  - 边界：`days_done == days_needed` 判 triggered（含边界）；差一天判 counting。
- **倒计时**：`trading_days_to_last` = 交易日历中 `as_of < d ≤ last_trade_date` 的交易日个数
  （`last_trade_date ≤ as_of` → 0，含边界不含当日）；跨周末/节假日由日历自然扣除。
  日历缺失（空/不可得）/ 日历未覆盖 `last_trade_date` / `last_trade_date` 缺失 → `None` + note
  （如实缺值，不估算自然日；counting/triggered 未公告态本无最后交易日，note 亦指明缺字段）。
- `as_of` 非规范 ISO 日期 → 上抛 `ValueError`（调用方缺陷，不静默降级）。

**清单接线（规则化准入，零 LLM）**：`cb_events` 全部条目进入既有观察清单（`source="cbb"`，
复用 `event_watchlist` 表与只读端点，键 = `event_key`）；条目**载荷四键冻结**（跨变更契约，2026-10-18 裁决）：
`price`（元，取 `cb_quotes` 该代码最新交易日 `close`）/ `premium_rate`（%，取 `cb_master`）/
`remaining_size`（**亿元**，取事件行 `remain_scale`）/ `call_countdown_days`（交易日个数，构建时按日历推导后落
清单注记）——缺值一律 null（前端 `—`），MUST NOT 折算/估算/换单位。清单构建**零 LLM 调用**（MUST NOT 依赖
LLM 分诊）；`reason` = 规则化标注文本（状态标签 + 天计数 + 距最后交易日 + 不可判定 note）。
清单条目不因状态为 undetermined 而被剔除（如实入清单，倒计时 null + note）。
注记刷新纪律：**交易日历可得时**刷新既有 cbb 条目的注记（倒计时随交易日递减），
**日历不可得时只插不覆盖**（如业绩预告分诊日批经本函数顺带构建的路径——MUST NOT 把既有倒计时降级为
null + 「日历不可用」文案冒充刷新，评审 F2）；两种情形均**不动** `status`/`session_id`/`created_at`。
**深度分析入口 v1 禁用**（评审 F3 控制器裁决）：条款事件条目 MUST NOT 经
`POST /api/events/watchlist/{id}/analyze` 发起深度分析（后端 409「条款事件暂不支持发起深度分析（v1）」，
前端不渲染该入口）——cbb 条目标的在 `cb_code`（业绩预告侧 `code` 为 NULL），放行会以空标的建会话入队。

**家族隔离声明**：本变不注册策略族成绩单、不建记账（`event_journal` 仅事件线业绩预告写入），
**不进 T+20 观点统计**（`predictions` 族样本不含转债样本）；转债观察清单为纯观察/研究通道，
不接 regime 仓位系数、不进执行链目标、**不产生订单**（与 §1.16 小票通道同纪律）。
转债行情/事件的日批 job 为 `cb_daily`（16:50，见 `outcome/scheduler.py`）。

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
**决策契约口径切点（2026-09-21，delta `require-watch-hold-rationale`，未跑批）**：watch/hold 决策新增结构化 `inaction_reason`（不行动原因）与 `reeval_triggers`（可观察再评估触发条件）——两侧一次打回回路（trader 侧 `validate_trade_prices` / 终稿侧 `risk_judge`，同价位必填语义）+ 报告结构化渲染（缺失如实标注「未申报」）。prompt 契约同步（trader/risk_judge **v28**，2026-09-21 发布）。**judge 材料经 `_serialize_decision` 自动携带新字段 → B5 可执行性维度（rubric 原句「'观望等待好转' = 差」，`family_b_judge.py:227`）与 consistency 的打分基线可能移动，跨此切点的 B5 pairwise 分数不可直接比较**；action 语义与动作分布不变（契约不改决策倾向，真实产出分布待下轮读数核）。取证背景与真实链路实证（600519 一次产出合格字段、risk_judge 改写非照抄）见 `docs/evals/2026-09-21-决策层全watch取证.md` 与 `tests/validation/2026-09-21-require-watch-hold-rationale-validation.md`。
**hosted 判分口径切点（2026-09-22，delta `switch-hosted-judge-to-k-mean`，未跑批）**：`python -m evals.run` 的 judge 判分从单次调用改为 **K 次均值**（`run_judge_mean`，K 经 `--judge-repeats` 声明、默认 3；scores/spread/部分失败计数随 Scores.comment 落库，产物 JSON 记 `judge_repeats`）。round11 实测单次调用在 4/5 边界双峰翻转（同材料 n=13：4 分 7 次/5 分 6 次）、K=3 中极差>0 行 5/8——单次噪声与 hosted 待测回归效应同阶，故与消融路径（2026-09-14 起 K 均值）统一协议。**跨此切点的 judge 绝对分不可与 r1–r9（单次口径）直接比较**；切点前「跨实验比较按同噪声下的相对差异解读」的临时纪律随之退役。非 debate 维度结果形状不变；debate 封顶/枚举遥测取最低分调用（与消融同口径）；judge 维度均值在时间线表中的含义不变（仍为各 item 点估计的均值）。
**report_relevance 口径切点（2026-09-22，delta `resolve-report-relevance-zero-variance`，未跑批）**：quick 条目停评全部 judge 维（该维 quick 段自 r3 起零方差全 5、Spearman 不可算，失败面由 ticker_match/section_coverage/citation 系确定性指标覆盖）；deep 条目保留并 rubric **v3→v4**（5 分档锚点判例：查询显式子问题逐一回答方可给 5，任一被回避/泛泛带过降 4 且 reason 指明被回避的子问题）。**跨此切点 report_relevance 均值不可直接比较（quick 行退出分母 + v4 收紧）**。离线校准（round7 同批 14 行、K=3 均值）：MAE **0.143** / 方向一致率 **1.000**，过门（≤1.0 / ≥0.80）——如实记录：**14 行 v4 全部仍 5（含人工打 4 的两行），锚点判例在本样本未产生降分行**（样本查询多为单一焦点），判例实际区分力待含多焦点查询的下轮样本验证（产物 `evals/judge_calibration/data/judge-sample-round12-relevance-v4.jsonl`，脚本 `tests/scripts/rejudge_relevance_v4.py`）。
**Outcome 收益口径切点（2026-09-23，delta `add-outcome-profitability-protocol`，未跑批）**：outcome 收益指标口径登记 §1.9（主指标 T+20 超额 / 回避辅助 / settled<10 红线 / 收口纪律 / 泄漏控制），首个 outcome 预登记落 `evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md`。**本切点只登记口径与纪律，不改任何结算行为**——T+20 结算窗口、回避判定、标准化入场价的落地在 delta `update-decision-settlement-contract`（其切点另行登记）；读数腿（forward cohort / walk-forward 回测）开跑前置 = 两 delta 均已落地，且首个读数之前 §1.9 与预登记不得再改（改须走 §1 修订 + 新预登记版本 + 新切点行）。（同日实施期修订注记：实施收口前对 §1.9 做了**预数据口径澄清**——① 明确均值人口为 long/short 全部已判定观点（三态）与标的簇 bootstrap CI、胜率为描述性指标、BENCHMARK_CODE 环境覆盖即失效；⑤ 将「行情缺失率」正名为不可判定率（与结算成功率同分母互补）。修订发生于**未跑批、无任何读数、无数据依赖选择**之前；预登记文档同步措辞对齐，门禁字段无实质变更。）
**结算契约切点（2026-09-23，delta `update-decision-settlement-contract`，未跑批；口径见 §1.9（Outcome 收益指标））**：默认判定窗口 252→20（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`）；判定入场基准改为派生 `settle_entry_price`（归属日收盘 / 收盘后次一交易日 / 非交易日顺延），参考价仅展示与盯市；neutral 观点改回避判定（`avoidance_status`，不进胜率，不走 superseded；判定后 `status` 落终态 `avoidance`）；track-record 判定链路补齐 long/short 的 decision_* Score 上报。**存量已结算行不重算、存量 open 按原 horizon 判定；跨此切点的胜率读数不可直接比较**（头条口径按 `caliber_horizon=20` 过滤，`legacy_settled` 字段披露存量计数）。真实链路 LLM 端到端（deep 分析 → 落库 → 判定）留待 owner 有预算时补跑。`agent_metrics_daily` 日批快照同按 `horizon_days=DEFAULT_HORIZON_DAYS` 过滤（跨切点不混算）。（同日实施期修订注记：§1.9⑤ 健康检查分母明确为「已判定（含回避判定）= `resolved_*` 三态 + `status='avoidance'` 且 `avoidance_status` 非空」+ `unresolvable` 的互补两率——把回避终态从「两侧都不计」修正为计入已判定，防止 §1.9⑤ 门禁对大量 neutral 流量假 FAIL；修订发生于**未跑批、无任何读数、无数据依赖选择**之前，属实施期口径澄清，不触发新预登记版本。）
**cohort 启用切点（2026-09-23，delta `add-forward-paper-trading-cohort`，未跑批）**：标的池 `universe-v1`（10 只，seed 42，池快照可离线重建）；开关 `COHORT_ENABLED` 默认关；开启前须 owner 批预算（≈1.7M tokens/日）；读数口径见 §1.9⑧。
**复权口径切点（2026-09-23，delta `add-backtest-leakage-controls`，未跑批）**：结算与回测取数统一**后复权（hfq）**，分析输入保持前复权；跨切点的结算数值（分红除权样本）不可直接比较，存量已结算行不重算。
**泄漏控制启用切点（2026-09-23，delta `add-backtest-leakage-controls`，未跑批）**：回测**正式批（`--batch-kind formal`）**绑定预登记（门禁字段 `OUTCOME_REQUIRED_FIELDS`，无效预登记拒绝正式批身份）+ 干净窗口判定（决策日距跑批日 ≥ 20 个**交易日**，基准不可得 / 空样本保守判不通过）+ 泄漏探针披露（裸问三层题，方向命中率阈值 0.60，超阈 → 结论降级为「泄漏污染下的上界证据（真实 skill ≤ 读数）」；探针不可测 → 按通路验证处理）。`--batch-kind pathway`（默认）结论恒标「通路验证定位」，不产 skill / 赚钱能力结论句——**深历史批次永久定位通路验证**（记忆泄漏不可根除，只可披露与降级）。报告 md 落 `evals/backtest/results/*.md`（含生命周期 status 头，已纳入 `docs/evals/README.md` 结论注册表索引扫描范围）；`evals/backtest/results/pilot-2023-shock.md` 就地补 status 头 + 「通路验证 + 泄漏风险」标注（原文与数字保留）。**本切点不产生任何 skill 读数**：首个正式批的窗口与样本量由预登记 MDE 反算后 owner 批预算，探针降级阈值（0.60）为预登记可调项。

**决策完整性 anomaly 信号启用切点（2026-09-30，delta `update-decision-integrity-gates`，未跑批）**：决策层新增三类确定性可观测信号——①决策文本价位交叉校验 anomaly（偏差形态：触发价 vs 已验证指标偏差 >2% 且不在参考带；空洞形态：上破/下破触发价已被现价满足），②buy/sell 终稿再评估触发条件打回标注（final_reeval_check note），③FM 置信度漂移（|FM−终稿|>0.15 时报告标注）。**全部只观测不进处置**（incident 026 纪律：聚合指标进入处置前须分桶归因逐条人工终裁），信号经 anomalies 通道与报告标注呈现，供后续轮次 judge 材料与标注人使用。本切点不产生任何读数，§2 时间线不加行。

**运维控制台启用切点（2026-09-24，delta `add-eval-ops-console`，未跑批）**：设置中心新增「评估运维」分区（六页签），把日批五任务（判定 / 盯市 / 指标快照 / 完整性 / cohort）的状态与运行历史、手动补跑、cohort 开关与跑批时刻、回测批与泄漏探针触发、outcome 收口健康检查、回测报告注册表、预登记版本化与口径旋钮全部纳入界面（只读 + 触发；烧钱动作一律前端确认 + 后端沿用既有门禁，**预算熔断 / 串行 / usage 真值记账一行不改**）。**cohort 开关与跑批时刻的运行期唯一真相源由环境变量改为持久化 `ops_config` 表**（`cohort_enabled` / `cohort_hour` / `cohort_minute`；env 仅作进程启动引导默认值、已有表值不被覆盖，重启保持）——**跨此切点，「改 env 重启生效」的运维时代结束：旧的「env-only」运维期与切点后的界面期不可直接比较**（同一开关在两期的取值来源不同；切点后归因不得再把环境变量改动当作生效手段，须查 `ops_config` 现值与 `job_runs` 审计行）。调度器未启动（TESTING=1 或显式禁用）时状态接口显式返回「未运行」而非 500 或空列表。口径旋钮（判定窗口 / 中性带 / 探针阈值 / 最小已结算样本）的界面修改**只生成 `docs/evals/caliber-drafts/ops-caliber-draft-*` 草稿**，不写本文件、不改 `evals/outcome/caliber.py` 常量——生效仍须走 §1 口径修订 + delta 流程（草稿落在 `openspec/changes/` **之外**：骨架草稿未成 change，放进 changes/ 会让仓库级 `openspec validate --all --strict` 报错，见 `tests/validation/2026-09-24-add-eval-ops-console-validation.md` 残留风险）。**本切点不产生任何读数**：无 cohort 跑批、无回测正式批、无探针 LLM 调用，故 §2 时间线不加行。

**judge 端点切换切点（2026-10-04，delta `add-kimi-reasoning-effort`（#218/#219）；未跑批）**：judge LLM 从 deepseek-v4-flash@方舟（temperature=0.0）切至 **kimi-for-coding@Kimi Code**（`JUDGE_REASONING_EFFORT=none` 关思考，路由无思考 K2.8；切换动机=方舟套餐到期 + 裁判与被评模型解耦）。**采样口径变化：Kimi Code 端点采样温度锁死（实证：思考档仅接受 temperature=1、无思考档仅接受 0.6，其他值一律 400），kimi 请求 temperature 一律不发送（adapter→gateway `suppress_temperature` 契约），judge 实际采样=端点固定值 0.6**——采样确定性较 T=0.0 弱化，单次判分噪声边界相应变化（K 次均值协议不变、rubric 版本不变、材料口径不变）。**跨此切点的 judge 绝对分不可直接比较**（judge 模型与采样双变），跨轮结论以同切点内比较为准。同日管线主 LLM env 回退链切 bigmodel glm-5.3（config-only，请求级配置不受影响；bigmodel 端点对 glm-5.3 接受 temperature=0.3，管线口径无变化）。

**judge 端点切换切点（2026-10-11，config-only 双向对换；未跑批）**：judge LLM 从 kimi-for-coding@Kimi Code 切至 **glm-5.3-flash@bigmodel coding**（`JUDGE_REASONING_EFFORT` none→**low**：glm 族无 none 档（`ArkGLMOptions` 仅 max/high/low，thinking 强制开启），low 为最接近原「关思考」口径的最轻档，探针实证 low 档思考显著缩短）。**采样口径：temperature=0.0 保留（探针实证 glm-5.3-flash 接受 T=0.0 与 effort=low 含组合），judge 实际采样=T=0.0**——较上一切点的「端点固定 0.6」确定性增强。（2026-10-17 勘误：实测 adapter 对含 glm 的模型名按 `capability_for_model` 启发命中 ark-glm preset（`reasoning_forced=True`）在发送前剔除 temperature——含 glm 名模型两代端点均从不携带 temperature（2026-10-17 A/B 72/72 次调用同款剔除告警），实际采样跟随模型/端点默认；「judge 实际采样=T=0.0」不成立，成立的仅为「端点接受 T=0.0」。K 均值协议、rubric 版本与读数口径均不受影响，此处仅更正事实描述。）**跨此切点的 judge 绝对分不可直接比较**（judge 模型与采样双变），跨轮结论以同切点内比较为准；round 系列校准数据若需跨切点复用，仅方向性结论可参考。同日管线主 LLM env 回退链从 bigmodel glm-5.3 切 **k3-256k@Kimi Code**（被评对象模型变更，config-only；采样口径变化：① Kimi 端点温度锁死仅接受 1，harness 路径 temperature=0.7 走 adapter 温度拒绝自动降级（spec「temperature 拒绝自动降级」，每次调用多一次 400 往返，trace 记 `degradation=temperature_dropped_endpoint_rejected`），单发管线调用不携带 temperature 跟随端点固定值；② effort 不携带（kimi env 分支无 effort 出口）跟随端点默认 k3 族=high，**较 glm 期的 LLM_REASONING_EFFORT=low 思考量上升**；③ 输出预算：env-kimi capability max_output=8192（glm 期 ark-glm 为 65536），reasoning 与正文共享，长输出节点截断风险上升（OutputTruncated→续写 1 次→report 降级链兜底），若实跑截断率显著需立项 kimi capability preset）。同日 `LLM_MAX_CONTEXT=256000` 启用（k3-256k 真实窗口兑现，ContextBudget/压缩阈值随动，react 预算 128k→256k）。

**管线主 LLM 切换切点（2026-10-13，config-only；未跑批）**：分析 LLM 从 k3-256k@Kimi Code 切至 **deepseek-v4.1-flash@opencode zen/go**（被评对象模型变更；judge 保持 glm-5.3-flash@bigmodel 不变）。实测探针（五项 capability probe，离线脚本与 `/api/llm-config/test` 双通道）：non_stream / stream / tool_call / tool_followup 四过；json_output 不支持（`response_format=json_object` 被端点拒绝，管线无消费方，仅能力矩阵记录）。**采样口径变化：① 温度恢复发送**——端点接受 T=0.3（节点调用）与 T=0.7（ReAct harness），kimi 期「端点锁死 + 每次调用一次 400 降级往返」的强约束消失，单发节点实际采样=T=0.3；**② effort 不携带**（env 分支 openai/ 前缀无 effort 出口，`LLM_REASONING_EFFORT` 不消费）跟随端点/模型默认，deepseek-v4.1-flash 为思考模型（reasoning 与正文共享 max_tokens 预算）——思考量基线待实跑观测；**③ 输出预算不变**（节点显式 65536 / harness 显式 16384，显式请求优先于 capability 静态 8192）；**④ `LLM_MAX_CONTEXT=256000` 不变**（模型原生窗口约 1M，models.dev opencode-go 目录：context 1000000 / output 384000；启用更大预算是独立决策）。opencode 网关强制 `x-opencode-session` 头——同日修复 probe 路径 `base_url` 别名漏注入（此前探针形态必 400；pipeline 走 `api_base` 不受影响）。**跨此切点的分数不可直接比较**（被评模型变更）；同一切点内比较不受影响。

**judge 端点切换切点（2026-10-17，config-only；未跑批）**：judge LLM 从 glm-5.3-flash@bigmodel coding plan 迁至 **glm-5.3-flash@opencode Go**（同模型换服务栈；`JUDGE_MODEL` / `JUDGE_REASONING_EFFORT=low` / rubric 版本 / K 均值协议均不变，仅 `JUDGE_BASE_URL` / `JUDGE_API_KEY` 换为 opencode 账号；备份 `.env.bak-judge-bigmodel-20261017`）。动机：① 解耦 bigmodel 5h 窗口熔断家族（10-16 分析侧实证三败）；② 与分析的 deepseek-v4.1-flash 额度独立（opencode Go 逐模型月额度：GLM-5.3-Flash $60 / DeepSeek V4.1 Flash $80）；③ 主管线与裁判同账号单网关。**采样口径：两代端点 temperature 均从未发送**（adapter 按模型名启发剔除，见同日对 10-11 条目的勘误；effort=low 经 extra_body 透传保留）——**本切点不引入采样变化**。**A/B 实测（同日、非跑批：2026-09-22 variant=full 冻结状态 3 标的（000001/300750/600887）× 4 维 × K=3 双端点同材料对跑，共 72 次调用；原始读数落 `log/judge_ab_result.json`，Langfuse 标记 `judge-endpoint-swap-ab`）**：解析失败 0/0；12 对配对单元 8 对完全一致、4 对 |Δ|≤1.0（其中 3 对为 K=3 内单次翻转=调用级噪声级；000001/decision_grounding 为 1 分系统差 4.0 vs 5.0），无 |Δ|>1——同模型跨栈总分分布一致度高，**但 1 分级差异不承诺跨切点可解读**（沿用「同噪声下相对差异」口径）。**跨此切点**：judge 模型与采样均未变、仅服务栈变更，绝对分可比性显著强于此前三次 judge 切点；保守起见跨轮结论仍以同切点内比较为准。运维注记：后端容器不消费 judge purpose（判分只走宿主机 evals 路径），容器 env 未重建、无一致性影响。

---

**记忆注入消融批次（2026-10-18，delta `add-decision-memory` 闭环；n=10 配对）**：`--suite memory`（tech_no_memory → tech_memory，分析师层直出、单变量=analysts_entry 是否召回），10 标的 × 2 臂 × 1 重复 = 20 run；判分维按 #112 口径过滤为 report_relevance（K=3 均值）。**读数：Δ = −0.0333，95% CI [−0.2333, 0.1665] → 无显著差异**（配对单元=ticker bootstrap；CI 含 0 按纪律只写无显著差异，不得引申为对记忆价值的否定）。健康检查：20/20 run 完整、judge 失败 0、**10/10 标的记忆臂召回非空**（1–3 条已结算情景记忆；语义条款 0——该通路未被本批检验）。原始读数 `reports/ablation/ablation-20261018-142257.json`（本地）；报告 [2026-10-18-add-decision-memory-n10消融报告.md](2026-10-18-add-decision-memory-n10消融报告.md)。边界：仅测分析师层上下文注入面（该 delta 变体设计），不测辩论/决策层消费；有效 n=10。

## 2.1 round7 预登记分析计划（标注前锁定，防事后口径漂移）

- 数据：`judge-sample-round7-blind.xlsx`（人工 human_score + confidence）× `judge-sample-round7-judge.jsonl`（judge 分，r3 运行时落库，已冻结）
- 每维计算：MAE、方向一致率（>3 分界）、judge 分有方差的维度另算 Spearman（report_relevance / debate_quality judge 全 5，Spearman 不计）；整体 MAE / 方向一致率
- 判定：MAE ≤ 1.0 且方向一致率 ≥ 0.7 视为该维 judge 可用；Spearman ≥ 0.5 为加强项。n=9 的维度**不单独下结论**，与 round8 合并后判
- 附加：人工 confidence 与 judge confidence 的相关；人工低置信行单独列出复核
- 与 round5 对照：只比方向（是否从「judge 系统性高 1–2.6 分」收敛），不比绝对值（材料/rubric 不同口径）

| r8 09-13 21:32Z | cdca5b1+027+v7 | 基线重建轮（三变量：v7 rubric / 派生指标喂回 / 027 价位校验回路激活）；dg 3.56 系 v7 归属+解读失当扣分生效（契约抽验 4 条低分全中 v7 规则）；blocked 4/9 | 5.0 | 4.22 | 3.56 | 4.89 | 0.556（blocked 4/9） | 0.835 | 4/9｜1.22/条｜5.33/条 | 0 |
| r9 09-14 00:37Z | afd9d1f+v8 | 混合变量轮（材料升级：consistency 补 Trader 方案节 + debate 收敛骨架行；rubric v8 三版本两判例）；审计：dg 归属扣分全为真错安（判例无误放）、Trader→RJ 静默推翻核对首次可判、debate 5 分档仍漏判 ≥3 行（v9 候选：强制枚举）；blocked 6/9 | 5.0 | 4.67 | 3.44 | 4.67 | 0.333（blocked 6/9） | 0.843 | 6/9｜1.44/条｜6.33/条 | 0 |
| r13 09-23 02:02Z | 356feb4（三 delta 合入后） | **新口径首轮**：K=3 均值 + quick 停评 + relevance v4（跨 09-22 两切点，绝对分不与 r1–r9 直接比较）；生产栈含 bear 锚点 prompt/v28 契约/赔率自检；**blocked 9/9 + 真错 4.0/条为系列新高，待分桶归因不处置** | 5.0 | 4.0 | 3.52 | 4.78 | 0.0（blocked 9/9） | 0.786 | 9/9｜4.0/条｜7.44/条 | 0 |

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

**owner 下腿抽查（2026-09-20 表已生成，`tests/scripts/p2_lowerleg_export.py`，seed=20260920 可复现）——~~待判读~~ 已闭合（同日终裁）**：
- **B1（有条件转正的补独立抽查条件，§19.2）**：47 行 = 机器判「否」35 行全抽（正例层）+ 机器判「是」抽 10%（12 行）。**owner 终裁：机器判定确认无误（对话终裁，未逐行回填表内人工列；含「部分吸收」边界形态——owner 确认严格二值口径：部分吸收不算，无需加档/立项）→ B1 有条件转正的条件满足，条件注解除，转正定稿**
- **B5c（§19.11 转正 provenance 补强）**：6 行（分票 2 + 换判合理 2 + 一致票抽 2）同批终裁确认 → provenance 补强完成
- 判读口径（≥0.80 维持转正）按整体确认处理（B5c 校准「4 行批量核准」同款先例）


**owner 校准（2026-09-20 观测轮 §19.12，新材料跑批产物）——~~待判读~~ 已闭合（同日终裁）**：
- grounding 复扫：8 行（0 无源正例 → 全抽自有源）owner 终判机器无误 → **0/79 无源率读数转正**（新 prompt 生产成立，§19.8 承诺闭环）
- B5c 观测轮：5 行 owner 终判机器无误——**含 000001 矛盾行**（verdict=b 票面理由称「A更优」）：判定结果 b 维持，理由-结论不一致记录为判定器输出一致性的已知形态（与 §19.3「新增维度封顶」同族），**不处置**
- ~~**赔率自述矛盾处置候选（证据 strengthened 至四例）**~~ **已落地（2026-09-21，delta `extend-payout-self-check-coverage` 归档）**：归因修正——trader/risk_judge 出口自检本已生效，两漏网为 ①600030「1.78**倍**」形态盲区（正则只认 `N:1`）②601888 终稿 buy 价位全 None 直通。修复三件套：N倍 形态替换 + 转述护栏（批评语境词近距离跳过+计数 `payout_ratio_conflict_skipped`，防反转批评语义）+ 终稿价位完整性打回（`final_price_check`）。**FM 出口不挂**（裁决：审批转述文本错改风险高于收益，见 delta design）。真实材料复算实证 600030 替换生效 / 601888 护栏保留+计数；全套 3046 绿。验证报告 `tests/validation/2026-09-21-extend-payout-self-check-coverage-validation.md`


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
- ~~**消融跑批前的两处小账（2026-09-14 盘点发现）**~~ **已收口（2026-09-22，PR #141）**：① 脚本参数化 `--resume/--min-runs/--out-dir`（默认 `reports/ablation/resume.json`，out-dir 默认取 resume 所在目录），台账缺失给清晰报错；**顺带修复隐藏缺陷**——脚本缺 sys.path 引导，按文档用法直调必报 `ModuleNotFoundError: evals`（同族脚本有引导；已补 + 「直调 --help」冒烟测试防回归）；② 报告新增「judge 明细分布」节（score_spread 中位/最大 + 纯定性条数中位/最大 + 枚举缺失 run 计数），使 debate v6 取值域压缩（4.0–4.333）的影响在权威报告里可见；旧记录无明细显式说明、不伪造。测试 `tests/evals/test_aggregate_90_cli_and_detail.py`（7 例）+ 既有渲染 6 例
- ~~**hosted 实验路径仍是单次判分**：`python -m evals.run`（`reports/evals/*.json` 与跨实验对比的数据源）的 judge 仍单次调用，带 ±1 调用级噪声（本轮只把**消融**路径改成 K 次均值）。口径一致性候选：也给该路径加 K（成本 ×K、且会改变与历史报告的绝对分可比性）——需 owner 定；在此之前跨实验比较请按「同噪声下的相对差异」解读~~ **已落地（2026-09-22，delta `switch-hosted-judge-to-k-mean`）**：hosted 判分切换 `run_judge_mean`（K 经 `--judge-repeats` 声明默认 3，产物 config 记 `judge_repeats`），切点登记见 §1.1/§2——跨切点绝对分不可直接比较，owner 决策成本 ×K 换取噪声下限披露
- **观测电池判定行不落盘**（2026-09-23 grounding 归因时暴露）：`run_battery` 只持久化汇总读数，grounding/B1/B2 行级明细须从 Langfuse 反解（Langfuse 不可达时归因材料不可得）。候选：三腿 judged rows 落盘（unit_id/argument/anchors/judge_label/reason）+ 产物路径写进 result——小改动，随下一次观测电池相关 delta 一并落地
- mypy 全仓 75 个既有错误（本次触碰文件为 0），是否立清理任务
