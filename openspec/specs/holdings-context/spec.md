# holdings-context Specification

## Purpose
TBD - created by archiving change add-holdings-context. Update Purpose after archive.
## Requirements
### Requirement: 受众持仓声明与上下文解析

分析请求 SHALL 接受可选的受众持仓声明（`holdings`：持有 / 未持有；缺省为未申报）。入口 SHALL 将其解析为 `HoldingsContext` 并写入 `state["holdings_context"]`，三态语义为：`holds=True`（已申报持有）、`holds=False`（已申报未持有）、`holds=None`（未申报）。

`holdings_context` SHALL 在 `AnalysisState` 声明——未声明的键在 LangGraph 合并时被静默丢弃（incident 027 教训）。系统 MUST NOT 把未申报等同于「未持有」：二者对方向可执行性的含义不同（未持有者看空只能观望；未申报者需按可执行默认表达并要求披露），MUST NOT 由系统臆测填充。

本能力预留的是**管道**——模型、状态键、解析、注入与消费契约。持仓数量、成本、建仓日等字段 SHALL NOT 在无消费方时提前引入，待真实账户接入（自动选股 / 交易）时随消费方一并增加。

#### Scenario: 已申报持有进入 state

- **GIVEN** 分析请求携带持仓声明「持有」
- **WHEN** 构建初始 state
- **THEN** `state["holdings_context"]` 的 `holds` SHALL 为 True
- **AND** 该键 SHALL 在 `AnalysisState.__annotations__` 中声明且已建图通道

#### Scenario: 已申报未持有进入 state

- **GIVEN** 分析请求携带持仓声明「未持有」
- **WHEN** 构建初始 state
- **THEN** `state["holdings_context"]` 的 `holds` SHALL 为 False
- **AND** `holds=False` MUST NOT 被上层归一为 None（未申报与未持有是不同状态）

#### Scenario: 未申报不臆测

- **GIVEN** 分析请求未携带持仓声明
- **WHEN** 构建初始 state
- **THEN** `state["holdings_context"]` 的 `holds` SHALL 为 None（未申报）
- **AND** 系统 MUST NOT 取默认值假定受众持有或未持有

### Requirement: 决策层持仓上下文注入

Trader 与 Risk Judge 的 LLM context SHALL 按持仓三态注入受众持仓行，行内携带事实与一句执行纪律（与「派生指标（代码计算）」注入行同款风格）：

- 已申报持有：「受众持仓: 已申报持有该标的——看空结论请按持有者减仓（`sell_type=exit`）表达」
- 已申报未持有：「受众持仓: 已申报未持有该标的——无敞口可退，MUST NOT 输出 `exit`；看空请按观望（`watch`）表达，除非受众明确要求做空研究」
- 未申报：「受众持仓: 未申报——受众是否持有未知；看空结论请按持有者减仓（`exit`）表达，并在理由中说明未持有情形的对应处置」

与 focus 注入的差异：focus 为空时零注入，受众持仓行 MUST 在三态下**均**注入——「未申报」本身是决策所需信息，MUST NOT 以省略该行代替。

#### Scenario: 三态各自注入对应行

- **WHEN** 分别以 `holds=True` / `holds=False` / `holds=None` 构建 Trader 或 Risk Judge 的 context
- **THEN** context SHALL 分别包含上列三行中的对应一行
- **AND** 未申报形态 SHALL 注入「未申报」行，MUST NOT 省略受众持仓行

#### Scenario: 注入行为事实与纪律同源

- **GIVEN** context 已注入受众持仓行
- **WHEN** 该行被执行纪律描述
- **THEN** 行内纪律 SHALL 与 `agent-prompt-contracts`「决策层受众可执行性约束」要求的 prompt 政策一致（context 行与 prompt 政策不得互相矛盾）

### Requirement: 未持有受众的 exit 可执行性门禁

受众已申报未持有（`holds=False`）且终稿 `action="sell"` 与 `sell_type="exit"` 并存时为语义矛盾——`exit` 是「持有者减仓/退出敞口」，受众无敞口可退则不可执行。系统 SHALL 打回重申一次，反馈说明「受众已申报未持有，无敞口可退；看空请改为 `watch`，或受众明确要求做空研究时用 `short`」；重申仍为 exit 时 SHALL 降级为 `watch`（清空 `sell_type`/`exit_schedule`/建仓价位，如实标注「未持有无敞口可退，已降级观望」），判定 SHALL 落 state 键供 trace 观测。

受众未申报（`holds=None`）或已申报持有（`holds=True`）时 MUST NOT 触发本门禁——未申报走可执行默认（`exit`），持有则 `exit` 本就可执行。

#### Scenario: 未持有受众的 exit 打回后改判放行

- **GIVEN** `holds=False`，Risk Judge 首次输出 `action=sell`、`sell_type=exit`
- **WHEN** 可执行性门禁执行
- **THEN** 系统 SHALL 打回重申一次，反馈列明「无敞口可退」与改判方向
- **WHEN** 重申输出为 `watch`
- **THEN** 决策 SHALL 放行进入后续门禁，并如实标注门禁已触发且已改判

#### Scenario: 重申仍为 exit 降级观望

- **GIVEN** `holds=False`，重申输出仍为 `action=sell`、`sell_type=exit`
- **THEN** 终稿 SHALL 为 `action=watch`，`sell_type`/`exit_schedule`/`entry_price`/`stop_loss`/`target_price` SHALL 清空
- **AND** 标注「未持有无敞口可退，已降级观望」SHALL 落 state，MUST NOT 编造 exit_schedule 或建仓价位

#### Scenario: 未申报与已持有不触发

- **GIVEN** `holds=None`（未申报）或 `holds=True`（已申报持有），终稿为 `action=sell`、`sell_type=exit`
- **WHEN** 可执行性门禁复核
- **THEN** 门禁 MUST NOT 生效，决策路径与现状一致（零回归）

