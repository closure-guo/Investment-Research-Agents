# Delta for evaluation

## ADDED Requirements

### Requirement: Outcome 收益指标口径与预登记

系统 SHALL 在产出任何「赚钱能力」（outcome 收益）读数之前，于 `docs/evals/metrics.md` §1 登记 outcome 收益指标口径，并完成有效预登记。口径登记 SHALL 至少含：主指标（逐决策 T+20 交易日相对沪深300 超额收益的均值与胜率）、胜率定义（沿用 track-record ±2% 中性带 resolved 口径，SHALL NOT 另造定义）、人口划分（主结论仅基于可执行决策 long/short；neutral 以回避正确率作辅助指标，SHALL NOT 进主结论）、辅助观测窗口（T+5/T+10，由每日盯市派生，只观测不判定）、红线（settled 可执行样本 <10 SHALL NOT 报任何胜率类结论）、泄漏控制声明（回测腿须带泄漏探针披露与干净窗口判定）。预登记文档 SHALL 沿用 `evals/ablation/preregister/` 门禁字段格式：主指标 / MDE / 决策阈值 / 样本量依据 / 停止规则 / 成本分型 / 泄漏控制，门禁字段不齐 SHALL NOT 开跑正式批。

#### Scenario: 首个正式批收口时口径与预登记齐备

- **WHEN** 任一 outcome 正式批（forward 或回测）收口
- **THEN** `docs/evals/metrics.md` §1 SHALL 已含 outcome 收益指标小节（主指标 / 人口划分 / 红线 / 泄漏声明齐备）
- **AND** 该批 SHALL 持有效预登记文档且门禁字段齐全

#### Scenario: 样本不足不报胜率

- **GIVEN** 某 outcome 读数的 settled 可执行样本 <10
- **WHEN** 生成读数报告或对外引用
- **THEN** SHALL 只报样本数与「样本积累中」，SHALL NOT 出现任何胜率、平均超额或赚钱能力措辞

#### Scenario: 无预登记批次只能标通路验证

- **GIVEN** 一批回放或跑批未持有效预登记
- **WHEN** 生成批次报告
- **THEN** 报告 SHALL 标注「通路验证」定位
- **AND** SHALL NOT 产出 skill / 赚钱能力结论句

#### Scenario: 回避指标不进主结论

- **WHEN** outcome 读数报告撰写主结论段
- **THEN** 主结论 SHALL 仅引用可执行决策（long/short）的指标
- **AND** 回避正确率 SHALL 仅在辅助指标段出现，且带「不进主结论」标注

### Requirement: Outcome 读数收口纪律

outcome 读数批次的收口 SHALL 依序执行：① 健康检查（结算成功率 / 不可判定率（unresolvable/settleable，与结算成功率互补、同一分母）/ 污染护栏——测试库隔离与 integrity_check 通过 / 记账完整率）；② 异常行（结算失败、行情缺口、口径存疑）逐条人工终裁，未终裁行 SHALL 单列 pending 计数，SHALL NOT 静默剔除或计入；③ 结论 SHALL 为两句式之一——**显著方向**（效应量 + CI）或**分辨率不足**（MDE + 扩样方向），裸「未获统计支持」SHALL NOT 出现；④ 收口报告 SHALL 带生命周期 status 头（`active` 或 `superseded-by: <路径>`，指针须可解析）；⑤ `metrics.md` §2 时间线追加一行 + `runs.jsonl` 追加机器可读摘要。低胜率读数 SHALL 先分桶归因（数据缺口 / 结算缺陷 / watch 语义误读 / 模型真错误）并逐条人工终裁，之后才允许进入任何处置（改 prompt / 判定 agent 缺陷 / 写进报告结论）。

#### Scenario: 收口顺序完整性

- **WHEN** 一个 outcome 批次收口
- **THEN** 收口材料 SHALL 含健康检查记录、人工终裁对照表（落 `tests/validation/`）、两句式结论、status 头、时间线与 runs.jsonl 追加行

#### Scenario: 结论句合法性校验

- **WHEN** 收口报告的结论句缺 CI（显著句式）或缺 MDE（分辨率不足句式）
- **THEN** 结论格式校验 SHALL 失败并指明缺失项
- **AND** 出现裸「未获统计支持」句式 SHALL 判非法

#### Scenario: 低胜率先归因后处置

- **GIVEN** 某批读数胜率显著低于预期
- **WHEN** 拟据此改 prompt、判定 agent 缺陷或写入对外结论
- **THEN** SHALL 先完成分桶归因与逐条人工终裁
- **AND** 处置对象 SHALL 与归因桶匹配（契约病修契约、结算病修结算、真错误才动分析师/Trader）

#### Scenario: 异常行不静默消失

- **WHEN** 批次中存在结算失败或不可判定的决策行
- **THEN** 该行 SHALL 计入 pending/unresolvable 并在报告中披露数量与原因
- **AND** SHALL NOT 从分母静默剔除后当作无异常

### Requirement: Forward 与回测双腿互证

forward（真实管线定时跑批）与回测（历史 as-of 重放）两条 outcome 读数腿 SHALL 使用同一预登记口径：同结算窗口、同基准、同结算入场价口径（决策归属日收盘；收盘后决策/非交易日取次一交易日收盘；两腿同源派生，参考价仅展示与盯市，见 delta `update-decision-settlement-contract`）、同胜率定义。两腿读数 SHALL 并列报告并标注腿别与各自局限（forward：样本积累慢但无泄漏；回测：样本快但带泄漏风险）。两腿读数分歧超出预登记阈值时 SHALL 先归因（泄漏 / regime 漂移 / 执行差异）才可下任何结论；归因完成前结论段 SHALL 标注「待归因」。回测腿报告 SHALL 披露泄漏探针读数与模型知识截止假设。

#### Scenario: 并列报告腿别标注

- **WHEN** 同一评估周期内两腿均有读数
- **THEN** 报告 SHALL 分列 forward 与回测两腿的主指标、样本量与局限声明
- **AND** SHALL NOT 将两腿样本合并计算单一胜率

#### Scenario: 分歧未归因不下结论

- **GIVEN** 两腿主指标读数分歧超出预登记阈值
- **WHEN** 撰写结论
- **THEN** 结论段 SHALL 标注「待归因」并列出候选归因方向
- **AND** SHALL NOT 直接采信任一腿下赚钱/不赚钱结论

#### Scenario: 回测腿强制泄漏披露

- **WHEN** 回测腿批次报告生成
- **THEN** 报告 SHALL 含泄漏探针读数（或探针失败时的未知占比披露）与模型知识截止假设声明
