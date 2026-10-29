# Delta for factor-proposal

## ADDED Requirements

### Requirement: 生成侧风格缺口定向回喂

回喂载荷（跨会话记忆桥导出）SHALL 在既有 `contract` 块与拒收清点之外，附**风格缺口定向块**，内容包含两族数据：active 池的风格构成画像（按 `category` 桶的在册因子数）与 per-family 拒收聚合计数（按 `category` 与闸口聚合的被拒结构数）。两族数据 SHALL 数据驱动派生（单一真相源为因子库 `factors` 表与收割状态，MUST NOT 在载荷侧硬编码类别清单或阈值）；任一族数据不可得时 SHALL 整块省略（fail-safe），MUST NOT 以空清单、0 或占位值冒充画像。定向块 SHALL 只作披露（供生成器提案时参考），MUST NOT 携带改变既有任务指令语义的硬性指令。既有语义 SHALL 零回退：admitted 全量在前、按规范化表达式去重、总量 `limit` 上界、`contract` 块、`gate_rejections` 聚合、配额公平性分配照常存在且不受本块影响。

#### Scenario: 正常构造含池画像与族计数

- GIVEN active 池 10 只（量/流动性 8、高低价差 2）且累计 rejected 按类别与闸口可聚合
- WHEN 执行记忆桥导出
- THEN 载荷含风格缺口定向块，池画像与 `factors` 表实际构成逐桶一致
- AND per-family 拒收计数与收割状态聚合一致（可程序核对）

#### Scenario: 画像不可得时整块省略

- GIVEN 因子库为空或池画像/族计数任一不可得
- WHEN 执行记忆桥导出
- THEN 风格缺口定向块整体省略（不输出空清单或占位）
- AND 载荷其余各块照常（admitted / 契约 / 拒收清点 / 配额读数零回退）

#### Scenario: 披露不越权

- GIVEN 任一次成功导出
- WHEN 消费端渲染定向块
- THEN 渲染结果为参考性披露文本
- AND 既有任务指令文本与不含本块时逐字一致（可 diff 核对）
