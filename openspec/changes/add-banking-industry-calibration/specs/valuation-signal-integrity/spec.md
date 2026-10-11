# add-banking-industry-calibration delta — valuation-signal-integrity

## MODIFIED Requirements

### Requirement: 估值缺数据的诚实文案

估值组件（GARP、相对估值）在估值输入缺失时 SHALL 输出「数据缺失（未参与比较）」类诚实文案与 missing 标注，MUST NOT 将缺失渲染成比较失败。诚实分桶 SHALL 覆盖 GARP 全部四个输入（PE、净利润增长率、ROE、负债率）：任一输入为 None 或 NaN 时，对应项 SHALL 输出「<指标> 数据缺失（未参与比较）」并在 details 标注 `<指标>_missing`，MUST NOT 输出「<指标> <= 阈值」类伪比较文案，NaN MUST NOT 因比较恒 False 被伪装成通过。比较失败与数据缺失 SHALL 在 failures/details 中分桶可辨。诚实文案与 details 中内插的数值 SHALL 以亿元口径保留两位小数格式化，MUST NOT 输出未舍入浮点尾巴进入用户可见交付物。

GARP SHALL 支持第三类分桶「行业不适用」：银行业标的（`is_banking_industry` 判定）的负债率 SHALL 退出 failures（不参与 60% 比较、不记 missing——数据真实存在），details SHALL 标注 `负债率_行业不适用=True` 并保留数值可审计；GARP 通过与否由其余三项输入判定。非银行业负债率比较语义不变。

(Previously: 同文案与分桶要求，但未约束内插数值格式——`_derive_pe_ttm` 缺失原因串原样内插 float 导致「TTM 归母净利润(-13.060000000000002)非正」进入 2026-10-05 南方航空(600029) 报告口径披露节。)

#### Scenario: GARP 无 PE 时报缺失而非伪失败

- GIVEN `quote.PE` 与 `PE_ttm` 均为 None（推导输入缺失）
- WHEN GARP 筛选执行
- THEN failures SHALL 含「PE 数据缺失（未参与比较）」
- AND MUST NOT 输出「PE >= 行业平均」
- AND details 中 PE 相关项 SHALL 标注 missing 而非数值

#### Scenario: 有 PE_ttm 时 GARP 正常比较

- GIVEN `PE_ttm` = 87.8、行业平均 PE = 45
- WHEN GARP 筛选执行
- THEN failures SHALL 含「PE >= 行业平均」（真实比较失败）
- AND details SHALL 记录 PE = 87.8 与口径 `derived_ttm`

#### Scenario: ROE 缺失时报缺失而非伪失败

- GIVEN ROE 为 None（年报序列缺失该年值）
- WHEN GARP 筛选执行
- THEN failures SHALL 含「ROE 数据缺失（未参与比较）」
- AND MUST NOT 输出「ROE <= 15%」
- AND details SHALL 标注 `ROE_missing`

#### Scenario: 负债率 NaN 视同缺失不伪装通过

- GIVEN 负债率输入为 NaN（NaN 参与比较恒 False）
- WHEN GARP 筛选执行
- THEN failures SHALL 含「负债率 数据缺失（未参与比较）」
- AND SHALL NOT 因 NaN 比较恒 False 而让负债率项静默通过
- AND details SHALL 标注 `负债率_missing`

#### Scenario: 净利润增长率缺失报缺失

- GIVEN 净利润增长率为 None
- WHEN GARP 筛选执行
- THEN failures SHALL 含「净利润增长率 数据缺失（未参与比较）」
- AND MUST NOT 输出「净利润增长率 <= 15%」

#### Scenario: 负 TTM 缺失文案数值格式化

- GIVEN 年报归母净利润 8.10 亿、上年同期累计 0.94 亿、最新累计 -20.22 亿（TTM = 8.10 − 0.94 + (-20.22) = -13.060000000000002，2026-10-05 南方航空 600029 同型）
- WHEN `_derive_pe_ttm` 推导执行且 TTM 非正
- THEN 缺失原因 SHALL 含「-13.06」
- AND MUST NOT 含未舍入浮点尾巴（-13.060000000000002）

#### Scenario: 银行业负债率行业不适用

- GIVEN 银行业标的负债率 0.91（91%，通用阈值下恒判「负债率 >= 60%」失败）、行业信息为「银行」
- WHEN GARP 筛选执行
- THEN failures MUST NOT 含负债率项，MUST NOT 输出「负债率 >= 60%」
- AND details SHALL 标注 `负债率_行业不适用=True` 且保留 负债率=0.91 数值可审计
- AND GARP 通过与否 SHALL 仅由 PE/净利润增长率/ROE 三项判定
