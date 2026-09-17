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

## 进行中（2026-09-16 更新）

| Delta | 内容 | 状态 |
|---|---|---|
| revamp-ablation-v2-causal-claims | 因果主张登记 + 注入法 + 单元级判定；GATED 段（驱动薄壳化 / citation 四桶拆报 / 结论注册表 / metrics §1 口径 / 3 标的通路验证）已于 2026-09-16 解阻完成 | **验证通过、待 owner 复核 8 项**，未 archive（见 `tests/validation/2026-09-16-causal-ablation-framework-validation.md`） |

2026-09-16 归档三项（详见 `openspec/changes/archive/2026-09-16-*`）：
- `add-debate-argument-anchors` —— 论点结构化锚点 + 零 LLM 校验 + `argument_anchor_coverage`；验证报告 `tests/validation/2026-09-16-add-debate-argument-anchors-validation.md`
- `ground-comparative-delta-claims` —— 比较型差值计数 + `derived_series`/`price_levels` 通路修复 + 均线差幅；验证报告 `tests/validation/2026-09-15-ground-comparative-delta-claims-validation.md`
- `update-ablation-driver-parity-and-report-status` —— 消融口径正名/驱动接回/报告状态契约
## 评估补强（后续批次，2026-09-15 登记，尚未立项）

> 与 `update-ablation-driver-parity-and-report-status` 互补：该 delta 修「已有结论的可信度管理」，本批修「新结论的产出机制」。

1. **结论注册表 + superseded 状态机**：把报告 `status` 契约（本 delta 已立）扩到 `docs/evals/` 全库（9 份日期报告 + 台账 + 模板），索引页渲染状态徽章 + 状态迁移校验。需先盘清全库每份报告的真实状态。
2. **跑批入口唯一化**：`ablation_pilot.py` 改薄壳——只做续跑/记账，判分与过滤全调库侧。本 delta 只把维度过滤拉回库侧；整体瘦身牵动计量、材料落盘、断点续跑三块。
3. **阳性对照进消融跑批**：每轮消融带一个已知劣化变体（关 `verify_citations`），测不出差异则本轮阴性结果作废——防「评估系统失灵导致的假阴性」被当成架构结论。
4. **v2 消融框架**（已立项：`revamp-ablation-v2-causal-claims`，GATED 段已完成、待 owner 复核）：因果主张登记 + 注入法 + 单元级判定。与幻觉率度量 v1（`evals/hallucination/`，目前只覆盖数值型 claim）互补——注入法能覆盖文本/事件型这一最薄防线。

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

## P2 族 B 进行中（2026-09-17 开工）

**已落地**：预登记转正（门禁字段齐备，MDE 按功效反算锁定：B1/B2/B5 = 44pp、B3/B4 在 n≈5 下无分辨率）；
材料腿与 code 腿实现并单测（`family_b_materials.py` / `family_b_text.py`，
驱动 `tests/scripts/p2_family_b_materials.py`，44 条零 LLM 用例）。

**进行中**：材料批（full 变体 × 20 标的，同快照对照 P1 的 analysts 臂；实测 19 次调用/标的）。
续跑命令（已落材料自动跳过）：

```bash
uv run python tests/scripts/p2_family_b_materials.py   # 续跑 + 出 B3/B4 报告
```

**跑完材料后紧接着要做的三件**：
1. B3/B4 描述性读数（零 LLM，随上面的命令一并产出）——注意 n≈5 无分辨率，只报点估计与明细；
2. B1/B2 判读材料已随材料导出（`judge_material_rows` / `b2_material_rows` + 20% 校准抽样）→
   交人工标注（≥20%），据此建立 B1 NLI / B2 rubric 的校准基线（≥0.80 才允许进结论）；
3. B5 pairwise 盲评（judge K=3，位置随机化 `pairwise_assign`）——同样先过校准。

**已知待解（B1 code 侧的阈值问题）**：B1 的「新增风险点」目前用字符二元组 Jaccard 判，
实测对改写型文本极不稳（600519：阈值 0.35 → 12/12 全「新增」，相似度仅 0.06–0.20；
扫描表显示 0.05→17%、0.20→100%）。**在阈值标定前 B1 的 code 侧不得单独解读**——
出路：① 用人工标注的 20% 样本标定阈值并写入预登记；② 把「是否新增」也改判 NLI（与吸收判定同腿）。

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
