## ADDED Requirements

### Requirement: 比较型差值申报的显式降级与计数

comparative claim 的 `stated_value` 不属比较方向枚举（`greater_than` / `less_than` / `equal_to`）时——典型为 LLM 把差值或差幅数字（「MA5 较 MA20 低约 2.3%」的 2.3）填入 `stated_value`——校验器 SHALL 判 UNVERIFIABLE，bucket SHALL 为 `comparative_delta_unregistered`，且 SHALL 计入覆盖缺口计数（与「未注册根键的计算型 claim」同一计数通道），SHALL NOT 静默不计。UNVERIFIABLE 拆报 SHALL 将其单列为第三类 `citation_unverifiable_comparative_delta`（与文本类、未注册类并列），实验报告与 trace SHALL 分别输出。

本要求 SHALL NOT 为该路径新增验证语义：SHALL NOT 重算差值、SHALL NOT 判 PASS/FAIL、SHALL NOT 改变三枚举路径与 D3 基期校验的既有行为与执行顺序、SHALL NOT 触发阻断或重试。是否为比较型差值建立公式申报与重算（`formula` + `stated_delta`），SHALL 依据该计数在首轮实验中的占比与逐条人工归因另行决策，SHALL NOT 预先实现。

#### Scenario: 差值数字填入 stated_value 被计数

- **GIVEN** comparative claim `field_ref=technical_indicators.MA.5.-1`，`field_ref_b=technical_indicators.MA.20.-1`，`stated_value=2.3`
- **WHEN** 执行校验
- **THEN** 结果 SHALL 为 UNVERIFIABLE，bucket `comparative_delta_unregistered`
- **AND** 覆盖缺口计数 SHALL +1，SHALL NOT 出现在阻断分母

#### Scenario: 拆报单列

- **WHEN** 一轮实验结束
- **THEN** 报告 SHALL 含 `citation_unverifiable_comparative_delta` 计数，与 `citation_unverifiable_text`、`citation_unverifiable_unregistered` 并列
- **AND** `citation_unverifiable_ratio` 的构成 SHALL 可按三类分解

#### Scenario: 三枚举行为不变

- **WHEN** comparative claim `stated_value="less_than"` 且两端真值 a < b、基期申报合规
- **THEN** 校验 SHALL 判 PASS，与变更前完全一致
- **AND** `stated_value="less_than"` 而 a ≥ b 时 SHALL 判 FAIL、bucket `value_mismatch`，与变更前一致

#### Scenario: 不新增验证语义

- **WHEN** 非枚举 `stated_value` 的 comparative claim 进入校验
- **THEN** 校验器 SHALL NOT 尝试用两端真值重算差值与 `stated_value` 比对
- **AND** 阻断层 SHALL NOT 因该类 claim 置位，路由 SHALL 不受影响
