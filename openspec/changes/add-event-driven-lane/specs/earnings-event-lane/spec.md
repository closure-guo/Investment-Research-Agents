# Delta for earnings-event-lane（add-event-driven-lane）

## ADDED Requirements

### Requirement: 业绩预告数据落库

系统 SHALL 从公开数据源抓取 A 股业绩预告记录并落库，字段至少含：标的代码、名称、报告期、预告类型、净利润变动幅度下限与上限、**公告日期**（作为可得时点）、来源标识。写入 SHALL 幂等（同一标的 + 报告期 + 来源记录唯一）。回填与下游判断 MUST 使用公告日期及之前可得的信息（时点截断），MUST NOT 前视。抓取失败 SHALL 重试并如实留痕；MUST NOT 以空数据集或静默跳过冒充成功。

#### Scenario: 幂等 upsert

- **GIVEN** 同一标的同一报告期的预告记录已存在
- **WHEN** 重复抓取写入
- **THEN** 仍为单条记录，字段被更新为最新值

#### Scenario: 时点纪律

- **GIVEN** 一条公告日期为 T 的预告记录
- **WHEN** 构造 T 日之前任何下游判断的输入
- **THEN** 该记录不出现（MUST NOT 前视）

#### Scenario: 抓取失败如实留痕

- **GIVEN** 数据源请求失败
- **WHEN** 抓取流程结束
- **THEN** 该次运行留痕失败原因，且 MUST NOT 以零条新增冒充成功

### Requirement: 日批扫描与 LLM 分诊

系统 SHALL 提供日批扫描通道：对新增且未分诊的预告记录构造分诊输入（预告要素 + 标的快照要素）并调用 LLM 分诊；输出 SHALL 满足合同：`verdict ∈ {outperform, in_line, risk}`、`reason`（≤ 50 字）、`evidence`（引用的输入字段及取值）。单条调用失败 SHALL 降级为 `in_line` 并记 trace warning，不阻断本批其他记录。分诊结果 SHALL 落库并含模型标识与 prompt 版本指纹。分诊 prompt 权威源 SHALL 为 `src/finance_agent/prompts/event_triage.md`，发布 SHALL 走既有 prompt 部署通道（prompt-deploy-consistency 门禁适用）。

#### Scenario: 分诊合同校验

- **GIVEN** 一条预告记录进入分诊
- **WHEN** LLM 返回
- **THEN** 结果含合法 `verdict`、≤ 50 字 `reason` 与证据引用
- **AND** 非法输出被拒收并降级处理

#### Scenario: 单条失败不阻断

- **GIVEN** 一批 20 条记录其中 1 条 LLM 调用抛出 typed error
- **WHEN** 批次执行
- **THEN** 其余 19 条正常产出分诊结果；失败条目按 `in_line` 处理且 trace 含 warning

### Requirement: 事件观察清单与深度分析接线

`outperform` 分诊结论的候选 SHALL 进入事件观察清单（落库 + 只读端点）。清单条目 SHALL 可发起既有深度分析队列（复用 `screening/queue.py` 的并发与补位治理，来源标记 `event`），发起后 SHALL 关联对应 session 标识；MUST NOT 另建独立分析与并发通道。

#### Scenario: 发起深度分析复用既有队列

- **GIVEN** 清单内一条待分析条目
- **WHEN** 发起深度分析
- **THEN** 条目进入既有队列（同一并发上限），并记录关联 session
- **AND** 排队/运行状态可经既有队列快照查询

#### Scenario: 非 outperform 不进清单

- **GIVEN** 一条分诊结论为 `in_line` 或 `risk` 的记录
- **WHEN** 清单生成
- **THEN** 该记录不出现在清单中

### Requirement: 事件独立记账与结算

事件候选 SHALL 独立记账：入场口径 SHALL 与执行链一致（次一交易日开盘价为起算价；停牌或一字板不可成交时顺延至下一可成交日，判定沿用执行链门禁语义），持有期限 SHALL 独立于 T+20（默认 5 个交易日，可配置）；到期由结算通道产出事件族读数（持有期收益）。事件读数 SHALL 注册进 strategy-scorecards 注册表（`family_id=event`），MUST NOT 混入 T+20 观点统计（胜率/均值/超额口径互不污染）。参考价缺失时条目 SHALL 保持未结算并留痕重试，MUST NOT 以 0 或估算值结算。

#### Scenario: 到期结算

- **GIVEN** 一条事件条目起始于 T+1 开盘、horizon 5
- **WHEN** 第 5 个交易日收盘后结算
- **THEN** 条目状态变为已结算并产出持有期收益

#### Scenario: 不可成交顺延

- **GIVEN** 事件次一交易日标的停牌
- **WHEN** 记账入场
- **THEN** 起算日顺延至下一可成交日，且留痕顺延原因

#### Scenario: 家族隔离

- **GIVEN** event 族已有已结算读数
- **WHEN** 读取观点线（decision）统计与成绩单
- **THEN** 两者互不包含对方样本

#### Scenario: 参考价缺失不结算

- **GIVEN** 到期条目参考价取数失败
- **WHEN** 结算运行
- **THEN** 条目保持未结算并留痕，MUST NOT 以 0 或估算值结算

### Requirement: 事件观察只读端点

系统 SHALL 提供事件观察清单只读端点：返回清单条目（预告要素、分诊结论与理由、证据引用、公告日期、清单状态与关联 session）；MUST NOT 写入或修改任何状态。

#### Scenario: 清单返回

- **GIVEN** 清单含 3 条条目
- **WHEN** 请求端点
- **THEN** 返回 200 与 3 条条目全字段
- **AND** 端点零写入
