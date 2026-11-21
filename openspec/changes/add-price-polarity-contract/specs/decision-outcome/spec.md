# Delta for decision-outcome

## ADDED Requirements

### Requirement: 三价字段极性契约（sell 分型）

系统 SHALL 为每个 sell 决策的申报三价（entry_price / stop_loss / target_price）声明极性，且校验、落库、展示与执行消费 SHALL 按声明极性解释，SHALL NOT 假定单一惯例：

- `sell_type=exit`（清仓离场）：exit 惯例——stop 为 entry 下方清仓位、target 为 entry 上方反弹减仓带，即 stop < entry < target；
- `sell_type=short`（做空观点）：short 惯例——stop 为 entry 上方失效位、target 为 entry 下方兑现位，即 stop > entry > target。

buy 决策一律 long 惯例（stop < entry < target）。价位 sanity 校验 SHALL 按上述分型极性校验价格关系，SHALL NOT 对 exit 决策套用 short 极性规则或镜像改写其申报价位。极性声明 SHALL 随观点快照冻结写入 `rationale_snapshot.declared_prices`（含 polarity 字段）。前端展示与执行引擎消费三价 SHALL 读取声明极性解释，SHALL NOT 自行猜测惯例。Fund Manager 以「三价字段方向错置作废、按文字口径执行」类理由放行时，系统 SHALL 提供记账口径更正回写机制：按文字口径重设 declared_prices 并标注修正来源，SHALL NOT 把错置值原样入库。存量无极性声明的记录 SHALL NOT 追溯改写（append-only 不变先例），展示层 SHALL 可辨识其为口径未声明。

#### Scenario: exit 决策按 exit 惯例校验

- **GIVEN** `sell_type=exit` 的决策申报 stop=4.90 < entry=5.12 < target=5.3971（南方航空 600029，B 型 exit 叙事）
- **WHEN** 价位 sanity 校验执行
- **THEN** 价格关系 SHALL 判定通过（exit 惯例 stop<entry<target）
- **AND** SHALL NOT 被 short 惯例规则判为违规，SHALL NOT 镜像改写价位

#### Scenario: short 决策按 short 惯例校验

- **GIVEN** `sell_type=short` 的决策申报 stop=12.223 > entry=11.80 > target=10.954（广誉远 600771，A 型）
- **WHEN** 价位 sanity 校验执行
- **THEN** 价格关系 SHALL 判定通过（short 惯例 stop>entry>target）

#### Scenario: 分型与极性错置被拦截

- **GIVEN** `sell_type=exit` 的决策申报 stop 在 entry 上方且 target 在 entry 下方（short 惯例值）
- **WHEN** 价位 sanity 校验执行
- **THEN** 系统 SHALL 判定极性错置并走既有打回/修正回路（重出或按参考带修正）
- **AND** 修正 SHALL 可观测（沿用 price_level_corrected / correction_reason 语义）

#### Scenario: FM 记账口径更正回写

- **GIVEN** Fund Manager 理由申报「三价字段方向错置整体作废、按文字口径执行」并给出文字口径价位
- **WHEN** 观点落库
- **THEN** declared_prices SHALL 按文字口径回写并标注修正来源
- **AND** 原错置值 SHALL NOT 作为展示或消费口径（可留审计痕迹）

#### Scenario: 极性声明随快照冻结

- **GIVEN** 某观点落库
- **WHEN** rationale_snapshot 冻结
- **THEN** declared_prices SHALL 含 polarity 声明（exit / short / long）
- **AND** 快照冻结后 SHALL NOT 被修改（既有冻结语义不变）

#### Scenario: 消费方按声明极性解释

- **GIVEN** 已落库观点带 polarity 声明
- **WHEN** 前端展示或执行引擎读取 stop / target
- **THEN** 解释 SHALL 依据声明极性（exit → 反弹卖出带 / 清仓位；short → 失效位 / 兑现位）

#### Scenario: 存量混用不追溯但可辨识

- **GIVEN** #606 存量混用记录（polarity 声明为 NULL）
- **WHEN** 展示或统计消费
- **THEN** 已落库数据 SHALL NOT 追溯改写
- **AND** 展示 SHALL 标注「口径未声明」（如 stop/target 列标注占位）
