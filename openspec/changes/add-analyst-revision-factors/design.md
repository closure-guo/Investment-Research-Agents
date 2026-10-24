# Design: add-analyst-revision-factors

## Approach

**预计算值因子通道，而非 DSL 扩展。** 候选因子以「date × code 值矩阵 + feature_key + hypothesis」进入 `admit_candidates`；闸②③④⑤只消费 IC 序列与快照前向收益，天然与「值从哪来」解耦，逐字不动。只有闸①（缺失率）与闸0（查重）需要值因子分支。不扩展 DSL 的理由：DSL = 价量六字段纯函数契约（factor-dsl 规范），研报事件天然稀疏（日覆盖 ~数百票 vs 全市场 5000+），塞进 DSL 会让闸①全市场缺失率 ≥ 90% 全族结构性死亡，且污染六字段契约。

**模块划分**：新增 `src/finance_agent/factors/revision.py`——`derive_revision_matrices(research_db, snapshot_db, as_of) -> dict[feature_key, DataFrame]` 纯函数（无 LLM、无状态、幂等）；`values_to_candidates(matrices)` 把值矩阵包装为 `admit_candidates` 可消费的候选形态（新 kind：`values`，携带 DataFrame 而非 expression）。`validate.py` 的编排契约增加分支：候选为 values 形态时，阶段 A 的 `evaluate` 跳过 DSL 求值直接取矩阵、闸①切事件人群口径、闸0 切查重键；阶段 B/C（闸③④⑤）不改。`monitor.py` 的衰减监控对 `source="research"` 因子走派生视图取值（同口径广度下限），不走 DSL 求值。

**生效日语义**：`publish_date`/`snapshot_date` 为 T（收盘后可得）→ 值写入 T+1 交易日行。实现 = 派生时把事件日映射到「严格大于 T 的下一交易日」（交易日历来自快照面板索引），事件晚于 as_of 前最后一日 → 丢弃。

**三个首批因子**（公式见 spec）：`eps_rev_1m`（一致预期 EPS 修正幅度）、`rating_chg_1m`（净评级变动）、`coverage_chg_1m`（覆盖动量）。选这三个的理由：数据已在流动（`earnings_forecasts` 9472 行 / `rating_changes` 139 行）、公式一行可述、事件式口径（变动非水平——10-19 调研钉死的字段级决定）天然防「高覆盖 ≠ 高预期」混淆。

**时间线现实**：`earnings_forecasts` 自 2026-10-20 起积累，30 日基线意味着 **11 月中下旬才有首批非 NaN 值**，IC 样本量到位更晚。因此设计上首提预期是 `insufficient_event_history` 拒收留痕 + 按状态重放（复用收割通道「按状态重放」先例的语义，实现为日批跳过条件：库内该 feature_key 存在 rejected(reason=insufficient_event_history) 且距上次评估 < 重放间隔（7 个交易日）→ 跳过）。诚实慢，不伪造快车道。

## Alternatives Considered

- **DSL 扩展（加 `eps_rev` 等外部字段）**：拒绝——闸①全市场口径结构性错杀 + 污染六字段契约 + 稀疏 NaN 在 ts_* 算子里语义混乱。
- **独立「事件因子」通道绕开五道闸**：拒绝——破坏「因子库唯一真源 + 同一统计裁判」的治理原则；本 delta 的核心主张就是「换供给来源，不换裁判」。
- **落独立因子值表（日批物化）**：第一版不落——派生是毫秒级纯查询（研报三表 << 快照面板），物化表引入回填/一致性两个新问题；监控时点即时派生。若监控耗时实测不可接受，再加缓存（不构成行为契约）。

## Risks

- **稀疏截面的 ICIR 噪声**：事件人群小 → 单日 IC 方差大。对策：广度下限 20 + 闸②阈值 0.3 不放松 + 首提大概率留痕等数据；若 6 个月后仍零入库，读数是「该信号在 A 股此数据源上不成立」的诚实结论，不是管线失败（同 10-19「因子库为空」先例）。
- **与在飞变更的同文件冲突**：`add-factor-ic-visibility`（因子库只读查询）、`add-factor-monotonicity-gate`（五道闸准入管线）与本 delta 同改 factor-library 的两个 requirement——后归档方合并重写 MODIFIED 块（既有惯例），实施顺序上建议本 delta 在两者之后。
- **数据源单一**：`earnings_forecasts` 单源（东财）；dataplane 已有「第二源交叉与降级」requirement，因子线消费端不新增降级逻辑，跟随数据面覆盖状态。
- **重放风暴**：insufficient_event_history 重放间隔（7 交易日）与单飞锁防重放放大；重放不产生 LLM 调用，成本为纯统计评估。
