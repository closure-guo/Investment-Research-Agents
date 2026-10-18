# Delta: frontend（add-crowding-regime-positioning）

## ADDED Requirements

### Requirement: 选股页市场温度计区块

选股页 SHALL 新增市场温度计区块：状态徽章（`normal` / `elevated` / `danger` 三态，按语义色区分）、总仓位系数、四指标卡（小市值组成交额占比 / 涨跌家数 / 涨停跌停家数 / 全市场成交额，各含当日值与历史分位）、近一年指标曲线（复用既有图表设施）、一句话理由（取判定 `reasons` 拼接展示）。数据缺失（`available=false`）SHALL 展示「温度计未接入」空态并按原因码（`missing` / `corrupt`）区分文案，MUST NOT 显示骨架屏或错误提示冒充；`stale=true` SHALL 明示「数据已陈旧」（含 `as_of`）。区块 MUST NOT 提供手动覆盖系数或开关的控制入口（v1 只读）。

#### Scenario: 三态与系数渲染

- **GIVEN** 端点返回 `state=danger`、`multiplier=0`、四指标与历史序列
- **WHEN** 打开选股页
- **THEN** 状态徽章为 danger 语义色、系数展示为 0
- **AND** 四指标卡与近一年曲线渲染，理由行展示触发原因

#### Scenario: 陈旧披露

- **GIVEN** 端点返回 `stale=true`、`as_of` 为 10 天前
- **WHEN** 打开选股页
- **THEN** 区块明示「数据已陈旧」并展示 `as_of`
- **AND** 指标内容仍可见（标注为陈旧，MUST NOT 隐藏）

#### Scenario: 未接入空态

- **GIVEN** 端点返回 `available=false, reason=missing`
- **WHEN** 打开选股页
- **THEN** 展示「温度计未接入」空态文案，MUST NOT 显示骨架屏或错误提示冒充

#### Scenario: 只读无控制件

- **GIVEN** 温度计区块渲染完成
- **WHEN** 检查区块内交互控件
- **THEN** 无任何可切换状态或覆盖系数的控制入口
