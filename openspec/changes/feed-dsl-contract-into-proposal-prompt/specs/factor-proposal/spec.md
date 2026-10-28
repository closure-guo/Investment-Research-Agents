# Delta for factor-proposal

## ADDED Requirements

### Requirement: 生成 prompt 本体内嵌 DSL 契约

生成侧因子提案 prompt 的**模板本体**（任务指令层，非跨会话记忆块上下文段）SHALL 内嵌 DSL 表达契约段：复杂度上限（AST 深度/自由参数/字段种类）、可表达算子白名单与行情字段白名单、三条表达规则（数值常量必须具体禁 ε / 禁横截面运算 / 禁矩阵回归 ML）。契约段内容 SHALL 由单一真相源派生（`factors.dsl` 复杂度常量与 `factors.qlib_translate` 映射，经记忆桥导出产物的 `contract` 块），MUST NOT 在生成侧以手工字面量形成第二真相源。prompt 契约段与记忆桥载荷 `contract` 块 SHALL 数值一致；DSL 表达力或约束变更时契约段 SHALL 随真相源自动更新。契约源不可用（导出产物缺失/损坏/字段异常）时 prompt 组装 SHALL fail-safe——不阻断生成，降级为仅含记忆桥契约或无契约段，MUST NOT 虚构契约内容。本需求的实施 SHALL 以记忆桥路径（「生成侧 DSL 契约回喂」）的判效读数归档为前置，避免同期双变量。

#### Scenario: prompt 本体含契约段

- GIVEN distro 提案场景装配首轮生成 prompt
- WHEN prompt 组装完成
- THEN prompt 模板本体含契约段（三条规则 + 复杂度上限 + 算子/字段白名单）
- AND 复杂度数值与 `factors.dsl` 常量逐字一致

#### Scenario: 真相源跟随

- GIVEN `factors.dsl` 复杂度常量或 `qlib_translate` 白名单发生变更并重新导出
- WHEN 下一次 prompt 组装
- THEN 契约段数值/白名单随真相源更新，生成侧无手工副本需要同步

#### Scenario: 与记忆桥载荷一致

- GIVEN 记忆桥导出产物（`explored_factors.json`）存在且含 `contract` 块
- WHEN prompt 契约段生成
- THEN 其复杂度数值与白名单与载荷 `contract` 块一致（同一导出派生）

#### Scenario: 契约源缺失 fail-safe

- GIVEN 记忆桥导出产物缺失或 `contract` 块字段异常
- WHEN prompt 组装执行
- THEN 生成流程不受阻断（降级为无模板契约段，记忆桥契约按其既有 fail-safe 语义独立处理）
- AND 不虚构契约数值或白名单
