# Delta for stock-screening

## MODIFIED Requirements

### Requirement: 无可打分因子降级通道

系统 SHALL 在 L2 打分器拒绝打分的条件成立时（无 active 因子，或全部 active 因子无有效 IC 记录，
或全部 active 因子的 IC 样本不足以估计权重）进入显式降级通道：单因子在近 60 个交易日的 RankIC
记录数少于 **10** 条时 SHALL 不参与打分（权重按 0 处理），并在批次日志披露被排除的因子 id 与样本数；
全部因子均被排除时 L2 SHALL 被显式跳过（MUST NOT 静默等权、MUST NOT 伪造因子权重或 IC、MUST NOT
用 1–3 个样本估计权重）；系统 SHALL 以确定性流动性过渡排序产出 Top-N 候选——近 20 日日均成交额降序、
同额按标的代码升序——并照常落 L2 全量名单（在榜稳定性门槛照常累计）、照常执行 L3 粗筛与精选池落库；
批次 SHALL 标注选择模式 `selection_mode ∈ {factor, fallback}` 并经批次历史接口、池接口与选股页横幅
三处披露，披露文案 SHALL 明示该排序为流动性过渡规则、非因子信号。因子库恢复有可打分因子（含样本数
达标）后，后续批次 SHALL 自动回到 `factor` 路径（降级为死分支，无配置开关）。降级通道 MUST NOT
吞掉其他失败：L1/L3/池等其余任一步骤失败时批次仍 SHALL 记 failed、不产出精选池。

(Previously: 触发条件仅为「无 active 因子，或全部 active 因子无有效 IC 记录」；只要存在任意 IC 记录即视为可打分，3 个样本也会被用于估计权重。)

#### Scenario: 库空降级产出

- **GIVEN** 因子库无 active 因子、当日快照就绪
- **WHEN** 选股日批运行
- **THEN** 批次状态为 ok 且 `selection_mode=fallback`
- **AND** L2 名单与精选池照常产出（候选按近 20 日日均成交额降序，`factor_contribs` 为空）

#### Scenario: IC 样本不足降级

- **GIVEN** 因子库有 1 个 active 因子，但近 60 个交易日只有 3 条 RankIC 记录
- **WHEN** 选股日批运行
- **THEN** 批次状态为 ok 且 `selection_mode=fallback`
- **AND** 日志披露该因子因样本不足被排除（含因子 id 与样本数）
- **AND** L2 名单与精选池照常产出，`factor_contribs` 为空

#### Scenario: 单因子样本不足被排除

- **GIVEN** 因子库有 2 个 active 因子，A 有 60 条 RankIC 记录、B 只有 3 条
- **WHEN** 选股日批运行
- **THEN** 批次 `selection_mode=factor`
- **AND** 综合分只含 A 的贡献分解，B 不出现在 `factor_contribs`

#### Scenario: 三处披露

- **GIVEN** 最近成功批次为降级批次
- **WHEN** 读取池接口与批次历史接口
- **THEN** 两处均含 `selection_mode=fallback`，池接口另含明示「非因子信号」的披露文案
- **AND** 选股页展示降级披露横幅

#### Scenario: 恢复自动切回

- **GIVEN** 因子库已含可打分因子（active 且 IC 样本数 ≥ 10、Σ|IC 均值| > 0）
- **WHEN** 选股日批运行
- **THEN** 批次 `selection_mode=factor` 且 L2 走因子打分路径（Top-N 携带贡献分解）

#### Scenario: 降级不吞失败

- **GIVEN** 因子库无 active 因子，且 L3 粗筛因 LLM 网关整体不可用而失败
- **WHEN** 选股日批运行
- **THEN** 批次状态为 failed、当日精选池零行（fail-closed 语义不因降级通道放宽）
