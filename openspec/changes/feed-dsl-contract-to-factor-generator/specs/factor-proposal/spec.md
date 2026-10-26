# Delta for factor-proposal

## ADDED Requirements

### Requirement: 生成侧 DSL 契约回喂

跨会话因子记忆桥的导出载荷（`explored_factors.json`）SHALL 随已探索清单携带机器可读的 **DSL 表达契约块**（`contract`）：复杂度上限（AST 深度 / 自由参数 / 字段种类，数值取自 `factors.dsl` 复杂度常量单一真相源）、可表达算子白名单与行情字段白名单（取自 `factors.qlib_translate` 算子/字段映射表中**有 DSL 对应**的条目）、表达规则文本——数值常量 MUST 为具体数值（禁用 ε/epsilon 等符号常量）、表达式 MUST NOT 含横截面（当日跨标的）运算、MUST NOT 含矩阵/回归/机器学习流水线。导出载荷 SHALL 另携带闸口拒收计数（gate 编号 → 条数，按已裁决状态聚合）。生成侧消费端（RD-Agent `explored_feedback` 渲染块）SHALL 把契约块渲染进首轮生成 prompt（位于已探索清单之前）；契约块缺失或字段异常时 SHALL 仅渲染已探索清单（fail-safe，MUST NOT 阻断生成、MUST NOT 虚构契约内容）；渲染总量 SHALL 维持字符预算上界并在超限时截断标注。

#### Scenario: 契约块随导出载荷落盘

- GIVEN 收割状态文件（含已裁决候选）与候选文件
- WHEN 执行跨会话记忆桥导出
- THEN 载荷含 `contract` 块：复杂度上限数值与 `factors.dsl` 常量逐字一致、算子白名单与字段白名单均非空且只含有 DSL 对应的条目、规则文本同时覆盖「数值常量必须具体」「禁横截面运算」「禁矩阵/回归/ML」三条
- AND 载荷含按 gate 编号聚合的拒收计数（无拒收时为空聚合而非缺字段）

#### Scenario: 消费端渲染契约进首轮 prompt

- GIVEN 导出载荷含 `contract` 块与已探索清单
- WHEN 生成侧渲染跨会话记忆块
- THEN 渲染产物先呈现契约（规则 + 复杂度上限 + 算子/字段白名单 + 拒收计数）再呈现已探索清单，且总长不超过字符预算

#### Scenario: 契约缺失 fail-safe

- GIVEN 旧版导出载荷（无 `contract` 块，仅有已探索清单）
- WHEN 生成侧渲染跨会话记忆块
- THEN 仅渲染已探索清单，不报错、不虚构契约内容，生成流程不受阻断

#### Scenario: 载荷损坏 fail-safe（既有语义保持）

- GIVEN 反馈文件缺失或 JSON 损坏
- WHEN 生成侧渲染跨会话记忆块
- THEN 渲染返回空串（fail-safe 既有语义逐字保持），生成流程不受阻断
