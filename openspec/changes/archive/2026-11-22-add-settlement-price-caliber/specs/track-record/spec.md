# Delta for track-record

## ADDED Requirements

### Requirement: 结算与盯市行情口径契约

系统 SHALL 通过统一取价口径入口（`data/market_caliber`）获取判定与盯市行情序列，SHALL NOT 由调用方自行指定复权参数：

- **结算口径**（horizon 判定 / 事件 journal 结算段）：`settlement_series` SHALL 返回后复权（hfq）序列，源选择沿用 incident 054 裁决（新浪锁定 fail-closed）；复权参数 SHALL NOT 接受调用方输入。取数失败 SHALL 上抛异常（调用方既有缺失处置路径承接：留痕/跳过/计数），SHALL NOT 出现失败后换口径重试的静默降级。
- **盯市口径**（open 观点盯市近似）：`monitor_series` SHALL 返回 qfq 序列并如实披露口径（盯市读数为近似值，结算读数以 hfq 结算口径为准——展示层既有双口径标注语义不变）。

基准对齐收盘（`benchmark_close_on_or_before`）与中文列日期归一（`normalize_dates`）SHALL 以该模块为唯一实现，SHALL NOT 在消费方另留拷贝。

#### Scenario: 结算取数走 hfq 口径且失败不降级

- **GIVEN** 结算任务对某标的取行情
- **WHEN** `settlement_series` 调用
- **THEN** 复权参数 SHALL 为 hfq（`SETTLEMENT_ADJUST`），SHALL NOT 由调用方传入其他值
- **AND** 取数失败（源不可用/异常）SHALL 上抛，调用方既有处置路径承接，SHALL NOT 以 qfq 序列替代结算

#### Scenario: 盯市取数走 qfq 口径

- **GIVEN** open 观点盯市对某标的取行情
- **WHEN** `monitor_series` 调用
- **THEN** 复权参数 SHALL 为 qfq（盯市近似口径，与结算 hfq 口径分离如实披露）

#### Scenario: helper 单一实现

- **GIVEN** 判定/盯市需要对齐基准收盘或归一日期列
- **WHEN** 调用 `benchmark_close_on_or_before` / `normalize_dates`
- **THEN** 行为与既有实现一致（#57 排序防御随迁：乱序输入按日期取最后收盘）
- **AND** 消费方 SHALL NOT 保留本地拷贝
