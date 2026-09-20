# Backlog 索引

> 状态（2026-09-04 晚）：立项的 8 条 delta 已全部实施完毕（提交 225b10d / 12af347 /
> 417fd28 / ee1159c / a038970 / 3f429ad / 269c841 / 52a6451）。各 delta 的待办仅剩
> 人工环节或需 LLM 余额的验证项，明细见各 delta tasks.md 与 tests/validation/ 报告。

## 已实施

| Delta | 内容 | 验证报告 |
|---|---|---|
| calibrate-fm-approval | 取证证伪「FM 永不批准」+ return 回路反馈修复 + FM 决策双向门禁 | tests/validation/calibrate-fm-approval-validation.md |
| add-track-record-stage-b | 盯市/净值/风险收益指标 + 战绩页风险卡与净值图 | tests/validation/track-record-stage-b-validation.md |
| add-track-record-stage-c | 校准页/四维切片/详情页/版本分段（P6）/完整性校验 | tests/validation/track-record-stage-c-validation.md |
| add-toolcall-evaluation | 工具调用埋点（_trace_tool）+ 四维评估 + is_streaming 回归修复 | tests/validation/eval-suite-additions-validation.md |
| add-hallucination-rate-metric | v1 数值型 claim 抽取 + 证据校验 + 幻觉率门禁 | 同上 |
| add-latency-cost-regression | 时延/token/成本聚合 + 基线回归门禁 + 趋势检测 | 同上 |
| add-judge-human-calibration | 标注导出 CLI + Spearman/MAE/方向一致率 + 校准触发 | 同上 |
| enable-hosted-evaluator | 降级方案：scores 轮询 + 告警 + 口径对齐 + 模板快照 | 同上 |

## 遗留待人工/待资源（2026-09-14 更新）

> **2026-09-14 大扫除**：历史 sprint 的 8 个未归档 delta（settings-center/analyst-coverage/
> judge-calibration/latency-regression/news-topic-search/pipeline-graph-view/calibrate-fm/
> hosted-evaluator）全部清收归档——spec 冲突逐个核对（3 个陈旧 delta 经 9 条 requirement/
> scenario 比对确认主规范为超集后 skip-specs 归档；settings-center 的 llm-config 场景名
> 对齐主规范后正常 sync，主规范净增 20 条）。同日完成：round8 代裁收口、@live 切方舟修复、
> v8 delta 实施+round9 审计+归档、debate rubric v5+round10 离线重判、价位必填化 delta
> 实施+真实链路闭环实证+归档。changes/ 目录现仅剩本索引。以下为当前真实遗留。

## 进行中（2026-09-16 更新）

| Delta | 内容 | 状态 |
|---|---|---|
| revamp-ablation-v2-causal-claims | 因果主张登记 + 注入法 + 单元级判定；GATED 段（驱动薄壳化 / citation 四桶拆报 / 结论注册表 / metrics §1 口径 / 3 标的通路验证）已于 2026-09-16 解阻完成 | **验证通过、待 owner 复核 8 项**，未 archive（见 `tests/validation/2026-09-16-causal-ablation-framework-validation.md`） |

2026-09-16 归档三项（详见 `openspec/changes/archive/2026-09-16-*`）：
- `add-debate-argument-anchors` —— 论点结构化锚点 + 零 LLM 校验 + `argument_anchor_coverage`；验证报告 `tests/validation/2026-09-16-add-debate-argument-anchors-validation.md`
- `ground-comparative-delta-claims` —— 比较型差值计数 + `derived_series`/`price_levels` 通路修复 + 均线差幅；验证报告 `tests/validation/2026-09-15-ground-comparative-delta-claims-validation.md`
- `update-ablation-driver-parity-and-report-status` —— 消融口径正名/驱动接回/报告状态契约
## 评估补强（后续批次，2026-09-15 登记；2026-09-20 盘点更新）

> 与 `update-ablation-driver-parity-and-report-status` 互补：该 delta 修「已有结论的可信度管理」，本批修「新结论的产出机制」。

1. **结论注册表 + superseded 状态机**（未立项）：把报告 `status` 契约扩到 `docs/evals/` 全库（日期报告 + 台账 + 模板），索引页渲染状态徽章 + 状态迁移校验。需先盘清全库每份报告的真实状态。
2. **跑批入口唯一化**（未立项）：`ablation_pilot.py` 改薄壳——只做续跑/记账，判分与过滤全调库侧。整体瘦身牵动计量、材料落盘、断点续跑三块。
3. ~~**阳性对照进消融跑批**~~ **已随 v2 框架落地（2026-09-20 盘点确认）**：`pilot_runner._positive_control` + McNemar 精确检验 + 「阳性对照失灵作废本轮阴性」契约（`negative_results_status`）+ 预登记门禁引用，见 `evals/causal_ablation/pilot_runner.py` 与 spec causal-ablation「预登记与阳性对照」。
4. ~~**v2 消融框架**~~ **已完成并归档（2026-09-19，`revamp-ablation-v2-causal-claims`）**：因果主张登记 + 注入法 + 单元级判定 + 校准门控三腿转正；主规范 `openspec/specs/causal-ablation/` 9 条 requirement。与幻觉率度量 v1（`evals/hallucination/`）互补——注入法覆盖文本/事件型防线。

## 遗留待人工/待资源（2026-09-14 更新）

> **2026-09-14 大扫除**：历史 sprint 的 8 个未归档 delta（settings-center/analyst-coverage/
> judge-calibration/latency-regression/news-topic-search/pipeline-graph-view/calibrate-fm/
> hosted-evaluator）全部清收归档——spec 冲突逐个核对（3 个陈旧 delta 经 9 条 requirement/
> scenario 比对确认主规范为超集后 skip-specs 归档；settings-center 的 llm-config 场景名
> 对齐主规范后正常 sync，主规范净增 20 条）。同日完成：round8 代裁收口、@live 切方舟修复、
> v8 delta 实施+round9 审计+归档、debate rubric v5+round10 离线重判、价位必填化 delta
> 实施+真实链路闭环实证+归档。changes/ 目录现仅剩本索引。以下为当前真实遗留。

1. ~~judge 人工校准标注~~ **已闭合（2026-09-13）**：round7 盲标 41 对 + owner 终裁达标（整体
   MAE 0.342 / 方向一致率 97.6%），rubric v7 定稿；round8 基线重建轮经维护者代裁审计无虚高
   （见 docs/evals/2026-09-13-round8-维护者代裁报告.md）。spec 的人工 ≥80% 一致性校验以
   round7 结果为准判定通过。
2. **nightly @live 门禁**（已决策 2026-09-08：不暴露本地 Langfuse、不上云）：
   CI secrets **不配置**，GitHub Actions 侧 @live 维持跳过。live 验证走本地手动
   （`uv run pytest -m live`），或未来愿开电脑时挂 Windows 计划任务在收盘后定时跑。
   **2026-09-13 修复**：@live 用例硬编码 DeepSeek 直连（模型/key/端点）已切方舟生产栈
   （LLM_MODEL/LLM_API_KEY/LLM_BASE_URL，Agent Plan），幻觉率用例同步修 report_chunk
   事件抽取 + data_map source 契约；3 用例本机实测全绿，无 DEEPSEEK_API_KEY 依赖。
3. ~~docker 后端重建~~（2026-09-13 执行）：`docker compose up -d --build` 已刷新，
   FM state 修复/根 span output/session 头/incident 027 修复入镜像。
4. **judge-sample 数据文件入库规矩**：round5-8 盲标/标注样本 xlsx/jsonl 目前未跟踪，
   仅 round1 jsonl 在库——要么统一入库（可审计优先），要么 .gitignore 统一排除（本地
   报告已引用路径），待定。
5. ~~round9 + v8 候选~~ **已闭合（2026-09-14）**：delta `upgrade-judge-material-v8-rubric`
   实施+归档（consistency 补 Trader 方案节、debate 收敛骨架行、rubric v8 两判例）；
   round9 实验 + 审计完成——dg 多来源归属判例达标、Trader→RJ 静默推翻核对首次可判、
   debate 5 分档判例未达标（v9 候选：强制枚举论点标头，已登记 metrics.md 待决策）。
   见 docs/evals/2026-09-14-round9-v8审计.md。
6. **debate 5 分边界可靠性**：v5 强制枚举离线重判（round10）准确率 2/8→6/8 但仍漏判
   宁德、招行回归——prompt 机制到顶；候选=judge 输出结构化枚举字段 + 程序封顶
   （需开 delta 改 judge 输出契约），见 metrics.md 待决策。
7. **价位必填化已落地**：delta `require-trade-price-declaration` 实施+归档（2026-09-14），
   E2E 实证完整闭环（trader 申报 62/57/74 → 辩论 6/6 同源引用代码值 → 报告参数行渲染）；
   validation 报告 tests/validation/2026-09-14-require-trade-price-declaration-validation.md。
8. **事件观察项（不构成任务）**：① FM return 真实事件取证（calibrate-fm-approval 4.3 转观察，
   出现时按口径取证）；② news-topic-search 引导生效性日常观察；③ latency 趋势告警启用
   依赖 Windows 计划任务挂载 nightly。



## 决策层「全 watch」取证（P1 round-3 发现）——已取证立项（2026-09-16/17）

**现象**：P1 注入 pilot 三轮实测（600519 ×2 轮 + 000001 ×1 轮）中，Trader/FM 产出的 `trader_plan` 与 `final_trade_decision` **全部为 `action='watch'`**，导致价位 sanity（A5）在真实决策分布上无可校验对象（暴露率 ≈0）。

**为什么值得单独取证**：决策层极少给出可执行方案，本身是系统行为层面的待解释现象——可能与 calibrate-fm-approval 记录的 FM 44 次 return、决策层整体保守同源。

**取证内容**：① 生产流量上 watch / buy / sell 的占比分布；② 成因分解——是风控否决的下游结果，还是 Trader/FM 的默认姿态（prompt 倾向 / 阈值过严 / 输入信息不足）；③ 若属默认姿态，评估是否需要在 prompt 或流程上给出可执行方案（这是个产品决策，取证先行）。

**阻塞关系**：族 B（B3 数字出处率 / B5 pairwise）的 MDE 反算以决策动作分布为输入 → 本取证为 P2 前置。

**取证结论（2026-09-16，20 标的全图真跑，382 次调用）**：决策动作分布 = watch 14 / buy 4 / sell 1 / hold 1
（watch 70%，可执行 25%）——**早期「全 watch」系 3 标的小样本巧合**，非决策层默认姿态。可执行标的：
000858 / 601398 / 000333 / 601012 / 600887。故：① A5 可测面存在；② 族 B 暴露率输入 = 0.25；
③ 「决策层整体保守」这一疑点不成立（本取证关闭，如后续生产流量显示另一幅图再重开）。

## P2 族 B 首批读数已出（2026-09-17）

**已跑完**：材料腿（full × 20 + analysts 对照臂）、B3/B4 code 读数、B1/B2/B5 判定（provisional）。
读数与口径见 validation §19/§19.1，台账见 `docs/evals/metrics.md` §1.7 与 `runs.jsonl`。

| 腿 | 读数 | 可读性 |
|---|---|---|
| B3 风控数字出处率 | 0.952（20/21） | 描述性（n=7 无分辨率） |
| B4 sanity 首判 | 打回 1/7（000333 越参考带） | 描述性；**补算口径**不含打回 Trader 回路 |
| B1 风险点吸收率 | 0.786（196 行） | provisional；**落地主指标还差阈值标定** |
| B2 交锋修正率 | 0.198（111 行） | provisional |
| B5 pairwise | full 20 : analysts 0 | **口径作废**（同义反复），须换对照臂 |

**三件待办（按优先级）**：

1. **B5 换对照臂**（口径缺陷，非读数问题）：analysts vs full 让 judge 在「有决策 vs 无决策」间选，
   必然全选有决策的一份 → 改用**两臂都含决策层**的对照（预登记 §8 的 `plus_debate` vs `full`，
   或「裁掉一层但保留 Trader」的变体），成本 ≈ 20 × (6 + 19) 次调用。
2. **B1 阈值标定**（决定主指标能否落地）：「新增风险点」现用字符二元组 Jaccard，
   扫描表显示 0.05→17%、0.20→100%（600519 实测）→ 用校准样本（`2026-09-17-p2-calibration-b1.csv`）
   让人工同时标「是否新增 / 是否被吸收」，据此标定阈值并写入预登记；或把「是否新增」并入 NLI 腿。
3. **校准门控**：`2026-09-17-p2-calibration-{b1,b2,b5}.csv` 三张表待人工标注（≥20%），
   一致率 ≥0.80 后 B1/B2/B5 才可从 provisional 转正。
   **状态（2026-09-18，含同日更正）**：B1 = 0.90 **有条件转正**（owner 口径共建 + 8 行逐条裁决
   + 其余批量认可预填稿；5 行与预填不同、其中 4 行新增口径待 owner 确认本意）；**B2 = 门控未过、
   维持 provisional**（owner 未复核，人工列已清空，预填稿待看——分歧仅 4 行：2/5/17/27）；
   B5 = 口径作废待重跑。字面新增阈值已判死（0.65 上限）→ 是否新增并入判定腿（§19.2）。

**校准闭环（2026-09-17 就位）**：`tests/scripts/p2_calibration_apply.py`（零 LLM）——
读三张人工标注表的「是/否」列 → 一致率 → `calibration_gate` 判词（≥0.80 才允许 B1/B2 进结论；
未标注时 passed=None，不假装通过）+ B1「新增」阈值标定（扫 0.03–0.35 取与人工最一致者，
标定值须写入预登记后生效）。人工列空值 = 未标注，**不进一致率**。

**B5 终读改判（§19.11，B5c 结论级盲评）**：报告级 20:0 被定性为内容量对照（结构识别+
篇幅偏好）后，owner 定稿结论级判据（§10：六准则+双防+暗对照）——**风控终稿 19:1**，
暗对照 2/2 tie 无位置偏差，价值集中**风险边界(25票)+可执行性(22票)**；601899 初稿胜
（评审抓住终稿赔率自述 1.7:1 与参数算出 1.24:1 的矛盾）→ **新处置候选：Trader/FM 赔率
计算自检**。四层口径对照 19:1→17:3→20:0→19:1 全留档。校准 5 行待 owner。
（§19.10 报告级读数降为方法学对照保留。）

**B5 报告级读数（§19.10，外科手术对照臂）**：owner 追问后彻底执行共享原则——同 run 产物摘除
风控/FM 后重渲染（零 LLM + 渲染幂等小改），共享层全 byte 一致，判定 **full 20:0**（纯层
增量，provisional 待校准 4 对）。三层读数 19:1 → 17:3 → 20:0 记录噪声随共享程度收敛。
**纪律**：层增量归因一律外科手术式对照（同 run 摘层重渲染），独立采样臂只作旁证。
原冻结重跑方案（≈120 次）已被其取代，不再执行。

**B5 上游冻结重跑（已完成，被外科手术版取代）**：owner 审阅发现两臂分析师报告零字面重叠
（LLM 现跑 + temperature 被端点剔除）→ 19:1 含上游采样噪声，不得纯层归因（§19.7 混淆披露）。
方案：以 full 现有分析师产物为冻结上游，仅重跑 through_trader 下游（≈6 次/标的 × 20），
两臂共享同上游后重评 B5。
**装置教训（owner 追问定性：设计遗漏而非有意取舍）**：消融设计的混杂因子清单只枚举了
数据侧（快照 digest/数据源故障）与程序侧（prompt/rubric/位置），**漏了生成侧**——共享上游
层的 LLM 采样跨臂方差。两条固化纪律：① 今后一切层增量消融**默认冻结上游**（分析师产物
跑一次两臂复用），不再作为可选优化；② 校准三关增补第四关「跨臂对照先验上游一致性」
（程序化比对两臂共享层产物，非零差异即拦截）。

**judge prompt v2 阴性收口（2026-09-18，§19.3）**：双变量同动未过门（0.825/0.750/0.690）→
默认回 v1（0.875/0.906）；单变量探针（`tests/scripts/p2_b1_variant_probe.py`）证明**新增维度
封顶 ~0.75**——失败行全部为人工亦翻案的判断/框架边界行 → 新增率固化人工区间 0.45–0.55，
机器新增仅辅助观测。**无源增量定版扫描 pivot**：改 grounding 口径（`kind=data` 论点对 bear
实际输入的 4 份分析师摘要判可支撑性——比判新增更接近 NLI），已收口：bg-v1 校准 1.000（14/14）→ 无源率 0.125 转正（§19.5）；**处置已落地并验证**——
辩手断言级锚定 + 反例判例（ed2bc75）A/B 实测无源率 **0.125 → 0.012**（8/64 → 1/85，§19.8，
20 次调用）。遗留：Langfuse 在线后 sync+deploy（本地权威源已就绪，运行时回退已验证）。

**采样协议 v2（owner 提议 2026-09-18，下一腿起生效；本轮 B1 仍按已预登记的 20% 采样走完）**：
AI 全量预判（带自报把握高/中/低）→ 人工只审**低+中把握行**，高把握行**随机抽 10% 审计**，
其余高把握行按预判落账（口径记录为「机器判 + 人工分层审计」，不冒充全量人工）。
门控一致率改为分层加权：高把握层用抽查错率外推、低中把握层用实测。
**为什么高把握必须留抽查**：本会话实证——judge 判「被吸收」33/40 为是，被通道分账规则
推翻为系统性假阳性（把分析师通道引用记成辩论功劳，见 refs 扫描 analyst 顶命中 162/196），
且全部是**自信地错**。置信度是 AI 自评，其校准恰恰是待建对象；只审低把握行抓不到系统性偏差。
生效前提：先改 prereg 采样条款与 `metrics.md` §1 口径，再动导出/应用代码。
本轮 B1 预填稿的「把握 vs 人工改动」对照将作为把握度自校准的第一份实证。

**B1 材料表述歧义（2026-09-17，已修）**：论点从辩论上下文里单独摘出会歧义——实测
002415::b1::2「技术性反弹不改变趋势」被读成"不改上涨趋势"（多方立场），原话是空方
「下跌途中的喘息，**不改变下跌趋势方向**」。校准表已补「角色/轮次 + 原话上下文」两列。
同类纪律：材料必须先自证可读再交人工（同 incident 030 一族）。

**B1 跨轮重复计数（待修，未实施）**：同一条风险点在第 2 轮被重述时会**各算一次**
（如 002415 的均线/MACD 论点 r1、r2 各一条）→「新增数」被重复放大。
修法：跨轮按实质去重后再计增量（去重判据仍需阈值，与上一条的标定一并做）。

**B3/B4 样本量出路（owner 裁决 2026-09-18）**：**维持描述性读数，不扩样**——B3 0.952 离任何
决策阈值很远（价值在例外条目），B4 的价值在抓到 000333 具体案例而非比率本身；扩到 ~80 标的
买的是误差棒不是新信息。两腿读数永久带「n=7，仅描述」脚注。

**装置教训（已修，供后续复用）**：判定调用花钱 → 判定结果**即时落盘**并按 `unit_id` 回读缓存
（本轮 160 次调用因脚本崩溃丢失、60 次靠缓存迁移找回）；聚合前按 `unit_id` **去重**
（重复目标使分母虚高 1014 vs 实判 111）。

## 族 A 补测项：A1 / A4 尚无专门单元

P1 各批（round1/2/3、formal、illegal）的 mechanism_id 分布只有 A2/A3/A5/A6/A7——**A1（确定性指标注入）
与 A4（单点修复回路）从未有专门实验单元**。补测设计要点：

- ~~**A1**~~ **已补测（2026-09-17）**：OFF 态剥 `compute_metrics` 输出键（原始三表/kline/新闻/宏观保留），
  10 标的 × 2 态 + 同一把尺子（完整快照 + 生产 citation 链），实耗 102 次调用。**主指标不可测**（OFF 态
  可重算根 claim 319→4，6/10 单元分母为 0 → 触发预登记停止规则②）；归因 = 「LLM 不自算派生指标」
  （登记主张的「自算算错」无样本，未被证伪）。机制价值改读产出面：compute 根 claim 占比 58.6% vs 2.3%、
  claim 总量 -36%、路径不可解析率 0.2% vs 13.4%。读数与边界见 validation §18；前置修复见 incident 030。
  **待 owner 决策**：是否另设「要求分析师自行推导指标」的变体来测 A1 登记主张里的「自算算错」形态
  （当前提示词下分析师直接回落原始报表，不会去心算派生指标）。
- ~~**A4**~~ **已补测（2026-09-17）**：新建正文一致的稀疏 value_mismatch 单元（`evals/causal_ablation/repair_a4.py`
  + `tests/scripts/p1_a4_repair.py`，预登记 `2026-09-17-p1-a4-repair.md`）；实测改对 20/20、终稿留错 0/20，
  但流水线只记账 4/22（incident 029）。读数与边界见 validation §17。

### A4 补测带出的两个待办（2026-09-17）

1. **记账口径**（incident 029）：`value_mismatch_repaired` 只在「该分析师全 PASS」时 +1 → 本批低估
   16/20。处置候选（新增局部口径遥测 / 仲裁范围与记账解耦）**待 owner 决策**，本项不自行改产品行为。
2. **定位覆盖面**：修复回路第一步按 `claim.stated_value` 在正文里找出错句（0.1% 容差）→
   「正文以 亿元 表述、claim 申报 元」这类**值型不一致**的 claim 定位落空，自然腿 2/2 即此形态。
   候选改进：定位优先用 `field_ref`/interpretation 的语义锚而非数值回显。收益面 = 765/1070（71.5%）
   之外的 305 条数值 claim（其中 8 条正文不可定位）。
