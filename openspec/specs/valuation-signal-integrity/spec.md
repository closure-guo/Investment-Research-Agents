# valuation-signal-integrity Specification

## Purpose
由 delta update-financial-freshness-and-valuation 归档建立（2026-09-29 实施完毕并通过独立终审）。
## Requirements
### Requirement: PE 缺失时的 TTM 确定性推导

compute 层 SHALL 在 `quote.PE` 缺失且 `market_cap` 可得时，按 TTM 口径确定性推导 `PE_ttm = market_cap / TTM 归母净利润`。TTM 归母净利润 SHALL 由「最新年报归母净利润 − 最新年报内与最新累计报告期同期的归母净利润 + 最新累计报告期归母净利润」拼合；当最新报告期即年报时 TTM 直接取年报值。推导 MUST 为纯规则计算（不经过 LLM）；`market_cap` 或 TTM 任一缺失/非正时 `PE_ttm` SHALL 为 None 并注明缺失原因，MUST NOT 产出 0、无穷或 NaN。推导值 SHALL 标注口径为 `derived_ttm`，与主源静态 PE 可区分。

#### Scenario: 中报已披露时拼合 TTM

- GIVEN `market_cap` = 1910.23 亿、FY2025 归母净利润 9.27 亿、2026H1 归母净利润 13.43 亿、2025H1 归母净利润 0.94 亿
- WHEN compute 执行 PE 推导且 quote.PE 缺失
- THEN TTM 归母净利润 SHALL ≈ 21.76 亿（9.27 − 0.94 + 13.43）
- AND `PE_ttm` SHALL ≈ 87.8，口径标注 `derived_ttm`

#### Scenario: 推导输入缺失时留空

- GIVEN `market_cap` 缺失（行情全回退失败）
- WHEN compute 执行 PE 推导
- THEN `PE_ttm` SHALL 为 None 且 details 注明「market_cap 缺失」
- AND MUST NOT 以 0、无穷或邻股数据填充

#### Scenario: 主源 PE 存在时不覆盖

- GIVEN quote 携带主源静态 `PE`
- WHEN compute 装配估值维度
- THEN SHALL 保留主源 `PE`，`PE_ttm` 可并存但标注口径
- AND 估值比较 SHALL 优先使用与同业口径一致的那个并注明

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

### Requirement: 估值数据注入分析师上下文

基本面分析师上下文 SHALL 包含估值数据段：PE（含口径：主源静态/derived_ttm）、PB、market_cap、相对估值结论（可计算时）。当估值维度整体缺失时，上下文 SHALL 显式标注「估值数据缺失」，MUST NOT 省略该段使分析师默认估值不可知。

#### Scenario: 回退运行下估值段进入上下文

- GIVEN 东财封锁、百度回退提供 `market_cap`=1910.23 亿与 `PB`=15.02，`PE_ttm` 推导为 87.8
- WHEN 基本面分析师构建上下文
- THEN 上下文 SHALL 含 market_cap、PB、PE_ttm（口径 derived_ttm）与相对估值结论
- AND 分析师报告的估值论断 SHALL 可引用这些数值

#### Scenario: 估值维度整体缺失时显式声明

- GIVEN 行情全回退失败、PE 推导输入缺失
- WHEN 基本面分析师构建上下文
- THEN 上下文 SHALL 含「估值数据缺失」标注段
- AND 基本面报告 SHALL 声明估值维度缺失对结论的影响，MUST NOT 在无数值情况下断言贵贱

### Requirement: GARP 判定输入期次对齐

GARP 筛选的输入 SHALL 按指标性质取正确报告期：**负债率为时点指标**，SHALL 优先取 `latest_period_snapshot.资产负债率`（最新披露报告期期末值——中报/季报披露后年报口径即过时）；快照缺失或字段缺失时 SHALL 回落年报口径并在 details 标注回落。**ROE 为全年化指标**（阈值 15% 为年度口径语义），SHALL 取最新年报值，MUST NOT 用半年度/季度累计值直接比较。GARP details SHALL 标注负债率与 ROE 的期次来源（`负债率_期次`/`ROE_期次` 键），使判定依据的报告期可观测、可与正文口径对账。

#### Scenario: 中报披露后负债率取最新期

- **GIVEN** 拓荆科技最新年报 2025 资产负债率 64.11%，`latest_period_snapshot` 含 2026-06-30 中报资产负债率 47.85%
- **WHEN** GARP 筛选执行
- **THEN** 负债率判定 SHALL 用 47.85%（< 60% 阈值，不产生「负债率 >= 60%」failure）
- **AND** details SHALL 标注 `负债率_期次` 为中报来源（含报告日）

#### Scenario: 快照缺失回落年报并标注

- **GIVEN** `latest_period_snapshot` 缺失或不含资产负债率字段，年报资产负债率 64.11%
- **WHEN** GARP 筛选执行
- **THEN** 负债率判定 SHALL 回落年报口径 64.11%（≥ 60%，产生 failure）
- **AND** details 的 `负债率_期次` SHALL 标注为年报口径（回落可见，不伪装成最新期）

#### Scenario: ROE 维持全年口径并标注期次

- **GIVEN** 最新年报 ROE 22.5%，`latest_period_snapshot` 为 2026 中报累计口径
- **WHEN** GARP 筛选执行
- **THEN** ROE 判定 SHALL 用年报 22.5%（MUST NOT 用半年度累计值与 15% 年度阈值比较）
- **AND** details SHALL 标注 `ROE_期次` 为年报年份

