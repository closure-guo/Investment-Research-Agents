## ADDED Requirements

### Requirement: 研究面因子派生契约

系统 SHALL 提供研究面因子派生视图（纯函数，无 LLM 参与）：从 `research_reports.db` 的 `earnings_forecasts`（一致预期快照）、`rating_changes`（评级变动事件）、`analyst_claims`（事件式 claim）派生 per-`(trade_date, code)` 因子值。首批因子族 SHALL 为：

- `eps_rev_1m`：近 30 个自然日一致预期 EPS(Y1) 修正幅度 = `(eps_now − eps_then) / |eps_then|`（同票当前快照 vs 30 日前最近快照；无 30 日前基线、基线 EPS 为 0 → NaN）；
- `rating_chg_1m`：近 30 个自然日净评级变动 = Σ(上调 +1 / 下调 −1 / 首评 0)，评级符号按 research-report-dataplane「评级符号归一」口径；同事件 `event_key` 幂等去重；
- `coverage_chg_1m`：近 30 个自然日覆盖动量 = `(report_count_now − report_count_then) / max(report_count_then, 1)`。

派生 SHALL 以每 `(code, snapshot_date)` 取该日最新快照（期次键去重，复用 research-report-dataplane「期次键与幂等重放」语义）；公告时点字段（`publish_date` / `published_at`）为唯一时序真源。派生结果 MUST NOT 落独立因子值表（监控/准入时点即时派生；缓存为可选实现优化，不构成行为契约）。

#### Scenario: 派生幂等且可复现

- **GIVEN** 研报库固定不变
- **WHEN** 两次执行派生视图
- **THEN** 两次输出逐位一致（同输入同输出，无随机性）

#### Scenario: 基线缺失诚实置 NaN

- **GIVEN** 某票仅有当日一致预期快照、无 30 日前基线
- **WHEN** 派生 `eps_rev_1m`
- **THEN** 该票该日值为 NaN，MUST NOT 以 0 或当期值冒充修正幅度

### Requirement: 生效日语义（防前视）

研究面因子值 SHALL 挂**事件可得日的下一交易日**：事件/快照于 T 日收盘后可得（研报采集日批 ~17:00 落库）→ 因子值挂 T+1 交易日截面，T 日及更早截面为 NaN。该语义与执行链 D+1 口径一致，MUST NOT 把 T 日收盘后可得的信息挂到 T 日或更早截面。

#### Scenario: 收盘后事件不回填当日截面

- **GIVEN** 评级下调事件 `publish_date = T`（T 日 17:00 后入库）
- **WHEN** 派生值矩阵
- **THEN** 该事件影响 `rating_chg_1m` 自 T+1 交易日起的取值，T 日截面该票值为 NaN

### Requirement: 值矩阵契约

派生输出 SHALL 为 date × code 的浮点矩阵，与快照准入面板共享同一升序日期索引与代码列集对齐（面板外代码丢弃）；NaN = 无信号，MUST NOT 以 0 填充。单日截面有值票数 < 20（IC 广度下限）时，该日不产生 IC 样本（当日 IC 记 NaN，不计入 ICIR 样本量）——该下限是值因子横截面统计有效性的最低人群要求，不因因子而异。

#### Scenario: 广度不足日不计 IC

- **GIVEN** 某交易日仅 12 只票有 `eps_rev_1m` 值
- **WHEN** 计算该日横截面 RankIC
- **THEN** 该日 IC 记 NaN 且不计入 ICIR 样本量，MUST NOT 以 12 票截面产出一个高噪声 IC 样本

### Requirement: 研究面因子准入口径

研究面值因子 SHALL 经与价量因子**同一五道闸编排**（`admit_candidates` 单点）裁决：闸②③④⑤逐字不变（消费同一 IC 序列与快照前向收益）；闸①缺失率对**因子宣称覆盖的事件人群**计（有 30 日基线/有事件的票），MUST NOT 对全市场面板计（事件因子天然稀疏，全市场口径属结构性错杀）；闸0 查重键 = `revision:<feature_key>`（值因子无 AST；同 feature_key 存在于库内 candidate/active/degraded → `STRUCTURAL_DUPLICATE`，retired 允许重提）。准入面板沿用 factor-library「准入面板深度下限」同款快照面板与 fail loud 语义；事件值区段短于面板时，无事件区段计 NaN、不构成面板不足。事件历史不足（ICIR 样本量 < 闸②可评估下限）SHALL 以 `insufficient_event_history` 拒收留痕，并 SHALL 支持按状态重放（数据积累后原候选重新过闸，语义同 factor-proposal 收割重放）。来源标注 `source="research"`、`category="研究面"`（独立分桶保种）。

#### Scenario: 稀疏事件因子按事件人群计缺失

- **GIVEN** `rating_chg_1m` 全市场覆盖率约 5%（事件人群内缺失率 8%）
- **WHEN** 闸①评估
- **THEN** 缺失率按事件人群口径 8% < 20% 通过；MUST NOT 因全市场 95% 无事件而拒收

#### Scenario: 事件历史不足拒收留痕可重放

- **GIVEN** `eps_rev_1m` 派生首月仅累积 8 个达标广度的 IC 交易日
- **WHEN** 准入日批执行
- **THEN** 该候选以 `insufficient_event_history` 拒收留痕（记入准入账本，gate=2）
- **AND** 60 个交易日后按状态重放该候选，以届时样本重新过闸

#### Scenario: 值因子与量价因子同闸同权

- **GIVEN** 候选 `eps_rev_1m` 与库内量价因子池做闸④池级增量评估
- **WHEN** 其验证段符号定向后组合 IR 增量 > 0 且与全池 |IC 相关| ≤ 0.7
- **THEN** 闸④通过（值因子与量价因子适用同一增量与冗余判定，无豁免）

### Requirement: 日批接线与调度

系统 SHALL 新增统一日批任务「研究面因子派生与准入」：于当日研报采集日批完成后执行（~17:00 后窗口、当日选股日批与次日衰减监控取数之前），走统一日批通道（调度 / 运行历史 / 手动补跑 / 单飞锁）。任务 SHALL 依次：派生三因子值矩阵 → 逐候选（库内不存在该 feature_key 的 active/degraded 条目时）提交五道闸 → 摘要落 `{derived, submitted, admitted, rejected, insufficient_history}`。任务 SHALL 为零 LLM 依赖（纯派生 + 统计闸口，网关不可用不影响）；研报库可达但三表为空 SHALL 正常完成并摘要零候选；研报库不可达 SHALL fail loud（不静默跳过）。

#### Scenario: 日批幂等

- **GIVEN** 当日「研究面因子派生与准入」已成功执行一次
- **WHEN** 同日重复执行
- **THEN** 已 active 的 feature_key 跳过提交（零重复裁决），摘要 `submitted=0`

#### Scenario: 研报库不可达 fail loud

- **GIVEN** `research_reports.db` 缺失或损坏
- **WHEN** 日批执行
- **THEN** 任务记 failed（含库路径与错误），MUST NOT 以空矩阵放行或伪造摘要

### Requirement: 与分析师事后检验的边界

研究面因子线与 `analyst-ex-post-verification`（分析师记分卡）SHALL 保持读写边界：两者复用同一研报数据源，因子线 SHALL NOT 写 `analyst_claim_outcomes` / `analyst_scorecard_snapshots`，事后检验线 SHALL NOT 写因子库；两线的读数（因子 IC vs 分析师记分卡）互不背书、互不引用为裁决输入。

#### Scenario: 读写边界

- **GIVEN** 研究面因子日批执行
- **WHEN** 任一候选过闸入库
- **THEN** `analyst_claim_outcomes` 与 `analyst_scorecard_snapshots` 零写入；反向：事后检验批次执行时 `factors` / `factor_admission_ledger` 零写入
