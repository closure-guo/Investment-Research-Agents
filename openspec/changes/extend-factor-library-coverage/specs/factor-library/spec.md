# Delta for factor-library（extend-factor-library-coverage）

## MODIFIED Requirements

### Requirement: builtin 因子注册路径

系统 SHALL 内置手工因子基线包七条——既有五条（20 日动量、5 日反转、换手率代理、20 日波动率、市值代理；名称与表达式逐字不变）与**真实口径两条**：流通市值规模 `size_float`（`log(float_cap)`）、20 日平均换手率 `turnover_20`（`ts_mean(turnover,20)`）。真实口径两条 MUST NOT 以代理表达式顶替：面板未携带其引用的可选参考字段时 SHALL 按「可选数据字段的可计算性」在闸①拒收并计入 `reasons`（次日可重试）。全部条目以 `category=builtin` 注册入因子库；builtin 因子免 LLM 提案环节，但 SHALL 同样通过五道闸方可置为 active。注册 SHALL 由日批任务「内置因子注册」（工作日 15:50，快照与因子衰减监控之后、结算链之前）承载，走统一日批通道（调度 / 运行历史 / 手动补跑 / 单飞锁）；库内已存在同表达式条目（任意状态，含 retired/degraded；**`rejected` 拒绝池行除外——被拒 builtin 因子视为缺失、次日可重试**）SHALL 跳过（MUST NOT 复活 retired 条目、MUST NOT 重复入库）；存在缺失条目时 SHALL 以快照库最近面板逐条过闸，面板不可得 SHALL 上抛（任务记 failed，MUST NOT 以空面板放行注册）；摘要 SHALL 记录 `{registered, rejected, skipped, reasons}`（未过闸者逐条原因码）。

(Previously: 基线包为五条，换手率与市值只有价量代理——DSL 无股本数据，以量比代理换手率、以成交额代理市值。)

#### Scenario: builtin 因子受同等准入约束

- **GIVEN** builtin 反转因子在准入评估中 ICIR = 0.12
- **WHEN** 执行准入管线
- **THEN** 该因子被拒收于闸②，与 LLM 提案因子适用同一阈值

#### Scenario: 日批注册入口幂等（含 retired 不回填）

- **GIVEN** 因子库中已存在全部七条 builtin 条目（任意状态，含 retired）
- **WHEN** 内置因子注册日批执行
- **THEN** 七条均被跳过（摘要 `skipped=7 · registered=0 · rejected=0`），MUST NOT 复活 retired 条目、MUST NOT 重复入库
- **AND** 跳过路径 SHALL NOT 构造面板（快照库不可达亦不失败）

#### Scenario: 拒绝池行不构成已存在（被拒 builtin 可重试）

- **GIVEN** 某条 builtin 条目此前过闸失败、库内存在其 `rejected` 行
- **WHEN** 内置因子注册日批执行
- **THEN** 该条目 SHALL 视为缺失并重新过闸（MUST NOT 因 `rejected` 行存在而被跳过）

#### Scenario: 缺失条目过闸注册

- **GIVEN** 因子库存在缺失条目且快照面板可用
- **WHEN** 内置因子注册日批执行
- **THEN** 缺失条目逐条经五道闸：过闸者以 `category=builtin` / `source=builtin` 入库为 active，未过者摘要 `reasons` 记录逐条原因码
- **AND** 已存在条目（任意状态，`rejected` 除外）计入 `skipped`，MUST NOT 重复提交准入

#### Scenario: 面板不可得 fail loud

- **GIVEN** 因子库存在缺失条目且快照库为空 / 历史不足
- **WHEN** 内置因子注册日批执行
- **THEN** 任务上抛（运行历史记 failed），MUST NOT 以空面板放行注册

#### Scenario: 真实口径基线在参考数据缺失时闸①拒收

- **GIVEN** 库内缺 `size_float` 与 `turnover_20`，面板未携带 `float_cap` / `turnover` 帧（参考数据尚未回填）
- **WHEN** 内置因子注册日批执行
- **THEN** 两条真实口径基线各在闸①被拒（原因码 `LOW_COVERAGE`，`detail.missing_fields` 列出所缺字段），摘要 `rejected` 计入这两条、`reasons` 列出原因码
- **AND** 其余已存在条目照常计入 `skipped`，整批不因求值抛错而失败；次日两条仍视为缺失、可重试

#### Scenario: 真实口径基线在参考数据到位后过同一五道闸

- **GIVEN** 面板已携带覆盖准入面板深度的 `float_cap` / `turnover` 帧
- **WHEN** 内置因子注册日批执行
- **THEN** `size_float` 与 `turnover_20` 以 `category=builtin` 进入五道闸，适用与 LLM 提案因子相同的阈值（MUST NOT 因属真实口径而放宽）
- **AND** 过闸者入库为 active，未过者 `reasons` 记录原因码与闸口读数

## ADDED Requirements

### Requirement: 可选数据字段的可计算性

候选表达式引用面板未携带的可选参考字段（或行业分组）时，批量准入编排（`admit_candidates`）SHALL 在求值前识别并以闸①拒收（原因码 `LOW_COVERAGE`，`detail.missing_fields` 列出缺失项），MUST NOT 因求值抛错中断整批；该拒收按拒绝池持久化语义落 `rejected` 行。参考数据到位后，同构候选 SHALL 可被重新提案或经复苏通道按当前面板重走全量五道闸（`rejected` 行不阻断重提的既有语义不变）。面板已携带字段但个别日期 / 标的缺数据时，相关格位 SHALL 为 NaN 并按既有闸①缺失率口径裁决，MUST NOT 前向填充或以 0 冒充。

#### Scenario: 字段未携带时候选在闸①干净拒收

- **GIVEN** 面板未携带 `float_cap` 帧；一批 2 条候选：A 为 `log(float_cap)`，B 为 `ts_mean(close,5)`
- **WHEN** 执行批量准入
- **THEN** A 被拒于闸①（原因码 `LOW_COVERAGE`，`detail.missing_fields == ["float_cap"]`）并落 `rejected` 行
- **AND** B 照常走完后续闸口，整批无异常上抛

#### Scenario: 数据到位后同构候选重走五道闸

- **GIVEN** 候选 A 的 `rejected` 行在库，此后面板已携带 `float_cap` 帧
- **WHEN** 生成侧重新提出与 A 同构的候选，或复苏通道提交 A
- **THEN** 闸0 结构查重放行，该候选按当前面板重走全量五道闸

#### Scenario: 局部缺失不冒充

- **GIVEN** 面板携带 `turnover` 帧，但某标的最近 20 个交易日中有 3 个格位为 NaN
- **WHEN** 求值 `ts_mean(turnover,20)` 并执行闸①
- **THEN** 该标的含缺失格位的窗口产出 NaN（既有时序算子口径），缺失率按闸①「当日有行」分母裁决
- **AND** 系统 MUST NOT 前向填充或以 0 顶替缺失格位

### Requirement: 同构重提的桶归属

候选通过闸①–⑤ 后、入库前，若库内存在与其结构同构的 `retired` 行且该行最近一次 `active → retired` 迁移的原因码为 `BUCKET_QUOTA`，则新入库行的 `category` SHALL 沿用该 `retired` 行的 `category`（桶随结构走，不随提案自报类别走），分桶保种随后按该桶结算；存在多条时取 `factor_id` 最小者。入库事件 `detail` SHALL 记 `inherited_category_from`（被沿用的 retired 行 id）。不存在此类 `retired` 行时，`category` 仍取提案自报值（既有语义不变）。

#### Scenario: 配额退役因子换标签重提沿用原桶

- **GIVEN** 库内 `retired` 行 R（`category="外部"`，最近一次 `active → retired` 迁移原因码为 `BUCKET_QUOTA`）；候选与 R 结构同构且自报 `category="价量"`
- **WHEN** 该候选通过闸①–⑤
- **THEN** 新入库行 `category == "外部"`，ADMITTED 事件 `detail.inherited_category_from == R.id`
- **AND** 分桶保种在「外部」桶内结算（MUST NOT 因自报类别落入「价量」桶而绕过配额）

#### Scenario: 非配额退役或无同构退役行按自报类别

- **GIVEN** 库内与候选同构的 `retired` 行最近一次迁移原因码不是 `BUCKET_QUOTA`，或不存在同构 `retired` 行
- **WHEN** 该候选通过闸①–⑤ 入库
- **THEN** 新入库行 `category` 为提案自报类别，入库事件 `detail` 不含 `inherited_category_from`
