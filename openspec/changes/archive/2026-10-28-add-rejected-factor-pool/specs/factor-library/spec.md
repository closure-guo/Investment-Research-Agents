## ADDED Requirements

### Requirement: 拒绝池持久化

批量准入编排（`admit_candidates`）SHALL 在候选被五道闸拒收（闸①–⑤，含批内二次结构查重）时，将该候选持久化为因子库 `factors` 表 `status='rejected'` 行（单点承担，月批提案 / builtin 注册 / 收割导入三入口自动一致）：行 SHALL 携带表达式、假设、类别、来源与 `ast_json`，并 SHALL 落一条 created 事件，`detail` 携带拒收闸口编号、原因码与判定读数（与准入结论同源）。结构同构的重复拒收（同批或跨批）SHALL 更新既有 `rejected` 行（刷新 `updated_at` 并追加事件），MUST NOT 重复插入。预检失败与未过转译的候选（无 DSL 表达式，即 `untranslatable` / `transcription_error` / `errors` 桶）MUST NOT 落库（维持现状，仍只存在于收割状态文件与反馈 payload）。

#### Scenario: 闸②拒收候选持久化为拒绝池行
- **WHEN** 某候选在准入编排中于闸②（单因子检验）被拒收
- **THEN** 因子库 SHALL 新增一行 `status='rejected'`，携带该候选的表达式、假设、类别、来源与 `ast_json`
- **AND** SHALL 落一条事件，`detail` 含 `rejected_gate=2`、原因码与判定读数（IC 均值 / ICIR / 单调性读数等）

#### Scenario: 结构同构重复拒收按 upsert 合并
- **WHEN** 与既有 `rejected` 行结构同构的候选再次被拒收（同批逐字重复或跨批重提后被拒）
- **THEN** 系统 SHALL 更新既有 `rejected` 行的 `updated_at` 并追加事件，MUST NOT 新增第二行

#### Scenario: 无 DSL 候选不落库
- **WHEN** 候选未过转译（`untranslatable` / `transcription_error`）或预检失败
- **THEN** 因子库 MUST NOT 为其新增任何行（该类候选仍只留存于收割状态文件）

#### Scenario: builtin 注册跳过判定不把拒绝池行视为已存在
- **WHEN** builtin 日批注册遇到库内存在同表达式 `rejected` 行的内置因子
- **THEN** 该因子 SHALL 照常进入五道闸重试（MUST NOT 因 `rejected` 行存在而被跳过）

### Requirement: 拒绝池不参与查重与既有读路径

`rejected` 行 MUST NOT 参与结构层查重范围（查重维持 `candidate/active/degraded`，`retired` 允许重提的既有语义不变）——同构候选 SHALL 可被重新提案并再次走全量五道闸。`rejected` 行 MUST NOT 参与分桶保种配额、池级组合增量评估与选股打分。因子库只读列表 SHALL NOT 返回 `rejected` 行（对外查询行为不变，无前端变更）。库复位（`reset_factor_library`）SHALL 一并清空拒绝池。

#### Scenario: 拒绝池行不阻断重提
- **WHEN** 生成侧重新提出与某 `rejected` 行结构同构的候选
- **THEN** 闸0 结构查重 SHALL 放行（`rejected` 行不在比对对象中），该候选按当前数据窗口重走全量五道闸

#### Scenario: 只读列表不返回拒绝池行
- **WHEN** 调用因子库只读列表接口
- **THEN** 返回结果 SHALL NOT 包含 `status='rejected'` 的行（其余行为不变）

#### Scenario: 拒绝池行不参与桶配额与打分
- **WHEN** 分桶保种按 category 结算容量、或池级组合增量/选股打分读取池因子
- **THEN** 参与者 SHALL 仅限 active 因子，`rejected` 行 MUST NOT 计入

### Requirement: 拒绝池 IC 日批跟踪

衰减监控日批 SHALL 在 active 因子缺口回填之外，额外对全部 `rejected` 行执行同口径的缺口回填式 RankIC 记录（复用 `record_ic` 幂等 upsert：面板窗口内可算且尚缺的交易日，超出窗口的更早缺口不虚构回补）。`rejected` 行 MUST NOT 参与衰减判定（无滚动判决、无降级、无任何状态迁移）。运行摘要 SHALL 新增拒绝池读数（检查数 / 有可算 IC 数 / 本次写入 IC 行数），既有 `recorded` / `days_recorded` / `degraded` 等字段语义不变。面板不可得或 `as_of` 不在面板内 SHALL 与现状一致 fail loud。

#### Scenario: 日批为拒绝池行回填缺口 IC
- **WHEN** 衰减监控日批运行且面板可用，库内存在 `rejected` 行
- **THEN** 系统 SHALL 为每个 `rejected` 行按 active 同口径写入窗口内尚缺交易日的 RankIC（幂等，已存在日期不重写）

#### Scenario: 拒绝池行不触发衰减判定
- **WHEN** 某 `rejected` 行滚动 IC 连续为负且 ICIR 低于阈值
- **THEN** 系统 SHALL NOT 对其执行降级或任何状态迁移（`degraded` 判定仅适用于 active 因子）

#### Scenario: 摘要携带拒绝池读数且既有字段不变
- **WHEN** 衰减监控日批完成
- **THEN** 摘要 SHALL 包含拒绝池的检查数 / 有可算 IC 数 / 本次写入行数
- **AND** 既有 `recorded`（有可算 IC 的 active 因子数）/ `days_recorded` / `degraded` 语义保持不变

### Requirement: 存量拒绝候选回填

系统 SHALL 提供一次性回填脚本：从收割状态文件的已处理映射中，将拒收于五道闸（`status='rejected'` 且带 DSL 表达式与 `ast_json`）的既有候选按**原裁决原样**导入拒绝池（事件 `detail` 携带状态文件中记录的拒收闸口与原因；不重新过闸、不重新裁决）。导入 SHALL 沿用拒绝池 upsert 幂等语义（同构已有行则更新，MUST NOT 重复插入）；`untranslatable` / `transcription_error`（无 DSL）MUST NOT 导入。脚本 SHALL 支持显式指定库与状态文件路径，MUST NOT 依赖生产默认路径隐式生效。

#### Scenario: 回填导入存量闸口拒收候选
- **WHEN** 对含历史 `rejected` 记录（带表达式）的收割状态文件执行回填脚本
- **THEN** 拒绝池 SHALL 新增对应行与事件（原裁决原样：原闸口编号与原因码），摘要报告导入 / 更新 / 跳过条数

#### Scenario: 回填幂等且不导入无表达力候选
- **WHEN** 对同一状态文件重复执行回填，或状态文件含 `untranslatable` / `transcription_error` 记录
- **THEN** 重复执行 SHALL 零新增（同构行命中 upsert 更新路径或跳过），无 DSL 记录 SHALL 全部跳过

## MODIFIED Requirements

### Requirement: builtin 因子注册路径

系统 SHALL 内置手工因子基线包（20 日动量、5 日反转、换手率、20 日波动率、市值代理），以 `category=builtin` 注册入因子库；builtin 因子免 LLM 提案环节，但 SHALL 同样通过五道闸方可置为 active。注册 SHALL 由日批任务「内置因子注册」（工作日 15:50，快照与因子衰减监控之后、结算链之前）承载，走统一日批通道（调度 / 运行历史 / 手动补跑 / 单飞锁）；库内已存在同表达式条目（任意状态，含 retired/degraded；**`rejected` 拒绝池行除外——被拒 builtin 因子视为缺失、次日可重试**）SHALL 跳过（MUST NOT 复活 retired 条目、MUST NOT 重复入库）；存在缺失条目时 SHALL 以快照库最近面板逐条过闸，面板不可得 SHALL 上抛（任务记 failed，MUST NOT 以空面板放行注册）；摘要 SHALL 记录 `{registered, rejected, skipped, reasons}`（未过闸者逐条原因码）。

#### Scenario: builtin 因子受同等准入约束

- **GIVEN** builtin 反转因子在准入评估中 ICIR = 0.12
- **WHEN** 执行准入管线
- **THEN** 该因子被拒收于闸②，与 LLM 提案因子适用同一阈值

#### Scenario: 日批注册入口幂等（含 retired 不回填）

- **GIVEN** 因子库中已存在全部五条 builtin 条目（任意状态，含 retired）
- **WHEN** 内置因子注册日批执行
- **THEN** 五条均被跳过（摘要 `skipped=5 · registered=0 · rejected=0`），MUST NOT 复活 retired 条目、MUST NOT 重复入库
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
