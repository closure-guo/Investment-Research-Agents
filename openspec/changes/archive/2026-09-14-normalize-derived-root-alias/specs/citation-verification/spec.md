## MODIFIED Requirements

### Requirement: 路径形态归一

field_ref 解析 SHALL 对 DataFrame 行键同时尝试原始值与去连字符形式（`2025-12-31` ↔ `20251231`）；根键为 `quarterly_trend` 且路径段匹配 `\d{4}Q[1-4]` 时 SHALL 查 `quarters` 列表换算为位置索引。**根键别名 SHALL 归一：`derived` → `derived_series`**（analysts context 曾提示简写前缀 `derived.`，与 state 根键不一致——归一面同时覆盖普通解析与计算型注册表查找，两种写法等价）。归一 SHALL 双向幂等，SHALL NOT 改变既有负索引与 `[N]` 括号语义。

#### Scenario: 显示格式日期可解析

- **WHEN** claim field_ref 为 `financial_indicators.2025-12-31.加权每股收益`，行键存储为 `20251231`
- **THEN** 解析 SHALL 命中该行

#### Scenario: 季度标签换算位置

- **WHEN** claim field_ref 为 `quarterly_trend.yoy.2026Q2`
- **THEN** 解析 SHALL 经 quarters 列表定位该季度

#### Scenario: 根键别名等价解析

- **WHEN** claim field_ref 为 `derived.chg_5d`（简写根）而 state 根键为 `derived_series`
- **THEN** 解析 SHALL 归一命中该值
- **AND** 计算型 claim 引用 `derived.*` 时 SHALL 同样按 `derived_series` 查重算注册表（不得落 UNVERIFIABLE）
