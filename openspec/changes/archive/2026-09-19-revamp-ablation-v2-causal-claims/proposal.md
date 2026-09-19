# Proposal: revamp-ablation-v2-causal-claims

> 来源设计方案：《消融实验框架 v2.1 设计方案》（2026-09-15），经仓库事实核验后修订为 v2.2。
> 核验结论（v2.1 → v2.2 修订依据）见 design.md §1。

## Why

v1 消融（judge 四维层增量打分）在结构上无法回答「每层值不值」：切题/辩论/依据/一致是约束族指标，任何合格变体的天花板就是目标态，用它们测增量 CI 注定跨 0——n10 权威批次（90 run）已实证全维 CI 含 0，且该批次自身带 #112 伪影与 judge 未校准两处缺陷。管线裁剪决策（辩论层 +7.9% / 决策+风控层 +30.1% token 成本悬而未决）需要换问题提法：对每个被消融对象先登记因果主张——「没有它，哪个可观测失败会变多」——然后只测那个东西。

## What Changes

- **因果主张登记表制度**：每个进入消融矩阵的对象（机制 A1–A7 / 编排层 B1–B6）必须先登记因果主张与失败模式；无主张的对象不得进入矩阵，登记表为消融框架的准入工件。
- **实验族 A（注入法反幻觉消融）**：8 类污染类型矩阵（值错误/方向错误/单位错误/期次错位/镜像叙事/事件编造/时效过期/价位非法）× 机制开/关两态 × 逃逸率指标，作为 `evals/hallucination/` 的 v2 增量落地；自然分布真幻觉≈0（r2 终裁 0/565）使移除法无信号，注入法是唯一可产生效应量的路径。
- **实验族 B（编排层价值消融指标管线）**：B1 风险点增量率（差集代码 + NLI 吸收判定）、B2 交锋修正率（锚点覆盖率 + judge 判真修正）、B3 风控数字出处率（dg 检查代码化）、B4 价位遥测、B5 pairwise 盲评（judge K=3 + 位置随机化 + 20% 人工复核）。
- **统计纪律**：预登记（主指标/MDE/决策阈值/样本量依据/停止规则，落 `evals/ablation/preregister/`）、阳性对照义务（关 verify_citations 的已知劣化变体同行，测不出差异则本轮阴性作废）、结论仅两种合法句式且必带 MDE、标的聚类 bootstrap + 如实报告设计效应。
- **旧四维安置**：report_relevance / consistency 降级为 CI 回归健康监控（门禁族）；debate_quality 从消融退役（由 B1/B2/B5 替代）；decision_grounding judge 版降级为抽查校准用。
- **结论注册表**：evals 报告头部加 `status: active | superseded-by` 状态字段，docs/evals 索引渲染状态——结构性防止过期结论（pilot「显著退步」被简历引用）复发。
- **v2.2 修订（对 v2.1 的更正）**：① A5 因果主张引用的 incident 027 方向更正——该文档记录价位校验回路曾因 state 键未声明**从未生效**（2026-09-13 修复），不能作为「回路真实触发过」的证据，效应量预期改为「待 027 修复后遥测实证」；② judge/NLI 校准义务从 B2/B5 扩展到 B1 的吸收判定与 B3 的 NLI 补方向；③ P1 成本结构显式区分「离线重放型」（校验侧机制 A3/A4，重放已有产物 JSON，零 LLM）与「真跑型」（输入侧联动 + 决策层 A5，全管线 run）；④ 移除法从纸面双轨落为 P2 的组成部分；⑤ B1/B5 决策阈值须附换算依据，不得为裸数字；⑥ 逃逸率终裁的人工成本进入预算。
- **工程前置收口（对账后）**：F1/F2/F4/F5/F6/F7 已由 `update-ablation-driver-parity-and-report-status` 在当前分支实施——本 delta 不重复、只依赖；剩余 F0 后半（驱动侧 judge 判分/落盘薄壳化）、F3（citation 腿换拆报口径）、F8（结论注册表）由本 delta 承接。

**BREAKING**：无（评估框架内部演进，不触碰运行时管线与 API）。

## Capabilities

### New Capabilities

- `causal-ablation`：因果主张消融框架——登记表准入、注入法污染矩阵与逃逸率、单元级判定落库、编排层价值指标（B1–B5）、预登记与阳性对照、结论句式纪律、结论注册表生命周期。

### Modified Capabilities

- `evaluation`：「数据对齐消融实验」Requirement 的指标层置换——judge 四维层增量降级为门禁族健康监控，层增量推断改由因果下游指标（`causal-ablation`）承担；消融结论句式与 MDE 纪律并入该 Requirement。

## Impact

- **代码**：`evals/ablation.py`（指标管线扩展）、`evals/hallucination/measure.py`（注入矩阵作为 v2 增量）、`tests/scripts/ablation_pilot.py`（F0 后半薄壳化：judge 判分/落盘调库侧）、`evals/judge_calibration/`（B1/B5 校准复用）、`evals/backtest/sampling.py`（P2 分层抽样复用）。
- **文档**：`docs/evals/metrics.md` §1 口径先行变更 + 时间线；结论注册表状态字段与索引渲染。
- **依赖与顺序**：本 delta 与 `update-ablation-driver-parity-and-report-status` 修改同一 Requirement（`evaluation::数据对齐消融实验`）——按 project-workflow §6 并行变更规则，本 delta 须在后者收口合并后 rebase 到其合并结果上再 sync。
- **不修改**：`agent-evaluation-suite` 的幻觉率三态判定 Requirement（注入矩阵是其上游污染基础设施，不改三态契约本身）；休眠机制（CITATION_AUTO_RETRY_ENABLED）不在本 delta 唤醒——「休眠回路不进矩阵」纪律见 design.md。
