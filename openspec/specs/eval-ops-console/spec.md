# eval-ops-console Specification

## Purpose
TBD - created by archiving change add-eval-ops-console. Update Purpose after archive.
## Requirements
### Requirement: 日批运行状态与历史可观测

系统 SHALL 提供只读接口返回全部已注册日批任务的运行状态：任务标识、排程（工作日 + 时刻 + 时区）、下次触发时间、最近一次运行（开始/结束时间、结果摘要、错误信息）。每次任务触发 SHALL 落运行历史（含 cohort 开关关闭时的空转、失败与重试、手动补跑、配置变更审计行），使「某天跑没跑」可查而非只能翻日志。调度器未启动（TESTING=1 或显式禁用）时接口 SHALL 显式返回「未运行」状态，SHALL NOT 以 500 或空数据冒充正常。

#### Scenario: 五任务状态齐备

- **WHEN** 请求运行状态接口
- **THEN** SHALL 返回全部已注册任务（当前 9 个：判定 / 盯市 / 指标快照 / 完整性 / cohort / 行情快照 / 因子衰减 / 仿真结算 / 选股）各自的排程、下次触发时间与最近一次运行结果
- **AND** 每项 SHALL 含结果摘要（如 settle 的 settled/superseded/unresolvable 计数）或错误信息

#### Scenario: 调度器未启动显式态

- **GIVEN** TESTING=1 或 DECISION_SETTLE_ENABLED=0
- **WHEN** 请求运行状态接口
- **THEN** SHALL 返回 200 且带「调度器未运行」的显式标记
- **AND** SHALL NOT 返回 500 或以空列表冒充「一切正常」

#### Scenario: 空转与失败均留痕

- **GIVEN** cohort 开关关闭，或某任务执行失败
- **WHEN** 到达触发时刻
- **THEN** 运行历史 SHALL 各记一行（状态分别为 skipped-disabled / failed，含原因）
- **AND** 重试次数 SHALL 随失败行披露

### Requirement: 手动补跑

系统 SHALL 支持对任一日批任务发起手动触发（补跑），用于后端漏跑后的追赶；任务实现 SHALL 保持幂等，重跑不产生重复结算或重复记账。同一任务 SHALL NOT 并发执行两个实例（手动与定时互斥）。cohort 的手动触发 SHALL 要求开关已开启——开关关闭时 SHALL 拒绝并说明原因，维持「开关关 = 零花费」的绝对语义。

#### Scenario: 结算链补跑

- **GIVEN** 某工作日后端未运行导致判定漏跑
- **WHEN** owner 在界面对判定任务点「立即运行」
- **THEN** SHALL 执行与定时触发相同的判定逻辑并落运行历史
- **AND** 已结算观点 SHALL NOT 被重复结算（幂等）

#### Scenario: cohort 关闭时拒绝手动跑批

- **GIVEN** cohort 开关为关
- **WHEN** 请求手动触发 cohort
- **THEN** SHALL 拒绝（4xx）并返回「开关未开启」原因
- **AND** SHALL NOT 产生任何 LLM 调用

#### Scenario: 并发互斥

- **GIVEN** 某任务正在执行（定时或手动）
- **WHEN** 再次请求手动触发同一任务
- **THEN** SHALL 拒绝并返回「已在运行」
- **AND** SHALL NOT 启动第二个实例

### Requirement: cohort 开关与时刻的界面控制

cohort 开关（on/off）与跑批时刻（时/分）SHALL 可在界面修改并**即时生效**（无需重启进程）；配置 SHALL 持久化，进程重启后保持。环境变量 SHALL 仅作启动时的引导默认值，运行期的唯一真相源为持久化运维配置。开启操作 SHALL 在前端要求确认（展示单日成本估算与当前预算上限），后端 SHALL 记审计行（操作者时刻、旧值→新值）。关闭 SHALL 即时生效且后续触发零 LLM 调用。跑批时刻修改 SHALL 即时重排调度，SHALL NOT 影响结算链四任务的时刻。

#### Scenario: 开启需确认并审计

- **WHEN** owner 在界面将开关从关改为开
- **THEN** 前端 SHALL 先展示确认（含 ≈1.7M tokens/日 估算与预算上限）
- **AND** 保存成功后运行历史 SHALL 记一条配置变更审计行（旧值→新值）

#### Scenario: 关闭即时零花费

- **GIVEN** 开关刚被界面关闭
- **WHEN** 到达跑批时刻
- **THEN** SHALL 空转返回且零 LLM 调用，运行历史记 skipped-disabled

#### Scenario: 时刻修改即时重排

- **WHEN** owner 将跑批时刻从 18:00 改为 19:30
- **THEN** 无需重启，下次触发时间 SHALL 立即变为 19:30
- **AND** 结算链四任务时刻 SHALL 不变

#### Scenario: 重启保持

- **GIVEN** 界面已开启开关并改过时刻
- **WHEN** 后端进程重启
- **THEN** 开关与时刻 SHALL 保持界面设定值，而非回退环境变量

### Requirement: 回测批与探针的界面触发

系统 SHALL 支持在界面发起回测批（通路验证 / 正式）与对候选窗口的泄漏探针单跑。正式批 SHALL 复用既有门禁（有效预登记 + 干净窗口 + 探针披露），门禁未过时界面 SHALL 拒绝发起并展示具体原因，SHALL NOT 提供绕过入口。探针单跑 SHALL 展示三态读数（方向命中率 / 未知占比 / 逐窗口明细）与阈值对照。回测报告 SHALL 以注册表形式列出（`evals/backtest/results/*.md`）：生命周期徽章（active / superseded-by）、定位标签（通路验证 / 泄漏污染下的上界证据）、探针读数摘要。

#### Scenario: 无有效预登记时界面拒绝正式批

- **GIVEN** 预登记缺失或字段不齐
- **WHEN** owner 在界面选择「正式批」并发起
- **THEN** SHALL 拒绝并展示门禁失败原因（缺哪些字段）
- **AND** SHALL NOT 启动任何回放

#### Scenario: 通路验证批可触发

- **WHEN** owner 在界面发起通路验证批
- **THEN** SHALL 以 pathway 身份执行（结论恒为通路验证定位）
- **AND** 完成后注册表 SHALL 出现该报告且徽章为 active

#### Scenario: 探针单跑读数展示

- **WHEN** owner 对某候选窗口发起探针单跑
- **THEN** 界面 SHALL 展示方向命中率 / 幅度桶率 / 事件率 / 未知占比与阈值对照
- **AND** 不可测态 SHALL 展示为「不可测」而非 0

#### Scenario: 报告注册表展示状态与定位

- **WHEN** owner 打开回测报告注册表
- **THEN** SHALL 列出全部报告及其 status 徽章与定位标签
- **AND** 被 superseded 的报告 SHALL 展示取代者路径

### Requirement: 健康检查界面运行与展示

系统 SHALL 支持在界面运行 outcome 收口健康检查（结算成功率 / 不可判定率 / integrity / 记账完整率），并逐门禁展示读数与阈值对照；任一门禁不过 SHALL 显式标 FAIL 并给出原因，SHALL NOT 静默通过或以缺省值冒充读数。读数缺失（如无已结算样本）SHALL 展示为「无读数」而非 0 或 100%。

#### Scenario: 门禁不过显式 FAIL

- **GIVEN** 不可判定率超过阈值
- **WHEN** 界面运行健康检查
- **THEN** 该门禁 SHALL 标 FAIL 并展示实测值与阈值
- **AND** 总体结论 SHALL 为不通过

#### Scenario: 读数缺失如实展示

- **GIVEN** 尚无任何已结算观点
- **WHEN** 界面运行健康检查
- **THEN** 相关读数 SHALL 展示「无读数」
- **AND** SHALL NOT 展示 0% 或 100% 冒充

### Requirement: 预登记与口径的受治理编辑

系统 SHALL 提供预登记的界面编辑：七个门禁字段表单化，保存前 SHALL 跑与 CLI 同一套字段校验（缺字段 / 决策阈值缺换算依据 → 拒绝保存）。保存 SHALL 生成**新版本**预登记（不覆盖历史版本）；**已产生读数的预登记 SHALL 锁定不可编辑**（防事后改靶），界面 SHALL 展示锁定原因。系统 SHALL 提供口径（§1.9）的界面查看；数值旋钮（判定窗口 / 中性带 / 探针阈值 / 最小已结算样本）的修改 SHALL NOT 直接改写台账或代码常量，SHALL 生成一份 OpenSpec delta 草稿与 §2 切点行草稿供评审，界面 SHALL 明示「生效须走 delta 流程」。两类编辑 SHALL 均记审计行。

#### Scenario: 预登记缺字段拒绝保存

- **GIVEN** 表单中「MDE」为空
- **WHEN** owner 点保存
- **THEN** SHALL 拒绝并列出缺失字段
- **AND** SHALL NOT 写入任何版本

#### Scenario: 已有读数的预登记锁定

- **GIVEN** 某预登记版本已被正式批读数引用
- **WHEN** owner 尝试编辑该版本
- **THEN** SHALL 拒绝并展示「已有读数，锁定」原因
- **AND** SHALL 引导新建版本

#### Scenario: 口径修改生成 delta 草稿而非直接改台账

- **WHEN** owner 在界面把探针阈值从 0.60 改为 0.55 并提交
- **THEN** 系统 SHALL 在 `docs/evals/caliber-drafts/` 生成 delta 草稿与 §2 切点行草稿
  （草稿未成 change，SHALL NOT 落入 `openspec/changes/`——否则仓库级 `validate --all --strict` 会因骨架不完整而失败）
- **AND** `docs/evals/metrics.md` 与代码常量 SHALL 保持原值不变
- **AND** 界面 SHALL 明示「草稿待评审，生效须走 delta 流程」

#### Scenario: 编辑留审计

- **WHEN** 预登记或口径草稿发生保存
- **THEN** 运行历史 SHALL 记审计行（对象 / 旧值→新值 / 时刻）

### Requirement: 评估运维分区前端

设置中心 SHALL 新增「评估运维」分区，承载上述全部能力；分区 SHALL 沿用既有面板的 loading / ready / error 状态机（加载失败可重试）。烧钱动作（cohort 开启、正式批发起、探针单跑）SHALL 一律带确认交互并展示成本或规模估算。分区 SHALL 在调度器未运行时展示显式提示而非空白。本分区为交互类变更，SHALL 有 E2E 覆盖核心交互（状态渲染 / 开关确认 / 补跑反馈 / 门禁拒绝展示）。

#### Scenario: 分区渲染五任务与开关

- **WHEN** owner 打开设置中心「评估运维」分区
- **THEN** SHALL 展示 5 个任务卡片（排程 / 下次触发 / 最近运行）与 cohort 开关、时刻、花费
- **AND** 调度器未运行时 SHALL 展示显式提示

#### Scenario: 开关确认弹窗

- **WHEN** owner 切换 cohort 开关为开
- **THEN** SHALL 弹出确认（含成本估算与预算上限）
- **AND** 取消 SHALL 不产生任何配置变更

#### Scenario: 门禁拒绝的界面展示

- **GIVEN** 正式批门禁未过
- **WHEN** owner 发起正式批
- **THEN** 界面 SHALL 展示拒绝原因（而非静默失败或转圈）

